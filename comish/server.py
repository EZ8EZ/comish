"""Phase 0 spike server: BlueBubbles webhook in, "pong" out.

Configure BlueBubbles (Settings -> API & Webhooks) to POST "New Messages" to
http://127.0.0.1:8787/webhooks/bluebubbles?token=<webhook_token>
"""

import hmac
import time
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from comish import intake
from comish.audit import EventLog
from comish.config import Settings
from comish.ratelimit import RateLimiter
from comish.transport.base import InboundMessage, Transport
from comish.transport.bluebubbles import parse_webhook

PONG = "pong"


def create_app(
    settings: Settings, transport: Transport, webhook_token: str, log: EventLog
) -> FastAPI:
    app = FastAPI(title="comish", docs_url=None, redoc_url=None)
    seen = intake.SeenMessages()
    limiter = RateLimiter(settings.max_per_sender_per_10min, settings.max_outbound_per_day)

    async def reply(msg: InboundMessage, question: str) -> None:
        if not limiter.allow_outbound():
            log.write("outbound_cap_hit", guid=msg.guid, chat=msg.chat_guid)
            return
        try:
            await transport.send_text(msg.chat_guid, PONG)
        except Exception as exc:  # log every send failure; the spike report counts them
            log.write("send_failed", guid=msg.guid, chat=msg.chat_guid, error=str(exc))
            return
        now_ms = int(time.time() * 1000)
        log.write(
            "pong_sent",
            guid=msg.guid,
            chat=msg.chat_guid,
            sender=msg.sender,
            question=question,
            latency_ms=now_ms - msg.sent_at_ms if msg.sent_at_ms else None,
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/bluebubbles")
    async def bluebubbles_webhook(
        request: Request, background: BackgroundTasks, token: str = ""
    ) -> dict[str, Any]:
        if not hmac.compare_digest(token, webhook_token):
            raise HTTPException(status_code=403)
        msg = parse_webhook(await request.json())
        if msg is None:
            return {"handled": False, "reason": "not_new_message"}

        decision = intake.evaluate(msg, settings.allowed_chat_guids, seen)
        if not decision.handle:
            log.write("ignored", guid=msg.guid, chat=msg.chat_guid, reason=decision.reason)
            return {"handled": False, "reason": decision.reason}
        if not limiter.allow_question(msg.sender):
            log.write("rate_limited", guid=msg.guid, chat=msg.chat_guid, sender=msg.sender)
            return {"handled": False, "reason": "rate_limited"}

        log.write("mention_received", guid=msg.guid, chat=msg.chat_guid, sender=msg.sender)
        # Reply after returning 200 so BlueBubbles never times out and re-sends.
        background.add_task(reply, msg, decision.question)
        return {"handled": True, "reason": decision.reason}

    return app
