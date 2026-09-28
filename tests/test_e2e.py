"""End-to-end: real HTTP servers, real BlueBubblesTransport, fake BlueBubbles.

Runs the comish app under uvicorn, points it at a fake BlueBubbles server that records
sends, then delivers webhooks over real HTTP exactly as BlueBubbles would.
"""

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request

from comish.audit import EventLog
from comish.config import Settings
from comish.server import create_app
from comish.transport.bluebubbles import BlueBubblesTransport
from tests.fixtures import GROUP_GUID, new_message

PASSWORD = "bb-password"
TOKEN = "hook-token"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def serve(app: FastAPI) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def fake_bluebubbles(sent: list[dict[str, Any]]) -> FastAPI:
    app = FastAPI()

    @app.post("/api/v1/message/text")
    async def send_text(request: Request, password: str = "") -> dict[str, Any]:
        if password != PASSWORD:
            return {"status": 401, "message": "Unauthorized"}
        sent.append(await request.json())
        return {"status": 200, "message": "Message sent!", "data": {}}

    return app


def wait_for(predicate: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.02)


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
