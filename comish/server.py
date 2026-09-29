"""Webhook server: BlueBubbles in, replies out.

Without a Bot it runs the Phase 0 spike: "pong" to @comish in allowed chats. With a
Bot (COMISH_MODE=shadow or live) it answers questions in each league's bound group
chat and handles commissioner DMs. Replies are sent after the webhook returns, so
BlueBubbles never times out and re-delivers.

Configure BlueBubbles (Settings -> API & Webhooks) to POST "New Messages" to
http://127.0.0.1:8787/webhooks/bluebubbles?token=<webhook_token>
"""

import asyncio
import hmac
import time
from typing import TYPE_CHECKING, Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from comish import intake
from comish.audit import EventLog
from comish.config import Settings
from comish.ratelimit import RateLimiter
from comish.transport.base import InboundMessage, Transport
from comish.transport.bluebubbles import parse_webhook

if TYPE_CHECKING:
    from comish.bot import Bot

PONG = "pong"


def create_app(
    settings: Settings,
    transport: Transport,
    webhook_token: str,
    log: EventLog,
    bot: "Bot | None" = None,
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

    async def send_all(guid: str, messages: list[tuple[str, str]]) -> None:
        for chat_guid, text in messages:
            if not limiter.allow_outbound():
                log.write("outbound_cap_hit", guid=guid, chat=chat_guid)
                return
            try:
                await transport.send_text(chat_guid, text)
            except Exception as exc:  # log and continue; one failed DM mustn't block others
                log.write("send_failed", guid=guid, chat=chat_guid, error=str(exc))
                continue
            log.write("sent", guid=guid, chat=chat_guid)

    async def answer(msg: InboundMessage, question: str) -> None:
        assert bot is not None  # noqa: S101 - only scheduled when a bot is configured
        try:
            messages = await asyncio.to_thread(bot.handle_question, msg, question)
        except Exception as exc:  # the pipeline abstains on its own errors; this is a bug
            log.write("answer_crashed", guid=msg.guid, error=f"{type(exc).__name__}: {exc}")
            return
        await send_all(msg.guid, messages)

    async def command(msg: InboundMessage, text: str) -> None:
        assert bot is not None  # noqa: S101
        messages = await asyncio.to_thread(bot.handle_dm, msg, text)
        await send_all(msg.guid, messages)

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

        if bot is not None and not msg.is_group:
            dm = intake.evaluate_dm(msg, bot.desk.is_commissioner, seen)
            log.write("dm", guid=msg.guid, handled=dm.handle, reason=dm.reason)
            if dm.handle:
                background.add_task(command, msg, dm.question)
            return {"handled": dm.handle, "reason": dm.reason}

        allowed = bot.chat_guids if bot is not None else settings.allowed_chat_guids
        decision = intake.evaluate(msg, allowed, seen)
        if not decision.handle:
            log.write("ignored", guid=msg.guid, chat=msg.chat_guid, reason=decision.reason)
            return {"handled": False, "reason": decision.reason}
        if not limiter.allow_question(msg.sender):
            log.write("rate_limited", guid=msg.guid, chat=msg.chat_guid, sender=msg.sender)
            return {"handled": False, "reason": "rate_limited"}

        log.write("mention_received", guid=msg.guid, chat=msg.chat_guid, sender=msg.sender)
        if bot is not None:
            background.add_task(answer, msg, decision.question)
        else:
            background.add_task(reply, msg, decision.question)
        return {"handled": True, "reason": decision.reason}

    return app
