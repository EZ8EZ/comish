"""League instances: one config entry per league, loaded from data/leagues.yaml.

The file lives under the gitignored data/ directory because it holds chat GUIDs and
phone numbers. config/leagues.example.yaml documents the format.
"""

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

Sport = Literal["nfl", "nba"]
RulingAuthority = Literal["commissioner", "relay"]

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
DRIVE_FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_-]{10,})")


class LeagueConfigError(ValueError):
    pass


@dataclass(frozen=True)
class League:
    slug: str
    name: str
    sport: Sport
    sleeper_league_id: str
    drive_folder_id: str | None = None
    chat_guid: str | None = None
    # "commissioner": flag_handles belong to the league's commissioner.
    # "relay": flag_handles belong to a trusted manager who relays the
    # commissioner's rulings; rulings are cited as relayed.
    ruling_authority: RulingAuthority = "commissioner"
    commissioner_name: str | None = None
    flag_handles: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not SLUG_RE.match(self.slug):
            raise LeagueConfigError(f"invalid slug {self.slug!r}: use a-z, 0-9 and hyphens")
        if self.sport not in ("nfl", "nba"):
            raise LeagueConfigError(f"{self.slug}: sport must be nfl or nba")
        if not self.sleeper_league_id.isdigit():
            raise LeagueConfigError(f"{self.slug}: sleeper_league_id must be numeric")
        if self.ruling_authority not in ("commissioner", "relay"):
            raise LeagueConfigError(f"{self.slug}: ruling_authority must be commissioner or relay")
        if self.ruling_authority == "relay" and not self.commissioner_name:
            raise LeagueConfigError(f"{self.slug}: relay leagues need commissioner_name")


def parse_drive_folder_id(value: str) -> str:
    """Accept a Drive folder URL or a bare folder ID."""
    match = DRIVE_FOLDER_RE.search(value)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", value):
        return value
    raise LeagueConfigError(f"not a Drive folder link or ID: {value!r}")


def _from_dict(raw: dict[str, Any]) -> League:
    known = set(League.__dataclass_fields__)
    unknown = set(raw) - known
    if unknown:
        raise LeagueConfigError(f"unknown league keys: {sorted(unknown)}")
    data = dict(raw)
    data["sleeper_league_id"] = str(data.get("sleeper_league_id", ""))
    data["flag_handles"] = tuple(data.get("flag_handles") or ())
    try:
        return League(**data)
    except TypeError as exc:
        raise LeagueConfigError(str(exc)) from exc


def load_leagues(path: Path) -> dict[str, League]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    leagues = [_from_dict(item) for item in raw.get("leagues", [])]
    by_slug: dict[str, League] = {}
    for league in leagues:
        if league.slug in by_slug:
            raise LeagueConfigError(f"duplicate league slug {league.slug!r}")
        by_slug[league.slug] = league
    chats = [lg.chat_guid for lg in leagues if lg.chat_guid]
    if len(chats) != len(set(chats)):
        raise LeagueConfigError("a chat GUID is bound to more than one league")
    return by_slug


def save_leagues(path: Path, leagues: dict[str, League]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    items = []
    for league in leagues.values():
        item = asdict(league)
        item["flag_handles"] = list(league.flag_handles)
        items.append({k: v for k, v in item.items() if v not in (None, [])})
    path.write_text(yaml.safe_dump({"leagues": items}, sort_keys=False), encoding="utf-8")
