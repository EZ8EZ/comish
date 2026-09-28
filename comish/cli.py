"""comish CLI.

Messaging (Phase 0):
  comish bb-ping                   check BlueBubbles is reachable and the password works
  comish chats                     list chats with GUIDs, to pick COMISH_ALLOWED_CHAT_GUIDS
  comish serve                     run the webhook server (pong spike)
  comish spike-report              summarize logs/events.jsonl against the Phase 0 exit criteria

Knowledge base (Phase 1):
  comish league add                add a league interactively (writes data/leagues.yaml)
  comish league list               show configured leagues
  comish sync <slug>               pull Sleeper settings and the Drive folder into review
  comish review-status <slug>      what's citable, pending, and failed
  comish admin                     run the review UI on 127.0.0.1:8788
  comish verify-field <slug> <path> [--unverify]
"""

import argparse
import asyncio
import statistics
import sys
from collections import Counter
from typing import TYPE_CHECKING, Any

from comish.audit import EventLog
from comish.config import Settings
from comish.secrets import get_secret
from comish.transport.bluebubbles import BlueBubblesTransport, is_group_chat

if TYPE_CHECKING:
    from comish.kb.store import LeagueStore


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

    from comish.server import create_app

    if not settings.allowed_chat_guids:
        print("warning: COMISH_ALLOWED_CHAT_GUIDS is empty; the bot will answer nowhere")
    app = create_app(
        settings, _transport(settings), get_secret("webhook_token"), _event_log(settings)
    )
    uvicorn.run(app, host=host, port=port)


def spike_report(events: list[dict[str, Any]]) -> dict[str, Any]:
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


def _store(settings: Settings, slug: str) -> "LeagueStore":
    from comish.kb.store import LeagueStore

    return LeagueStore(settings.data_dir, slug)


def _league_cmd(settings: Settings, action: str) -> int:
    from comish.commands import add_league, interactive_league
    from comish.ingest.sleeper import SleeperClient
    from comish.leagues import load_leagues

    if action == "list":
        for league in load_leagues(settings.leagues_path).values():
            drive = league.drive_folder_id or "no Drive folder"
            print(
                f"{league.slug:<12} {league.sport} {league.name} "
                f"(Sleeper {league.sleeper_league_id}; {drive})"
            )
        return 0
    league = interactive_league(input, SleeperClient(), print)
    add_league(settings, league)
    print(f"Added {league.slug}. Next: comish sync {league.slug}")
    return 0


def _sync_cmd(settings: Settings, slug: str) -> int:
    from comish.commands import format_sync_report, get_league, run_sync
    from comish.ingest.drive import GoogleDriveClient
    from comish.ingest.images import make_transcriber
    from comish.ingest.sleeper import SleeperClient
    from comish.llm.gemini import GeminiLLM
    from comish.secrets import optional_secret

    league = get_league(settings, slug)
    key_json = optional_secret("google_service_account_json")
    drive = GoogleDriveClient(key_json) if key_json and league.drive_folder_id else None
    gemini_key = optional_secret("gemini_api_key")
    transcribe = (
        make_transcriber(GeminiLLM(gemini_key, settings.gemini_model)) if gemini_key else None
    )
    store = _store(settings, slug)
    try:
        report = run_sync(store, league, SleeperClient(), drive, transcribe)
    finally:
        store.close()
    print(format_sync_report(report))
    failed = report["drive"].get("totals", {}).get("failed", 0)
    return 1 if failed else 0


def _admin_cmd(settings: Settings) -> None:
    import uvicorn

    from comish.admin.app import create_admin_app
    from comish.leagues import load_leagues

    leagues = load_leagues(settings.leagues_path)
    app = create_admin_app(
        leagues, lambda slug: _store(settings, slug), get_secret("admin_password")
    )
    print(f"Comish admin on http://{settings.admin_host}:{settings.admin_port}")
    uvicorn.run(app, host=settings.admin_host, port=settings.admin_port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="comish", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    league = sub.add_parser("league")
    league.add_argument("action", choices=["add", "list"])
    sync = sub.add_parser("sync")
    sync.add_argument("slug")
    status = sub.add_parser("review-status")
    status.add_argument("slug")
    sub.add_parser("admin")
    verify = sub.add_parser("verify-field")
    verify.add_argument("slug")
    verify.add_argument("path")
    verify.add_argument("--unverify", action="store_true")
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
    elif args.command == "league":
        return _league_cmd(settings, args.action)
    elif args.command == "sync":
        return _sync_cmd(settings, args.slug)
    elif args.command == "review-status":
        from comish.commands import get_league, review_status

        store = _store(settings, args.slug)
        print(review_status(store, get_league(settings, args.slug)))
        store.close()
    elif args.command == "admin":
        _admin_cmd(settings)
    elif args.command == "verify-field":
        from comish.commands import get_league
        from comish.ingest.fields import load_fields

        sport = get_league(settings, args.slug).sport
        if args.path not in {f.path for f in load_fields(sport)}:
            print(f"unknown field {args.path}")
            return 2
        store = _store(settings, args.slug)
        (store.unverify_field if args.unverify else store.verify_field)(args.path)
        store.close()
        print(("Unverified " if args.unverify else "Verified ") + args.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
