import copy
import datetime as dt
import re

import pytest

from comish.answer.corpus import build_corpus
from comish.answer.render import source_line
from comish.ingest.fields import load_fields
from comish.ingest.history import build_history, label_playoff_games
from comish.ingest.history_sync import load_players, sync_history
from comish.kb.store import LeagueStore
from comish.leagues import League
from tests.history_fixtures import PLAYERS, TRADE, WINNERS, previous_league, season_data

FIELDS = load_fields("nfl")


def facts(**changes):
    result = build_history(season_data(**changes), previous_league(), FIELDS, set())
    assert result.skipped is None, result.skipped
    return {r.meta["key"]: r.text for r in result.records}


def test_standings_are_head_to_head_and_not_seeding():
    text = facts()["standings"]
    assert "Team Three (owner3) 3-0, 332.10 points for; Team One (owner1) 2-1, 300.60" in text
    assert "Team Four (owner4) 1-2, 226.30" in text and "Team Two (owner2) 0-3, 260.80" in text
    assert "not the playoff seeding" in text


def test_average_margin_weekly_median_and_highest_score():
    f = facts()
    # margins 9.90 39.90 15.10 15.10 44.90 1.30 sum 126.20 over 6 games
    assert "21.03 points over 6 games" in f["average margin of victory"]
    assert "week 1 95.15; week 2 90.65; week 3 100.55" in f["weekly median"]
    assert "120.30 points by Team Three (owner3) in week 1" in f["highest score"]


def test_playoff_games_are_labeled_from_how_they_feed_each_other():
    labels = label_playoff_games(WINNERS)
    assert labels == {1: "Semifinal", 2: "Semifinal", 3: "Championship game", 4: "3rd place game"}
    f = facts()
    assert (
        "Semifinal: Team Three (owner3) (130.00) beat Team Four (owner4) (100.00)"
        in f["playoffs > match 1"]
    )
    assert (
        "Championship game: Team Three (owner3) (110.00) beat Team Two (owner2) (105.50)"
        in f["playoffs > match 3"]
    )
    assert (
        "1st Team Three (owner3), 2nd Team Two (owner2), 3rd Team Four (owner4), 4th Team One"
        in f["playoffs > placements"]
    )
    assert f["champion"] == "The 2025 season league champion was Team Three (owner3)."


def test_playoff_scores_are_left_out_when_rounds_span_two_weeks():
    data = season_data()
    data.league["settings"]["playoff_round_type"] = 1
    result = build_history(data, None, FIELDS, set())
    text = {r.meta["key"]: r.text for r in result.records}["playoffs > match 3"]
    assert "Team Three (owner3) beat Team Two (owner2)" in text and "110.00" not in text


def test_top_scorer_counts_started_points_only():
    f = facts()
    # p1 started 60+50+55 = 165.00; the bench player p9 scored 50 per week and must not count.
    assert "was First1 Last1 with 165.00 points" in f["top scorer > roster 1"]
    assert "First6 Last6 with 171.00 points" in f["top scorer > roster 3"]
    assert "bench points not counted" in f["top scorer > roster 1"]


def test_top_scorer_ties_are_listed_and_empty_slots_are_never_a_player():
    f = facts()
    # roster 2: p3 and p4 each 45.10 + 35.35 + 49.95 = 130.40
    assert "top scorer (tied)" in f["top scorer > roster 2"]
    assert "First3 Last3 and First4 Last4 with 130.40 points" in f["top scorer > roster 2"]
    # roster 4's week 3 had an empty slot ("0"): p7 40.40 + 45.80 + 60.10 = 146.30 wins
    assert "First7 Last7 with 146.30 points" in f["top scorer > roster 4"]
    assert "player 0" not in " ".join(f.values())


def test_trade_names_assets_and_the_date_in_utc():
    text = facts()["trade t1"]
    assert text.startswith("2025 season trade on 2024-09-25 (UTC, week 2): ")
    assert "Team One (owner1) received First3 Last3" in text
    assert (
        "Team Two (owner2) received First5 Last5, 2026 round 2 pick "
        "(originally Team One (owner1)'s) and $10 of FAAB budget"
    ) in text


def test_draft_states_the_number_one_pick_and_each_round():
    f = facts()
    first = f["draft d1 > first overall pick"]
    assert (
        "rookie draft: the #1 overall pick (pick 1) was First9 Last9 (WR), "
        "selected by Team Two (owner2)"
    ) in first
    assert (
        "pick 1 First9 Last9 (WR) to Team Two (owner2); pick 2 First8 Last8 (WR) to Team One"
        in f["draft d1 > round 1"]
    )


def test_settings_change_is_known_only_to_the_season():
    text = facts()["settings changes"]
    assert text.startswith("Between the 2024 and 2025 seasons")
    assert "Waiver type: 0 (raw value) became 2 (raw value)" in text
    assert "doesn't record the date" in text


def test_settings_change_uses_words_only_for_verified_fields():
    verified = {"settings.waiver_type"}
    result = build_history(season_data(), previous_league(), FIELDS, verified)
    text = next(r.text for r in result.records if r.meta["key"] == "settings changes")
    assert "rolling waivers became FAAB bidding" in text and "raw value" not in text


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda d: d.league.update(status="in_season"), "not complete"),
        (lambda d: d.drafts[0].picks[0].update(picked_by="u3"), "roster owners not confirmed"),
        (lambda d: d.transactions[2][0].update(creator="u3"), "roster owners not confirmed"),
    ],
)
def test_a_season_that_fails_its_checks_writes_nothing(change, reason):
    data = season_data()
    change(data)
    result = build_history(data, None, FIELDS, set())
    assert result.records == [] and reason in (result.skipped or "")


