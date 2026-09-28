"""Sleeper API client and settings ingestion.

The API is public, read-only and unauthenticated (https://docs.sleeper.com). Sleeper
asks clients to stay under 1000 calls a minute; this client spaces calls out and
never fetches the ~15 MB /players payload.

Dynasty leagues get a new league ID every season, linked through previous_league_id.
sync_sleeper walks that chain, stores one snapshot per season, and turns each
dictionary field into a setting record. Those records become citable only once the
commissioner verifies the field (see LeagueStore.refresh_sleeper_record_status).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from comish.ingest.fields import Field, load_fields
from comish.kb.store import LeagueStore, NewRecord

BASE_URL = "https://api.sleeper.app/v1"
MAX_CHAIN = 40  # seasons; a guard against a malformed or cyclic chain


class SleeperError(RuntimeError):
    pass


class SleeperClient:
    def __init__(
        self,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
        min_interval_s: float = 0.1,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._client = client or httpx.Client(timeout=20)
        self._base = base_url.rstrip("/")
        self._min_interval = min_interval_s
        self._sleep = sleep
        self._last = 0.0

    def _get(self, path: str) -> Any:
        wait = self._min_interval - (time.monotonic() - self._last)
        if wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()
        try:
            resp = self._client.get(f"{self._base}/{path}")
        except httpx.HTTPError as exc:
            raise SleeperError(f"GET {path}: {exc}") from exc
        if resp.status_code != 200:
            raise SleeperError(f"GET {path} -> {resp.status_code}")
        return resp.json()

    def user(self, username: str) -> dict[str, Any]:
        data = self._get(f"user/{username.lstrip('@')}")
        if not data:
            raise SleeperError(f"no Sleeper user {username!r}")
        return dict(data)

    def user_leagues(self, user_id: str, sport: str, season: str) -> list[dict[str, Any]]:
        return list(self._get(f"user/{user_id}/leagues/{sport}/{season}") or [])

    def league(self, league_id: str) -> dict[str, Any]:
        data = self._get(f"league/{league_id}")
        if not data:
            raise SleeperError(f"no Sleeper league {league_id}")
        return dict(data)

    def state(self, sport: str) -> dict[str, Any]:
        return dict(self._get(f"state/{sport}"))

    def chain(self, league_id: str) -> list[dict[str, Any]]:
        """The league and every earlier season, newest first."""
        leagues: list[dict[str, Any]] = []
        seen: set[str] = set()
        current: str | None = league_id
        while current and current != "0":
            if current in seen or len(leagues) >= MAX_CHAIN:
                raise SleeperError(f"previous_league_id chain loops or is too long at {current}")
            seen.add(current)
            league = self.league(current)
            leagues.append(league)
            current = league.get("previous_league_id")
        return leagues


@dataclass(frozen=True)
class LeagueChoice:
    league_id: str
    name: str
    season: str
    sport: str
    status: str
    league_type: int | None


def find_user_leagues(
    client: SleeperClient, username: str, sport: str, season: str
) -> list[LeagueChoice]:
    user = client.user(username)
    return [
        LeagueChoice(
            league_id=str(lg["league_id"]),
            name=str(lg.get("name", "")),
            season=str(lg.get("season", season)),
            sport=sport,
            status=str(lg.get("status", "")),
            league_type=(lg.get("settings") or {}).get("type"),
        )
        for lg in client.user_leagues(str(user["user_id"]), sport, season)
    ]


def setting_records(league: dict[str, Any], fields: list[Field]) -> list[NewRecord]:
    season = str(league["season"])
    records = []
    for f in fields:
        raw = f.lookup(league)
        if raw is None:
            continue
        rendered = f.render(raw)
        raw_text = ",".join(raw) if isinstance(raw, list) else raw
        text = (
            f"Sleeper league settings, {season} season: {f.label} = {rendered} "
            f"[{f.path} = {raw_text}]"
        )
        records.append(
            NewRecord(
                record_type="sleeper_setting",
                section_path=f"{season} > {f.path}",
                text=text,
                season=season,
                meta={
                    "path": f.path,
                    "raw": raw,
                    "league_id": str(league["league_id"]),
                    "app_enforced": f.app_enforced,
                },
            )
        )
    return records


@dataclass
class SleeperSyncReport:
    seasons: list[str] = field(default_factory=list)
    snapshots_changed: list[str] = field(default_factory=list)
    records_added: int = 0
    records_archived: int = 0
    records_unchanged: int = 0
    unmapped_fields: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


SLEEPER_SOURCE_ID = "sleeper:league-chain"


def _unmapped(league: dict[str, Any], fields: list[Field]) -> list[str]:
    known = {f.path for f in fields}
    present = [f"settings.{k}" for k in league.get("settings", {})]
    present += [f"scoring_settings.{k}" for k in league.get("scoring_settings", {})]
    return sorted(p for p in present if p not in known)


def sync_sleeper(
    store: LeagueStore, client: SleeperClient, league_id: str, sport: str
) -> SleeperSyncReport:
    fields = load_fields(sport)
    chain = client.chain(league_id)
    report = SleeperSyncReport()
    records: list[NewRecord] = []
    for league in chain:
        if league.get("sport") and league["sport"] != sport:
            raise SleeperError(
                f"league {league['league_id']} is {league['sport']}, expected {sport}"
            )
        season = str(league["season"])
        report.seasons.append(season)
        if store.upsert_snapshot(
            str(league["league_id"]), season, league.get("previous_league_id"), league
        ):
            report.snapshots_changed.append(season)
        records.extend(setting_records(league, fields))
    report.unmapped_fields = _unmapped(chain[0], fields) if chain else []

    source, _ = store.upsert_source(
        kind="sleeper",
        external_id=SLEEPER_SOURCE_ID,
        name="Sleeper league settings",
        path="sleeper",
        mime_type="application/json",
        status="approved",
    )
    store.set_source_basis(source.id, None, "sleeper-season")
    changes = store.replace_records(source.id, records)
    store.refresh_sleeper_record_status()
    report.records_added = changes.added
    report.records_archived = changes.archived
    report.records_unchanged = changes.unchanged
    return report
