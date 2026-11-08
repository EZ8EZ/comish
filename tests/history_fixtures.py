"""A tiny synthetic completed season: 4 teams, 3 regular-season weeks, 2 playoff rounds.

Every expected value in test_history.py is worked out by hand from these numbers.
Regular season (weeks 1-3) head-to-head and points:
  week 1: r1 100.10 beat r2 90.20; r3 120.30 beat r4 80.40
  week 2: r3 110.60 beat r1 95.50; r4 85.80 beat r2 70.70
  week 3: r1 105.00 beat r4 60.10; r3 101.20 beat r2 99.90
Playoffs: week 4 semifinals r3 130.00 beat r4 100.00 and r2 91.50 beat r1 90.00;
week 5 final r3 110.00 beat r2 105.50; third place r4 88.00 beat r1 80.50.
"""

import copy
from typing import Any

from comish.ingest.history import DraftData, SeasonData
from tests.sleeper_fixtures import league

PLAYERS = {
    f"p{i}": {"first_name": f"First{i}", "last_name": f"Last{i}", "position": "WR", "team": "XYZ"}
    for i in range(1, 10)
}

USERS = [
    {"user_id": f"u{i}", "display_name": f"owner{i}", "metadata": {"team_name": f"Team {name}"}}
    for i, name in enumerate(["One", "Two", "Three", "Four"], start=1)
]

# week -> roster -> (matchup_id, points, [starter ids], [starter points])
REGULAR = {
    1: {
        1: (1, 100.10, ["p1", "p2"], [60.00, 40.10]),
        2: (1, 90.20, ["p3", "p4"], [45.10, 45.10]),
        3: (2, 120.30, ["p5", "p6"], [70.30, 50.00]),
        4: (2, 80.40, ["p7", "p8"], [40.40, 40.00]),
    },
    2: {
        1: (1, 95.50, ["p1", "p2"], [50.00, 45.50]),
        3: (1, 110.60, ["p5", "p6"], [40.60, 70.00]),
        2: (2, 70.70, ["p3", "p4"], [35.35, 35.35]),
        4: (2, 85.80, ["p7", "p8"], [45.80, 40.00]),
    },
    3: {
        1: (1, 105.00, ["p1", "p2"], [55.00, 50.00]),
        4: (1, 60.10, ["p7", "0"], [60.10, 0.0]),  # an empty starter slot
        2: (2, 99.90, ["p3", "p4"], [49.95, 49.95]),
        3: (2, 101.20, ["p5", "p6"], [50.20, 51.00]),
    },
}
PLAYOFFS = {
    4: {3: (1, 130.00), 4: (1, 100.00), 1: (2, 90.00), 2: (2, 91.50)},
    5: {3: (1, 110.00), 2: (1, 105.50), 4: (2, 88.00), 1: (2, 80.50)},
}
FPTS = {1: 300.60, 2: 260.80, 3: 332.10, 4: 226.30}

TRADE = {
    "type": "trade",
    "status": "complete",
    "transaction_id": "t1",
    "created": 1727233973679,  # 2024-09-25 UTC
    "leg": 2,
    "creator": "u1",
    "roster_ids": [1, 2],
    "adds": {"p3": 1, "p5": 2},
    "drops": {"p3": 2, "p5": 1},
    "draft_picks": [{"round": 2, "season": "2026", "roster_id": 1, "owner_id": 2}],
    "waiver_budget": [{"sender": 1, "receiver": 2, "amount": 10}],
}


def _pick(no: int, roster: int, player: str, rnd: int) -> dict[str, Any]:
    return {
        "pick_no": no,
        "round": rnd,
        "roster_id": roster,
        "picked_by": f"u{roster}",
        "player_id": player,
        "metadata": {
            "first_name": f"First{player[1:]}",
            "last_name": f"Last{player[1:]}",
            "position": "WR",
        },
    }


DRAFT = DraftData(
    {
        "draft_id": "d1",
        "status": "complete",
        "type": "linear",
        "season": "2025",
        "settings": {"player_type": 1, "rounds": 2},
    },
    [_pick(1, 2, "p9", 1), _pick(2, 1, "p8", 1), _pick(3, 3, "p7", 2), _pick(4, 4, "p6", 2)],
)

WINNERS = [
    {"m": 1, "r": 1, "t1": 3, "t2": 4, "w": 3, "l": 4},
    {"m": 2, "r": 1, "t1": 1, "t2": 2, "w": 2, "l": 1},
    {
        "m": 3,
        "r": 2,
        "p": 1,
        "t1": 3,
        "t2": 2,
        "w": 3,
        "l": 2,
        "t1_from": {"w": 1},
        "t2_from": {"w": 2},
    },
    {
        "m": 4,
        "r": 2,
        "p": 3,
        "t1": 4,
        "t2": 1,
        "w": 4,
        "l": 1,
        "t1_from": {"l": 1},
        "t2_from": {"l": 2},
    },
]


def _league(league_id: str, season: str, previous: str | None, waiver_type: int) -> dict[str, Any]:
    return league(
        league_id,
        season,
        previous,
        playoff_week_start=4,
        start_week=1,
        last_scored_leg=5,
        playoff_round_type=0,
        league_average_match=1,
        waiver_type=waiver_type,
    )


def previous_league() -> dict[str, Any]:
    return _league("1", "2024", None, waiver_type=0)


def season_data(**changes: Any) -> SeasonData:
    current = league(
        "2",
        "2025",
        "1",
        playoff_week_start=4,
        start_week=1,
        last_scored_leg=5,
        playoff_round_type=0,
        league_average_match=1,
        waiver_type=2,
    )
    matchups: dict[int, list[dict[str, Any]]] = {}
    for week, rows in REGULAR.items():
        matchups[week] = [
            {
                "roster_id": r,
                "matchup_id": m,
                "points": pts,
                "starters": s,
                "starters_points": sp,
                "players_points": {"p9": 50.0},
            }
            for r, (m, pts, s, sp) in rows.items()
        ]
    for week, prow in PLAYOFFS.items():
        matchups[week] = [
            {"roster_id": r, "matchup_id": m, "points": pts, "starters": [], "starters_points": []}
            for r, (m, pts) in prow.items()
        ]
    rosters = [
        {
            "roster_id": i,
            "owner_id": f"u{i}",
            "settings": {"fpts": int(FPTS[i]), "fpts_decimal": round((FPTS[i] % 1) * 100)},
        }
        for i in range(1, 5)
    ]
    data = SeasonData(
        league=current,
        rosters=rosters,
        users=copy.deepcopy(USERS),
        winners_bracket=copy.deepcopy(WINNERS),
        matchups=matchups,
        transactions={2: [copy.deepcopy(TRADE)]},
        drafts=[copy.deepcopy(DRAFT)],
        players=copy.deepcopy(PLAYERS),
    )
    for key, value in changes.items():
        setattr(data, key, value)
    return data
