"""End-to-end: real HTTP servers, real BlueBubblesTransport, fake BlueBubbles.

Runs the comish app under uvicorn, points it at a fake BlueBubbles server that records
sends, then delivers webhooks over real HTTP exactly as BlueBubbles would.
"""

import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request

from comish.audit import EventLog
from comish.config import Settings
from comish.server import create_app
from comish.transport.bluebubbles import BlueBubblesTransport
from tests.fixtures import GROUP_GUID, new_message
from tests.servers import serve, wait_for

PASSWORD = "bb-password"
TOKEN = "hook-token"


def fake_bluebubbles(sent: list[dict[str, Any]]) -> FastAPI:
    app = FastAPI()

    @app.post("/api/v1/message/text")
    async def send_text(request: Request, password: str = "") -> dict[str, Any]:
        if password != PASSWORD:
            return {"status": 401, "message": "Unauthorized"}
        sent.append(await request.json())
        return {"status": 200, "message": "Message sent!", "data": {}}

    return app


@pytest.fixture
def running(tmp_path: Any) -> Iterator[tuple[str, list[dict[str, Any]], EventLog]]:
    sent: list[dict[str, Any]] = []
    log = EventLog(tmp_path / "events.jsonl")
    with serve(fake_bluebubbles(sent)) as bb_url:
        settings = Settings(
            bluebubbles_url=bb_url,
            allowed_chat_guids=frozenset({GROUP_GUID}),
            log_dir=tmp_path,
        )
        transport = BlueBubblesTransport(bb_url, PASSWORD, settings.send_method)
        with serve(create_app(settings, transport, TOKEN, log)) as app_url:
            yield app_url, sent, log


def test_webhook_to_pong_over_http(running: Any) -> None:
    app_url, sent, log = running
    hook = f"{app_url}/webhooks/bluebubbles?token={TOKEN}"

    resp = httpx.post(hook, json=new_message("@comish ping", guid="e2e-1"))
    assert resp.json()["handled"] is True
    wait_for(lambda: len(sent) == 1)
    assert sent[0]["chatGuid"] == GROUP_GUID
    assert sent[0]["message"] == "pong"
    assert sent[0]["method"] == "apple-script"

    # The bot's own "pong" comes back as a webhook too; it must not trigger another reply.
    echo = new_message("pong", guid="e2e-echo", isFromMe=True)
    assert httpx.post(hook, json=echo).json()["reason"] == "from_me"
    httpx.post(hook, json=new_message("no mention here", guid="e2e-2"))
    time.sleep(0.2)
    assert len(sent) == 1
    wait_for(lambda: any(e["event"] == "pong_sent" for e in log.read()))


def test_bad_token_rejected_over_http(running: Any) -> None:
    app_url, sent, _ = running
    resp = httpx.post(f"{app_url}/webhooks/bluebubbles?token=nope", json=new_message())
    assert resp.status_code == 403
    assert sent == []
