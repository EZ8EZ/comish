"""League history facts, computed in code from a completed Sleeper season.

A season's raw data (rosters, users, brackets, weekly matchups, transactions, drafts)
becomes short, self-contained fact records. The answer model only looks them up and
quotes them; it never computes anything, so the existing quote and number checks cover
history like any other source (docs/PLAN.md section 5c).

Nothing here is guessed. A season is skipped, with the reason reported, when:
- the league isn't complete,
- the roster owners can't be confirmed against that season's draft picks and trades
  (team names change in dynasty leagues, and a completed league's rosters may show
  today's owners), or

If the regular-season weeks don't reproduce Sleeper's own points-for totals, Sleeper's data
disagrees with itself and there is no way to tell which number is right. Every fact computed
from matchup scores is then withheld (the rest, such as trades and drafts, is kept) and the
sync report says so.

Settings changes are known only to the season, because Sleeper doesn't timestamp them.
"""

import datetime as dt
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from comish.ingest.fields import Field
from comish.kb.store import NewRecord

PF_TOLERANCE = Decimal("0.05")
CENT = Decimal("0.01")


@dataclass
class DraftData:
    draft: dict[str, Any]
    picks: list[dict[str, Any]]


@dataclass
class SeasonData:
    """Raw Sleeper payloads for one season. matchups and transactions are keyed by week."""

    league: dict[str, Any]
    rosters: list[dict[str, Any]]
    users: list[dict[str, Any]]
    winners_bracket: list[dict[str, Any]] = field(default_factory=list)
    losers_bracket: list[dict[str, Any]] = field(default_factory=list)
    matchups: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    transactions: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    drafts: list[DraftData] = field(default_factory=list)
    players: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def season(self) -> str:
        return str(self.league["season"])


@dataclass
class HistoryResult:
    season: str
    records: list[NewRecord] = field(default_factory=list)
    skipped: str | None = None
    notes: list[str] = field(default_factory=list)  # what was withheld, and why


def d(value: Any) -> Decimal:
    return Decimal(str(value))


def fmt(value: Decimal) -> str:
    return f"{value.quantize(CENT, rounding=ROUND_HALF_UP):.2f}"


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" and {items[-1]}"


# Weeks and teams


def regular_weeks(league: dict[str, Any]) -> range:
    settings = league.get("settings", {})
    start = int(settings.get("start_week") or 1)
    return range(start, int(settings.get("playoff_week_start") or 0))


def owners(data: SeasonData) -> dict[int, set[str]]:
    result: dict[int, set[str]] = {}
    for r in data.rosters:
        people = {str(r["owner_id"])} if r.get("owner_id") else set()
        people |= {str(c) for c in r.get("co_owners") or []}
        result[int(r["roster_id"])] = people
    return result


def team_names(data: SeasonData) -> dict[int, str]:
    users = {str(u["user_id"]): u for u in data.users}
    names = {}
    for roster_id, people in owners(data).items():
        owner = next((users[p] for p in sorted(people) if p in users), None)
        if owner is None:
            names[roster_id] = f"Roster {roster_id} (no owner)"
            continue
        team = (owner.get("metadata") or {}).get("team_name")
        who = owner.get("display_name") or str(owner["user_id"])
        names[roster_id] = f"{team} ({who})" if team and team != who else str(who)
    return names


def player_name(data: SeasonData, player_id: str, fallback: dict[str, Any] | None = None) -> str:
    info = (fallback or {}) if (fallback or {}).get("last_name") else data.players.get(player_id)
    if not info or not (info.get("first_name") or info.get("last_name")):
        return f"player {player_id}"
    return f"{info.get('first_name', '')} {info.get('last_name', '')}".strip()


def games_in_week(data: SeasonData, week: int) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    by_id: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in data.matchups.get(week, []):
        if row.get("matchup_id") is not None:
            by_id[row["matchup_id"]].append(row)
    return [(rows[0], rows[1]) for _, rows in sorted(by_id.items()) if len(rows) == 2]


