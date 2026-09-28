from fastapi.testclient import TestClient

from comish.audit import EventLog
from comish.cli import spike_report
from comish.config import Settings
from comish.server import create_app
from tests.fixtures import GROUP_GUID, new_message

TOKEN = "hook-token"


class FakeTransport:
    def __init__(self, fail: bool = False):
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    async def send_text(self, chat_guid: str, text: str) -> None:
        if self.fail:
            raise RuntimeError("AppleScript error -1728")
        self.sent.append((chat_guid, text))


def _client(tmp_path, transport=None, **settings):
    settings = Settings(allowed_chat_guids=frozenset({GROUP_GUID}), log_dir=tmp_path, **settings)
    log = EventLog(tmp_path / "events.jsonl")
    transport = transport or FakeTransport()
    return TestClient(create_app(settings, transport, TOKEN, log)), transport, log


def _post(client, payload, token=TOKEN):
    return client.post(f"/webhooks/bluebubbles?token={token}", json=payload)


def test_rejects_bad_token(tmp_path):
    client, transport, _ = _client(tmp_path)
    assert _post(client, new_message(), token="wrong").status_code == 403
    assert _post(client, new_message(), token="").status_code == 403
    assert transport.sent == []


def test_pong_for_mention(tmp_path):
    client, transport, log = _client(tmp_path)
    resp = _post(client, new_message("@comish ping"))
    assert resp.json() == {"handled": True, "reason": "mention"}
    assert transport.sent == [(GROUP_GUID, "pong")]
    events = [e["event"] for e in log.read()]
    assert events == ["mention_received", "pong_sent"]


def test_no_reply_without_mention_and_no_text_logged(tmp_path):
    client, transport, log = _client(tmp_path)
    _post(client, new_message("private banter about trades"))
    assert transport.sent == []
    (event,) = log.read()
    assert event["reason"] == "no_mention"
    assert "private banter" not in (tmp_path / "events.jsonl").read_text()


def test_duplicate_webhook_replies_once(tmp_path):
    client, transport, _ = _client(tmp_path)
    _post(client, new_message(guid="same"))
    _post(client, new_message(guid="same"))
    assert len(transport.sent) == 1


def test_per_sender_rate_limit(tmp_path):
    client, transport, _ = _client(tmp_path, max_per_sender_per_10min=2)
    for i in range(3):
        _post(client, new_message(guid=f"m{i}"))
    assert len(transport.sent) == 2


def test_outbound_daily_cap(tmp_path):
    client, transport, log = _client(tmp_path, max_outbound_per_day=1)
    _post(client, new_message(guid="a", sender="+15555550101"))
    _post(client, new_message(guid="b", sender="+15555550102"))
    assert len(transport.sent) == 1
    assert "outbound_cap_hit" in [e["event"] for e in log.read()]


def test_send_failure_is_logged_and_reported(tmp_path):
    client, _, log = _client(tmp_path, transport=FakeTransport(fail=True))
    _post(client, new_message(guid="x"))
    report = spike_report(log.read())
    assert report["send_failures"] == 1
    assert report["missed"] == ["x"]


def test_spike_report(tmp_path):
    client, _, log = _client(tmp_path)
    for i, sender in enumerate(["+15555550101", "+15555550102", "+15555550103"]):
        _post(client, new_message(guid=f"m{i}", sender=sender))
    _post(client, new_message("chatter", guid="c1"))
    report = spike_report(log.read())
    assert report["mentions_received"] == 3
    assert report["pongs_sent"] == 3
    assert report["missed"] == []
    assert report["distinct_senders"] == 3
    assert report["ignored"] == {"no_mention": 1}
    assert report["median_latency_s"] is not None


def test_healthz(tmp_path):
    client, _, _ = _client(tmp_path)
    assert client.get("/healthz").json() == {"status": "ok"}
