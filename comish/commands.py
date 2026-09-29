"""Phase 1 command logic: adding leagues, syncing, and review status.

Kept separate from argument parsing so each command can be tested with fakes for
Sleeper, Drive and the LLM.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from comish.config import Settings
from comish.ingest.drive import DriveClient
from comish.ingest.images import Transcriber
from comish.ingest.sleeper import SleeperClient, find_user_leagues, sync_sleeper
from comish.ingest.sync import DriveSync
from comish.kb.store import LeagueStore, now_iso
from comish.leagues import (
    League,
    LeagueConfigError,
    load_leagues,
    parse_drive_folder_id,
    save_leagues,
)

if TYPE_CHECKING:
    from comish.answer.pipeline import AnswerPipeline, AnswerResult
    from comish.evals.runner import Answerer

Prompt = Callable[[str], str]


def get_league(settings: Settings, slug: str) -> League:
    leagues = load_leagues(settings.leagues_path)
    if slug not in leagues:
        known = ", ".join(sorted(leagues)) or "none"
        raise LeagueConfigError(f"unknown league {slug!r} (configured: {known})")
    return leagues[slug]


def add_league(settings: Settings, league: League) -> None:
    leagues = load_leagues(settings.leagues_path)
    if league.slug in leagues:
        raise LeagueConfigError(f"league {league.slug!r} already exists")
    leagues[league.slug] = league
    save_leagues(settings.leagues_path, leagues)
    load_leagues(settings.leagues_path)  # re-validate the whole file (e.g. duplicate chats)


def _ask(prompt: Prompt, question: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        answer = prompt(f"{question}{suffix}: ").strip()
        if answer:
            return answer
        if default is not None:
            return default


def interactive_league(
    prompt: Prompt, sleeper: SleeperClient, say: Callable[[str], None]
) -> League:
    """Ask for everything `league add` needs, looking the league up on Sleeper."""
    sport = _ask(prompt, "Sport (nfl or nba)", "nfl").lower()
    who = _ask(prompt, "Sleeper username or league ID")
    if who.isdigit():
        league_id = who
        name_default = str(sleeper.league(league_id).get("name", ""))
    else:
        season = str(sleeper.state(sport).get("season"))
        choices = find_user_leagues(sleeper, who, sport, season)
        if not choices:
            raise LeagueConfigError(f"{who} has no {sport} leagues in {season}")
        for i, choice in enumerate(choices, start=1):
            kind = {0: "redraft", 1: "keeper", 2: "dynasty"}.get(choice.league_type or -1, "?")
            say(f"  {i}. {choice.name} ({kind}, {choice.status}) id {choice.league_id}")
        pick = int(_ask(prompt, "Pick a league number", "1"))
        if not 1 <= pick <= len(choices):
            raise LeagueConfigError("no such league number")
        league_id, name_default = choices[pick - 1].league_id, choices[pick - 1].name
    name = _ask(prompt, "League name", name_default)
    slug = _ask(prompt, "Short slug (a-z, 0-9, hyphens)", "football" if sport == "nfl" else "hoops")
    drive = _ask(prompt, "Drive folder link (or 'none')", "none")
    drive_id = None if drive.lower() == "none" else parse_drive_folder_id(drive)
    chat = _ask(prompt, "iMessage chat GUID from `comish chats` (or 'later')", "later")
    authority = _ask(prompt, "Who records rulings: commissioner or relay", "commissioner")
    commissioner = None
    if authority == "relay":
        commissioner = _ask(prompt, "Actual commissioner's name (for 'relayed by' citations)")
    handles = _ask(prompt, "Your phone/email for flags (comma-separated, or 'later')", "later")
    return League(
        slug=slug,
        name=name,
        sport=sport,  # type: ignore[arg-type]  # validated in League.__post_init__
        sleeper_league_id=league_id,
        drive_folder_id=drive_id,
        chat_guid=None if chat == "later" else chat,
        ruling_authority=authority,  # type: ignore[arg-type]
        commissioner_name=commissioner,
        flag_handles=() if handles == "later" else tuple(h.strip() for h in handles.split(",")),
    )


def run_sync(
    store: LeagueStore,
    league: League,
    sleeper: SleeperClient,
    drive: DriveClient | None,
    transcribe: Transcriber | None,
) -> dict[str, Any]:
    started = now_iso()
    report: dict[str, Any] = {"league": league.slug}
    report["sleeper"] = sync_sleeper(
        store, sleeper, league.sleeper_league_id, league.sport
    ).as_dict()
    if league.drive_folder_id is None:
        report["drive"] = {"skipped": "no Drive folder configured"}
    elif drive is None:
        report["drive"] = {"skipped": "no Drive credentials (google_service_account_json)"}
    else:
        report["drive"] = DriveSync(store, drive, transcribe).run(league.drive_folder_id).as_dict()
    store.record_sync_run(started, report)
    return report


def format_sync_report(report: dict[str, Any]) -> str:
    lines = [f"Sync: {report['league']}"]
    s = report["sleeper"]
    lines.append(
        f"  Sleeper: seasons {', '.join(s['seasons'])}; snapshots changed "
        f"{s['snapshots_changed'] or 'none'}; records +{s['records_added']} "
        f"-{s['records_archived']} ={s['records_unchanged']}"
    )
    drive = report["drive"]
    if "skipped" in drive:
        lines.append(f"  Drive: skipped ({drive['skipped']})")
    else:
        totals = ", ".join(f"{k} {v}" for k, v in drive["totals"].items() if v)
        lines.append(f"  Drive: {totals or 'no files'}")
        for f in drive["files"]:
            detail = f" ({f['detail']})" if f["detail"] else ""
            lines.append(f"    {f['status']:<9} {f['path']}{detail}")
        for path in drive["archived"]:
            lines.append(f"    archived  {path} (removed from Drive)")
    return "\n".join(lines)


def review_status(store: LeagueStore, league: League) -> str:
    counts = store.review_counts()
    last = store.last_sync()
    verified = len(store.verified_fields())
    lines = [
        f"{league.name} ({league.slug})",
        f"  citable records:  {counts['citable_records']}",
        f"  pending review:   {counts['pending_sources']} sources, "
        f"{counts['pending_records']} records",
        f"  failed sources:   {counts['failed_sources']}",
        f"  verified fields:  {verified}",
        f"  last sync:        {last['finished_at'] if last else 'never'}",
    ]
    return "\n".join(lines)


def build_pipeline(settings: Settings, league: League, store: LeagueStore) -> "AnswerPipeline":
    from comish.answer.pipeline import AnswerPipeline
    from comish.llm.gemini import GeminiLLM
    from comish.secrets import get_secret

    key = get_secret("gemini_api_key")
    return AnswerPipeline(
        store,
        league,
        GeminiLLM(key, settings.gemini_model),
        GeminiLLM(key, settings.gemini_verifier_model),
    )


def pipeline_answerer(pipeline: "AnswerPipeline") -> "Answerer":
    """Adapt the pipeline to the eval runner's Answerer interface."""
    from comish.evals.grader import Citation, Outcome

    def answer(question: str) -> Outcome:
        result = pipeline.answer(question, asked_by="eval")
        return Outcome(
            decision="answered" if result.decision == "answered" else "abstained",
            reply=result.reply,
            citations=tuple(Citation(c.label, c.source, c.quote) for c in result.citations),
            abstain_reason=result.reason,
        )

    return answer


def format_answer(result: "AnswerResult") -> str:
    lines = [result.reply, "", f"decision: {result.decision}"]
    if result.reason:
        lines.append(f"reason:   {result.reason}")
    for c in result.citations:
        lines.append(f"cited:    {c.label} ({c.source})")
    return "\n".join(lines)