def points_for(data: SeasonData) -> dict[int, Decimal]:
    totals: dict[int, Decimal] = defaultdict(Decimal)
    for week in regular_weeks(data.league):
        for row in data.matchups.get(week, []):
            totals[int(row["roster_id"])] += d(row.get("points") or 0)
    return totals


# Checks that decide whether a season may be written at all


def identity_problems(data: SeasonData) -> list[str]:
    """Why the roster owners can't be confirmed for this season (empty means confirmed).

    Each draft pick's picker and each trade's creator must own a roster that took part.
    Some evidence is required: with none, ownership is unverified and the season is skipped.
    """
    people = owners(data)
    evidence = 0
    problems = []
    for dd in data.drafts:
        for pick in dd.picks:
            picker, roster = pick.get("picked_by"), pick.get("roster_id")
            if not picker or roster is None or int(roster) not in people:
                continue
            evidence += 1
            if str(picker) not in people[int(roster)]:
                problems.append(f"draft pick {pick.get('pick_no')} by {picker} on roster {roster}")
    for trade in trades(data):
        creator = trade.get("creator")
        involved = [int(r) for r in trade.get("roster_ids") or [] if int(r) in people]
        if not creator or not involved:
            continue
        evidence += 1
        if not any(str(creator) in people[r] for r in involved):
            problems.append(f"trade {trade.get('transaction_id')} created by {creator}")
    if evidence == 0:
        problems.append("no draft picks or trades to confirm the roster owners against")
    return problems


def points_problems(data: SeasonData) -> list[str]:
    totals = points_for(data)
    problems = []
    for r in data.rosters:
        settings = r.get("settings") or {}
        if "fpts" not in settings:
            continue
        official = d(settings["fpts"]) + d(settings.get("fpts_decimal") or 0) / 100
        got = totals.get(int(r["roster_id"]), Decimal(0))
        if abs(got - official) > PF_TOLERANCE:
            problems.append(
                f"roster {r['roster_id']}: weeks {regular_weeks(data.league)} give "
                f"{fmt(got)} points, Sleeper says {fmt(official)}"
            )
    return problems


def trades(data: SeasonData) -> list[dict[str, Any]]:
    found = [
        t
        for week in sorted(data.transactions)
        for t in data.transactions[week]
        if t.get("type") == "trade" and t.get("status") == "complete"
    ]
    return sorted(found, key=lambda t: (t.get("created") or 0, str(t.get("transaction_id"))))


# Facts


def _fact(season: str, key: str, text: str, ordinal_: int = 0) -> NewRecord:
    return NewRecord(
        record_type="history_fact",
        section_path=f"{season} > {key}",
        text=text,
        ordinal=ordinal_,
        season=season,
        meta={"key": key},
    )


def standings_fact(data: SeasonData, names: dict[int, str]) -> NewRecord | None:
    weeks = regular_weeks(data.league)
    record: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0])
    scored: dict[int, Decimal] = defaultdict(Decimal)
    for week in weeks:
        for a, b in games_in_week(data, week):
            pa, pb = d(a.get("points") or 0), d(b.get("points") or 0)
            ra, rb = int(a["roster_id"]), int(b["roster_id"])
            scored[ra] += pa
            scored[rb] += pb
            if pa == pb:
                record[ra][2] += 1
                record[rb][2] += 1
            else:
                win, lose = (ra, rb) if pa > pb else (rb, ra)
                record[win][0] += 1
                record[lose][1] += 1
    if not record:
        return None
    order = sorted(record, key=lambda r: (-record[r][0], record[r][1], -scored[r], r))
    lines = [
        f"{names.get(r, f'Roster {r}')} {record[r][0]}-{record[r][1]}"
        + (f"-{record[r][2]}" if record[r][2] else "")
        + f", {fmt(scored[r])} points for"
        for r in order
    ]
    return _fact(
        data.season,
        "standings",
        f"{data.season} season regular-season head-to-head records (weeks {weeks.start} to "
        f"{weeks.stop - 1}), listed by wins then points for. This is not the playoff seeding, "
        f"and it leaves out median games: " + "; ".join(lines) + ".",
    )


