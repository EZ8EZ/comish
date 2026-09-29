import pytest

from comish.ingest.fields import FieldDictionaryError, _parse, load_fields, render_slots
from comish.ingest.sleeper import (
    SleeperClient,
    SleeperError,
    find_user_leagues,
    setting_records,
    sync_sleeper,
)
from comish.kb.store import LeagueStore
from tests.sleeper_fixtures import FakeSleeper, league


def _client(fake: FakeSleeper) -> SleeperClient:
    return SleeperClient(fake.client(), min_interval_s=0, sleep=lambda s: None)


@pytest.fixture
def store(tmp_path):
    s = LeagueStore(tmp_path, "football")
    yield s
    s.close()


def test_dictionaries_load_for_both_sports():
    nfl = {f.path for f in load_fields("nfl")}
    nba = {f.path for f in load_fields("nba")}
    assert "scoring_settings.rec" in nfl and "scoring_settings.rec" not in nba
    assert "scoring_settings.reb" in nba
    assert "settings.trade_deadline" in nfl & nba


def test_enum_needs_values():
    with pytest.raises(FieldDictionaryError):
        _parse(
            {"path": "x", "label": "X", "kind": "enum", "meaning": "c", "app_enforced": True},
            "t",
        )


def test_render_kinds():
    fields = {f.path: f for f in load_fields("nfl")}
    assert fields["settings.trade_deadline"].render(11) == "week 11"
    assert fields["settings.waiver_type"].render(2) == "FAAB bidding"
    assert fields["settings.waiver_type"].render(7) == "unknown code 7"
    assert fields["settings.pick_trading"].render(1) == "yes"
    assert fields["settings.waiver_budget"].render(100) == "$100"
    assert fields["scoring_settings.rec"].render(0.5) == "0.5 points"
    assert fields["scoring_settings.fum_lost"].render(-2.0) == "-2 points"
    assert fields["scoring_settings.pass_td"].render(1) == "1 point"
    assert fields["settings.trade_review_days"].render(1) == "1 day"


def test_render_slots():
    assert render_slots(["QB", "RB", "RB", "FLEX", "BN", "BN"]) == "QB x1, RB x2, FLEX x1, bench x2"


def test_chain_walks_newest_first():
    fake = FakeSleeper()
    seasons = [lg["season"] for lg in _client(fake).chain("300")]
    assert seasons == ["2026", "2025", "2024"]


def test_chain_detects_loops():
    fake = FakeSleeper({"1": league("1", "2025", "2"), "2": league("2", "2024", "1")})
    with pytest.raises(SleeperError, match="loops"):
        _client(fake).chain("1")


def test_missing_league_raises():
    with pytest.raises(SleeperError, match="no Sleeper league"):
        _client(FakeSleeper()).chain("404")


def test_find_user_leagues_accepts_at_handle():
    fake = FakeSleeper()
    (choice,) = find_user_leagues(_client(fake), "@tester", "nfl", "2026")
    assert (choice.league_id, choice.name, choice.league_type) == ("300", "Test Dynasty League", 2)


def test_unknown_user():
    with pytest.raises(SleeperError, match="no Sleeper user"):
        find_user_leagues(_client(FakeSleeper()), "nobody", "nfl", "2026")


def test_setting_record_text_and_meta():
    records = {
        r.section_path: r for r in setting_records(league("1", "2025", None), load_fields("nfl"))
    }
    deadline = records["2025 > settings.trade_deadline"]
    assert deadline.text == (
        "Sleeper league settings, 2025 season: Trade deadline = week 11 "
        "[settings.trade_deadline = 11]"
    )
    assert deadline.meta["path"] == "settings.trade_deadline"
    assert deadline.meta["app_enforced"] is True
    roster = records["2025 > roster_positions"]
    assert "QB x1, RB x2, WR x2, TE x1, FLEX x2, bench x10" in roster.text
    # Fields outside the dictionary never become records.
    assert not any("daily_waivers_last_ran" in path for path in records)


def test_sync_is_gated_by_verification_and_idempotent(store):
    fake = FakeSleeper()
    report = sync_sleeper(store, _client(fake), "300", "nfl")
    assert report.seasons == ["2026", "2025", "2024"]
    assert report.snapshots_changed == ["2026", "2025", "2024"]
    assert "settings.daily_waivers_last_ran" in report.unmapped_fields
    assert store.citable_records() == []

    store.verify_field("settings.waiver_type")
    citable = store.citable_records()
    assert sorted(r.season for r in citable) == ["2024", "2025", "2026"]
    by_season = {r.season: r.text for r in citable}
    assert "rolling waivers" in by_season["2024"]
    assert "FAAB bidding" in by_season["2025"]

    again = sync_sleeper(store, _client(fake), "300", "nfl")
    assert again.snapshots_changed == []
    assert (again.records_added, again.records_archived) == (0, 0)
    assert len(store.citable_records()) == 3


def test_changed_setting_replaces_record(store):
    fake = FakeSleeper()
    sync_sleeper(store, _client(fake), "300", "nfl")
    store.verify_field("settings.trade_deadline")
    fake.leagues["300"]["settings"]["trade_deadline"] = 12
    report = sync_sleeper(store, _client(fake), "300", "nfl")
    assert report.snapshots_changed == ["2026"]
    assert (report.records_added, report.records_archived) == (1, 1)
    current = [r.text for r in store.citable_records() if r.season == "2026"]
    assert current == [
        "Sleeper league settings, 2026 season: Trade deadline = week 12 "
        "[settings.trade_deadline = 12]"
    ]


def test_sport_mismatch_rejected(store):
    fake = FakeSleeper()
    with pytest.raises(SleeperError, match="expected nba"):
        sync_sleeper(store, _client(fake), "300", "nba")
