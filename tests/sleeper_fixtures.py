"""Synthetic Sleeper payloads shaped like real API responses (no real league data)."""

import copy
from typing import Any

import httpx

BASE_SETTINGS = {
    "type": 2,
    "num_teams": 12,
    "trade_deadline": 11,
    "trade_review_days": 1,
    "waiver_type": 2,
    "waiver_budget": 100,
    "playoff_teams": 7,
    "playoff_week_start": 15,
    "taxi_slots": 2,
    "reserve_slots": 1,
    "pick_trading": 1,
    "daily_waivers_last_ran": 12,  # not in the dictionary: must never be cited
}
BASE_SCORING = {"rec": 0.5, "pass_td": 4.0, "rush_td": 6.0, "fum_lost": -2.0}
ROSTER = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX"] + ["BN"] * 10


def league(league_id: str, season: str, previous: str | None, **settings: Any) -> dict:
    return {
        "league_id": league_id,
        "name": "Test Dynasty League",
        "season": season,
        "sport": "nfl",
        "status": "complete",
        "total_rosters": 12,
        "previous_league_id": previous,
        "settings": {**BASE_SETTINGS, **settings},
        "scoring_settings": dict(BASE_SCORING),
        "roster_positions": list(ROSTER),
    }


def chain() -> dict[str, dict]:
    """Three seasons: waivers were rolling (0) in 2024, FAAB (2) from 2025."""
    return {
        "300": league("300", "2026", "200"),
        "200": league("200", "2025", "100"),
        "100": league("100", "2024", None, waiver_type=0),
    }


class FakeSleeper:
    def __init__(self, leagues: dict[str, dict] | None = None):
        self.leagues = leagues if leagues is not None else chain()
        self.calls: list[str] = []
        self.user = {"user_id": "999", "username": "tester", "display_name": "Tester"}
        self.user_leagues = [copy.deepcopy(lg) for lg in self.leagues.values()][:1]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1/")
        self.calls.append(path)
        parts = path.split("/")
        if parts[0] == "league" and len(parts) == 2:
            data = self.leagues.get(parts[1])
            return (
                httpx.Response(200, json=copy.deepcopy(data))
                if data
                else httpx.Response(200, content=b"null")
            )
        if parts[0] == "user" and len(parts) == 2:
            if parts[1] == self.user["username"]:
                return httpx.Response(200, json=self.user)
            return httpx.Response(200, content=b"null")
        if parts[0] == "user" and parts[2] == "leagues":
            return httpx.Response(200, json=self.user_leagues)
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(self.handler), base_url="https://api.sleeper.app"
        )