def label_playoff_games(bracket: list[dict[str, Any]]) -> dict[int, str]:
    """Name each winners-bracket game from how the games feed each other."""
    by_match = {int(g["m"]): g for g in bracket}

    def feeds(game: dict[str, Any]) -> list[int]:
        return [
            int(src["w"])
            for key in ("t1_from", "t2_from")
            if isinstance(src := game.get(key), dict) and "w" in src
        ]

    labels: dict[int, str] = {}
    for m, g in by_match.items():
        placing = g.get("p")
        if placing == 1:
            labels[m] = "Championship game"
        elif placing:
            labels[m] = f"{ordinal(int(placing))} place game"
    final = next((m for m, label in labels.items() if label == "Championship game"), None)
    semis = set(feeds(by_match[final])) if final is not None else set()
    for m in semis:
        labels.setdefault(m, "Semifinal")
    quarters = {src for s in semis for src in feeds(by_match[s]) if src in by_match}
    for m in quarters:
        labels.setdefault(m, "Quarterfinal")
    for m, g in by_match.items():
        labels.setdefault(m, f"Playoff game (round {g['r']}, match {m})")
    return labels


def _week_points(data: SeasonData, week: int, roster: int) -> Decimal | None:
    for row in data.matchups.get(week, []):
        if int(row["roster_id"]) == roster and row.get("points") is not None:
            return d(row["points"])
    return None


def bracket_facts(
    data: SeasonData, names: dict[int, str], with_points: bool = True
) -> list[NewRecord]:
    season = data.season
    settings = data.league.get("settings", {})
    start = settings.get("playoff_week_start")
    one_week_rounds = with_points and start is not None and not settings.get("playoff_round_type")
    records: list[NewRecord] = []
    placements: dict[int, int] = {}

    def line(game: dict[str, Any], label: str) -> str | None:
        win, lose = game.get("w"), game.get("l")
        if win is None or lose is None:
            return None
        win, lose = int(win), int(lose)
        week = int(start) + int(game["r"]) - 1 if one_week_rounds else None
        pts = {r: _week_points(data, week, r) if week is not None else None for r in (win, lose)}

        def shown(r: int) -> str:
            return names.get(r, f"Roster {r}") + (
                f" ({fmt(pts[r])})" if pts[r] is not None else ""  # type: ignore[arg-type]
            )

        return f"{label}: {shown(win)} beat {shown(lose)}"

    labels = label_playoff_games(data.winners_bracket)
    for game in sorted(data.winners_bracket, key=lambda g: int(g["m"])):
        label = labels[int(game["m"])]
        text = line(game, label)
        if text is None:
            continue
        if game.get("p") and game.get("w") is not None and game.get("l") is not None:
            placements[int(game["p"])] = int(game["w"])
            placements[int(game["p"]) + 1] = int(game["l"])
        records.append(
            _fact(
                season,
                f"playoffs > match {game['m']}",
                f"{season} season playoffs, round {game['r']}, {text}.",
            )
        )
    for game in sorted(data.losers_bracket, key=lambda g: int(g["m"])):
        text = line(game, f"Consolation bracket game (round {game['r']}, match {game['m']})")
        if text:
            records.append(
                _fact(season, f"consolation > match {game['m']}", f"{season} season, {text}.")
            )
    if 1 in placements and 2 in placements:
        ranked = [
            f"{ordinal(p)} {names.get(placements[p], f'Roster {placements[p]}')}"
            for p in sorted(placements)
        ]
        records.append(
            _fact(
                season,
                "playoffs > placements",
                f"{season} season playoff results: " + ", ".join(ranked) + ".",
            )
        )
        records.append(
            _fact(
                season,
                "champion",
                f"The {season} season league champion was "
                f"{names.get(placements[1], f'Roster {placements[1]}')}.",
            )
        )
    return records


