"""Fetch completed seasons from Sleeper and store their history facts for review.

Each season becomes one source, `history:<season>`, that the commissioner approves in
the admin UI like any other. Re-syncing an unchanged season changes nothing; a changed
fact goes back to review. A season that fails its checks is reported and left as it is.
"""

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from comish.ingest.fields import load_fields
from comish.ingest.history import DraftData, SeasonData, build_history, regular_weeks
from comish.ingest.sleeper import SleeperClient
from comish.kb.store import LeagueStore

MAX_WEEKS = 22  # a guard against a malformed playoff_week_start
PLAYERS_MAX_AGE = dt.timedelta(days=1)
PLAYER_KEYS = ("first_name", "last_name", "position", "team")


@dataclass
class HistorySyncReport:
    written: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    withheld: dict[str, list[str]] = field(default_factory=dict)
    records_added: int = 0
    records_archived: int = 0
    records_unchanged: int = 0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def load_players(
    client: SleeperClient,
    sport: str,
    cache_dir: Path,
    now: dt.datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Player names, cached per league for a day. Only the fields history needs are kept."""
    now = now or dt.datetime.now(dt.UTC)
    path = cache_dir / f"players-{sport}.json"
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if now - dt.datetime.fromisoformat(cached["fetched_at"]) < PLAYERS_MAX_AGE:
            return dict(cached["players"])
    players = {
        pid: {k: info.get(k) for k in PLAYER_KEYS}
        for pid, info in client.players(sport).items()
        if isinstance(info, dict)
    }
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"fetched_at": now.isoformat(), "players": players}), encoding="utf-8"
    )
    return players


def fetch_season(
    client: SleeperClient, league: dict[str, Any], players: dict[str, dict[str, Any]]
) -> SeasonData:
    league_id = str(league["league_id"])
    settings = league.get("settings", {})
    last = min(int(settings.get("last_scored_leg") or settings.get("leg") or 0), MAX_WEEKS)
    data = SeasonData(
        league=league,
        rosters=client.rosters(league_id),
        users=client.users(league_id),
        winners_bracket=client.bracket(league_id, "winners"),
        losers_bracket=client.bracket(league_id, "losers"),
        players=players,
    )
    for week in range(min(regular_weeks(league).start, 1) or 1, last + 1):
        data.matchups[week] = client.matchups(league_id, week)
        data.transactions[week] = client.transactions(league_id, week)
    for draft in client.drafts(league_id):
        if draft.get("status") == "complete":
            data.drafts.append(DraftData(draft, client.draft_picks(str(draft["draft_id"]))))
    return data


def sync_history(
    store: LeagueStore,
    client: SleeperClient,
    sport: str,
    cache_dir: Path,
    players: dict[str, dict[str, Any]] | None = None,
) -> HistorySyncReport:
    report = HistorySyncReport()
    fields = load_fields(sport)
    verified = store.verified_fields()
    snapshots = sorted(store.snapshots(), key=lambda s: str(s["season"]))
    complete = [s for s in snapshots if s["league"].get("status") == "complete"]
    for s in snapshots:
        if s not in complete:
            report.skipped[str(s["season"])] = f"season is {s['league'].get('status')}"
    if not complete:
        return report
    if players is None:
        players = load_players(client, sport, cache_dir)
    previous: dict[str, Any] | None = None
    for snap in snapshots:
        league = snap["league"]
        if snap not in complete:
            previous = league
            continue
        season = str(league["season"])
        result = build_history(fetch_season(client, league, players), previous, fields, verified)
        previous = league
        if result.skipped:
            report.skipped[season] = result.skipped
            continue
        source, _ = store.upsert_source(
            kind="history",
            external_id=f"history:{season}",
            name=f"League history, {season} season",
            path=f"history/{season}",
            mime_type="application/json",
        )
        store.set_source_basis(source.id, None, "sleeper-season")
        changes = store.replace_records(source.id, result.records)
        report.written.append(season)
        if result.notes:
            report.withheld[season] = result.notes
        report.records_added += changes.added
        report.records_archived += changes.archived
        report.records_unchanged += changes.unchanged
    return report
