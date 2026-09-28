"""commish CLI (Phase 0).

    commish bb-ping          check BlueBubbles is reachable and the password works
    commish chats            list chats with GUIDs, to pick COMMISH_ALLOWED_CHAT_GUIDS
    commish serve            run the webhook server (pong spike)
    commish spike-report     summarize logs/events.jsonl against the Phase 0 exit criteria
"""

import argparse
import asyncio
import statistics
import sys
from collections import Counter

from commish.audit import EventLog
from commish.config import Settings
from commish.secrets import get_secret
from commish.transport.bluebubbles import BlueBubblesTransport, is_group_chat


def _transport(settings: Settings) -> BlueBubblesTransport:
    return BlueBubblesTransport(
        settings.bluebubbles_url, get_secret("bluebubbles_password"), settings.send_method
    )


def _event_log(settings: Settings) -> EventLog:
    return EventLog(settings.log_dir / "events.jsonl")


async def _bb_ping(settings: Settings) -> None:
    transport = _transport(settings)
    try:
        print(f"BlueBubbles at {settings.bluebubbles_url}: {await transport.ping()}")
    finally:
        await transport.aclose()


async def _chats(settings: Settings) -> None:
    transport = _transport(settings)
    try:
        chats = await transport.list_chats()
    finally:
        await transport.aclose()
    for chat in chats:
        kind = "GROUP" if is_group_chat(chat) else "1:1  "
        people = ", ".join(p.get("address", "?") for p in chat.get("participants") or [])
        name = chat.get("displayName") or "(unnamed)"
        bound = "*" if chat["guid"] in settings.allowed_chat_guids else " "
        print(f"{bound} {kind} {chat['guid']}\n        {name}: {people}")


def _serve(settings: Settings, host: str, port: int) -> None:
    import uvicorn

    from commish.server import create_app

    if not settings.allowed_chat_guids:
        print("warning: COMMISH_ALLOWED_CHAT_GUIDS is empty; the bot will answer nowhere")
    app = create_app(
        settings, _transport(settings), get_secret("webhook_token"), _event_log(settings)
    )
    uvicorn.run(app, host=host, port=port)


def spike_report(events: list[dict]) -> dict:
    received = {e["guid"]: e for e in events if e["event"] == "mention_received"}
    ponged = {e["guid"]: e for e in events if e["event"] == "pong_sent"}
    latencies = [e["latency_ms"] for e in ponged.values() if e.get("latency_ms") is not None]
    return {
        "mentions_received": len(received),
        "pongs_sent": len(ponged),
        "missed": sorted(set(received) - set(ponged)),
        "send_failures": sum(e["event"] == "send_failed" for e in events),
        "distinct_senders": len({e.get("sender") for e in received.values()}),
        "median_latency_s": round(statistics.median(latencies) / 1000, 2) if latencies else None,
        "max_latency_s": round(max(latencies) / 1000, 2) if latencies else None,
        "ignored": dict(Counter(e["reason"] for e in events if e["event"] == "ignored")),
        "rate_limited": sum(e["event"] == "rate_limited" for e in events),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="commish")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bb-ping")
    sub.add_parser("chats")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    sub.add_parser("spike-report")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.command == "bb-ping":
        asyncio.run(_bb_ping(settings))
    elif args.command == "chats":
        asyncio.run(_chats(settings))
    elif args.command == "serve":
        _serve(settings, args.host, args.port)
    elif args.command == "spike-report":
        report = spike_report(_event_log(settings).read())
        for key, value in report.items():
            print(f"{key:>18}: {value}")
        return 1 if report["missed"] or report["send_failures"] else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