def top_scorer_facts(data: SeasonData, names: dict[int, str]) -> list[NewRecord]:
    """Per team, the player with the most points scored while in that team's starting lineup.

    Regular season only. A starter slot of "0" is empty and never counted; bench points
    never count. Ties are all listed.
    """
    weeks = regular_weeks(data.league)
    totals: dict[int, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for week in weeks:
        for row in data.matchups.get(week, []):
            starters = row.get("starters") or []
            scores = row.get("starters_points") or []
            for pid, pts in zip(starters, scores, strict=False):
                if pid and str(pid) != "0" and pts is not None:
                    totals[int(row["roster_id"])][str(pid)] += d(pts)
    records = []
    for roster in sorted(totals):
        best = max(totals[roster].values())
        top = sorted(p for p, v in totals[roster].items() if v == best)
        who = join([player_name(data, p) for p in top])
        team = names.get(roster, f"Roster {roster}")
        tie = " (tied)" if len(top) > 1 else ""
        records.append(
            _fact(
                data.season,
                f"top scorer > roster {roster}",
                f"{data.season} season, {team}: top scorer{tie} by points scored while in "
                f"the starting lineup (regular season weeks {weeks.start} to {weeks.stop - 1}, "
                f"bench points not counted) was {who} with {fmt(best)} points.",
            )
        )
    return records


def league_facts(data: SeasonData, names: dict[int, str]) -> list[NewRecord]:
    season, weeks = data.season, regular_weeks(data.league)
    margins: list[Decimal] = []
    medians: list[str] = []
    best: tuple[Decimal, int, int] | None = None
    for week in weeks:
        scores = [
            (d(r["points"]), int(r["roster_id"]))
            for r in data.matchups.get(week, [])
            if r.get("points") is not None
        ]
        if not scores:
            continue
        medians.append(f"week {week} {fmt(statistics.median(s for s, _ in scores))}")
        top = max(scores)
        if best is None or top[0] > best[0]:
            best = (top[0], top[1], week)
        margins += [abs(d(a["points"]) - d(b["points"])) for a, b in games_in_week(data, week)]
    records = []
    if margins:
        average = sum(margins, Decimal(0)) / len(margins)
        records.append(
            _fact(
                season,
                "average margin of victory",
                f"{season} season average margin of victory in regular-season head-to-head "
                f"games (weeks {weeks.start} to {weeks.stop - 1}): {fmt(average)} points "
                f"over {len(margins)} games.",
            )
        )
    if medians:
        records.append(
            _fact(
                season,
                "weekly median",
                f"{season} season weekly league median score (the median of every team's "
                f"score that week, regular season): " + "; ".join(medians) + ".",
            )
        )
    if best:
        records.append(
            _fact(
                season,
                "highest score",
                f"{season} season highest single-week team score (regular season): "
                f"{fmt(best[0])} points by {names.get(best[1], f'Roster {best[1]}')} in "
                f"week {best[2]}.",
            )
        )
    return records


def trade_facts(data: SeasonData, names: dict[int, str]) -> list[NewRecord]:
    records = []

    def team(roster: Any) -> str:
        return names.get(int(roster), f"Roster {roster}")

    for t in trades(data):
        received: dict[int, list[str]] = defaultdict(list)
        for pid, roster in (t.get("adds") or {}).items():
            received[int(roster)].append(player_name(data, str(pid)))
        for pick in t.get("draft_picks") or []:
            received[int(pick["owner_id"])].append(
                f"{pick['season']} round {pick['round']} pick (originally "
                f"{team(pick['roster_id'])}'s)"
            )
        for move in t.get("waiver_budget") or []:
            received[int(move["receiver"])].append(f"${move['amount']} of FAAB budget")
        parties = [r for r in (int(x) for x in t.get("roster_ids") or []) if r in received]
        if len(parties) < 2:
            continue
        when = dt.datetime.fromtimestamp(int(t["created"]) / 1000, dt.UTC).date().isoformat()
        text = "; ".join(f"{team(r)} received {join(received[r])}" for r in parties)
        records.append(
            _fact(
                data.season,
                f"trade {t['transaction_id']}",
                f"{data.season} season trade on {when} (UTC, week {t.get('leg')}): {text}.",
            )
        )
    return records


def draft_label(draft: dict[str, Any]) -> str:
    # player_type 1 is rookies only and 0 is all players, per the drafts Sleeper returns
    # for the two kinds of draft in a dynasty league.
    kind = (draft.get("settings") or {}).get("player_type")
    labels = {1: "rookie draft", 0: "draft (all players eligible)"}
    return labels.get(kind, "draft") if isinstance(kind, int) else "draft"


def draft_facts(data: SeasonData, names: dict[int, str]) -> list[NewRecord]:
    records = []
    for dd in data.drafts:
        if dd.draft.get("status") != "complete" or not dd.picks:
            continue
        season, label, did = data.season, draft_label(dd.draft), dd.draft["draft_id"]
        picks = sorted(dd.picks, key=lambda p: int(p["pick_no"]))

        def who_and_team(pick: dict[str, Any]) -> tuple[str, str]:
            meta = pick.get("metadata") or {}
            who = player_name(data, str(pick.get("player_id")), meta)
            pos = f" ({meta['position']})" if meta.get("position") else ""
            return who + pos, names.get(int(pick["roster_id"]), f"Roster {pick['roster_id']}")

        def describe(pick: dict[str, Any]) -> str:
            who, team = who_and_team(pick)
            return f"pick {pick['pick_no']} {who} to {team}"

        who, team = who_and_team(picks[0])
        records.append(
            _fact(
                season,
                f"draft {did} > first overall pick",
                f"{season} season {label}: the #1 overall pick (pick {picks[0]['pick_no']}) "
                f"was {who}, selected by {team}.",
            )
        )
        by_round: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for pick in picks:
            by_round[int(pick["round"])].append(pick)
        for rnd, in_round in sorted(by_round.items()):
            records.append(
                _fact(
                    season,
                    f"draft {did} > round {rnd}",
                    f"{season} season {label}, round {rnd}: "
                    + "; ".join(describe(p) for p in in_round)
                    + ".",
                )
            )
    return records


def settings_change_fact(
    data: SeasonData,
    previous: dict[str, Any] | None,
    fields: list[Field],
    verified: set[str],
) -> NewRecord | None:
    """What changed in Sleeper's league settings since the previous season.

    Sleeper doesn't timestamp setting changes, so this is only ever known to the season.
    A value is decoded into words only for a field the commissioner has verified.
    """
    if previous is None:
        return None
    changes = []
    for f in fields:
        before, after = f.lookup(previous), f.lookup(data.league)
        if before == after:
            continue

        def show(raw: Any, f: Field = f) -> str:
            if raw is None:
                return "not set"
            if f.path in verified:
                return f.render(raw)
            return str(",".join(raw) if isinstance(raw, list) else raw) + " (raw value)"

        changes.append(f"{f.label}: {show(before)} became {show(after)}")
    if not changes:
        return None
    prev = str(previous["season"])
    return _fact(
        data.season,
        "settings changes",
        f"Between the {prev} and {data.season} seasons, Sleeper's league settings changed "
        f"({data.season} compared with {prev}; Sleeper doesn't record the date): "
        + "; ".join(changes)
        + ".",
    )


def build_history(
    data: SeasonData,
    previous_league: dict[str, Any] | None,
    fields: list[Field],
    verified: set[str],
) -> HistoryResult:
    result = HistoryResult(data.season)
    if data.league.get("status") != "complete":
        result.skipped = f"season is {data.league.get('status')}, not complete"
        return result
    problems = identity_problems(data)
    if problems:
        result.skipped = "roster owners not confirmed: " + "; ".join(problems[:3])
        return result
    names = team_names(data)
    problems = points_problems(data)
    parts: list[NewRecord | None] = []
    if problems:
        result.notes.append(
            "matchup-based facts withheld (standings, margins, medians, top scorers, playoff "
            "scores): the regular-season points don't match Sleeper's totals: "
            + "; ".join(problems[:3])
        )
    else:
        parts.append(standings_fact(data, names))
        parts += top_scorer_facts(data, names)
        parts += league_facts(data, names)
    parts += bracket_facts(data, names, with_points=not problems)
    parts += trade_facts(data, names)
    parts += draft_facts(data, names)
    parts.append(settings_change_fact(data, previous_league, fields, verified))
    result.records = [p for p in parts if p is not None]
    return result