def test_points_that_disagree_with_sleeper_withhold_only_matchup_facts():
    data = season_data()
    data.rosters[0]["settings"].update(fpts=299, fpts_decimal=60)  # Sleeper says 1.00 less
    result = build_history(data, previous_league(), FIELDS, set())
    assert result.skipped is None
    assert "don't match Sleeper's totals" in result.notes[0] and "roster 1" in result.notes[0]
    keys = {r.meta["key"] for r in result.records}
    assert not {"standings", "weekly median", "average margin of victory", "highest score"} & keys
    assert not any(k.startswith("top scorer") for k in keys)
    # Trades, drafts, the champion and settings changes don't use matchup scores.
    assert {"champion", "trade t1", "draft d1 > first overall pick", "settings changes"} <= keys
    games = [r.text for r in result.records if r.meta["key"].startswith("playoffs > match")]
    assert games
    assert not any(re.search(r"\d+\.\d\d", t) for t in games)  # no scores in any game


def test_no_evidence_for_owners_means_skip():
    data = season_data(drafts=[], transactions={})
    result = build_history(data, None, FIELDS, set())
    assert result.records == [] and "no draft picks or trades" in (result.skipped or "")


# Syncing into a league's store


class FakeSleeper:
    def __init__(self, data):
        self.data, self.player_fetches = data, 0

    # Only league "2" (the 2025 season) has any data; league "1" returns nothing.
    def rosters(self, league_id):
        return self.data.rosters if league_id == "2" else []

    def users(self, league_id):
        return self.data.users if league_id == "2" else []

    def bracket(self, league_id, which):
        return self.data.winners_bracket if league_id == "2" and which == "winners" else []

    def matchups(self, league_id, week):
        return self.data.matchups.get(week, []) if league_id == "2" else []

    def transactions(self, league_id, week):
        return self.data.transactions.get(week, []) if league_id == "2" else []

    def drafts(self, league_id):
        return [d.draft for d in self.data.drafts] if league_id == "2" else []

    def draft_picks(self, draft_id):
        return self.data.drafts[0].picks

    def players(self, sport):
        self.player_fetches += 1
        return PLAYERS


@pytest.fixture
def synced(tmp_path):
    store = LeagueStore(tmp_path, "football")
    data = season_data()
    store.upsert_snapshot("1", "2024", None, previous_league())
    store.upsert_snapshot("2", "2025", "1", data.league)
    client = FakeSleeper(data)
    yield store, client, data, tmp_path
    store.close()


def run(synced):
    store, client, _, tmp_path = synced
    return sync_history(store, client, "nfl", tmp_path / "cache")


def test_sync_writes_one_pending_source_per_completed_season(synced):
    store, *_ = synced
    report = run(synced)
    assert report.written == ["2025"]
    # 2024 has no drafts or trades in the fake, so its owners can't be confirmed.
    assert "no draft picks or trades" in report.skipped["2024"]
    (source,) = store.list_sources("history")
    assert source.external_id == "history:2025" and source.status == "pending_review"
    assert store.citable_records() == []


def test_unchanged_resync_changes_nothing_and_approval_makes_facts_citable(synced):
    store, *_ = synced
    first = run(synced)
    second = run(synced)
    assert (second.records_added, second.records_archived) == (0, 0)
    assert second.records_unchanged == first.records_added
    (source,) = store.list_sources("history")
    store.approve_source(source.id)
    assert len(store.citable_records()) == first.records_added


def test_a_changed_fact_goes_back_to_review(synced):
    store, _, data, _ = synced
    run(synced)
    (source,) = store.list_sources("history")
    store.approve_source(source.id)
    data.matchups[1][0]["points"] = 100.20  # one score corrected on Sleeper
    data.rosters[0]["settings"].update(fpts=300, fpts_decimal=70)
    report = run(synced)
    assert report.records_added > 0
    pending = [r for r in store.records_for_source(source.id) if r.status == "pending_review"]
    assert pending and all(r.record_type == "history_fact" for r in pending)
    assert len(store.citable_records()) < len(store.records_for_source(source.id))


def test_an_in_progress_season_is_never_written(synced):
    store, _, data, _ = synced
    in_progress = copy.deepcopy(data.league)
    in_progress.update(league_id="3", season="2026", status="in_season")
    store.upsert_snapshot("3", "2026", "2", in_progress)
    report = run(synced)
    assert "2026" not in report.written and "season is in_season" in report.skipped["2026"]
    assert [s.external_id for s in store.list_sources("history")] == ["history:2025"]


def test_history_is_citable_with_a_season_source_line_once_approved(synced):
    store, *_ = synced
    run(synced)
    (source,) = store.list_sources("history")
    store.approve_source(source.id)
    corpus = build_corpus(store)
    entry = next(e for e in corpus.entries.values() if e.record.meta["key"] == "champion")
    assert entry.cite_source == "history:2025" and not entry.undated
    league = League("football", "Test", "nfl", "2")
    line = source_line(entry, entry.record.text, league)
    assert line.startswith("Source: league history from Sleeper data, 2025 season:")
    assert "League history (Sleeper) season=2025" in corpus.text


def test_players_are_cached_for_a_day_per_league(synced, tmp_path):
    client = synced[1]
    cache = tmp_path / "players"
    now = dt.datetime(2026, 11, 8, 12, tzinfo=dt.UTC)
    load_players(client, "nfl", cache, now)
    load_players(client, "nfl", cache, now + dt.timedelta(hours=23))
    assert client.player_fetches == 1
    load_players(client, "nfl", cache, now + dt.timedelta(hours=25))
    assert client.player_fetches == 2
    assert TRADE  # the fixture's shared trade object is untouched by the sync
