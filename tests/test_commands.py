import pytest

from comish.commands import (
    add_league,
    format_sync_report,
    get_league,
    interactive_league,
    review_status,
    run_sync,
)
from comish.config import Settings
from comish.ingest import drive as d
from comish.ingest.sleeper import SleeperClient
from comish.kb.store import LeagueStore
from comish.leagues import League, LeagueConfigError
from tests.drive_fixtures import FakeDrive
from tests.sleeper_fixtures import FakeSleeper


def _sleeper(fake=None):
    fake = fake or FakeSleeper()
    return SleeperClient(fake.client(), min_interval_s=0)


class Answers:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.questions = []

    def __call__(self, question):
        self.questions.append(question)
        return self.answers.pop(0)


def test_interactive_add_by_username(tmp_path):
    fake = FakeSleeper()
    client = _sleeper(fake)
    client.state = lambda sport: {"season": "2026"}  # type: ignore[method-assign]
    said = []
    answers = Answers(
        "nfl",
        "@tester",
        "1",
        "",  # keep the Sleeper league name
        "football",
        "https://drive.google.com/drive/folders/1i0QV1CRCyPdwr6vjL6_GjchuLtUy7ok3?usp=sharing",
        "iMessage;+;chat1",
        "commissioner",
        "+15555550100",
    )
    league = interactive_league(answers, client, said.append)
    assert league == League(
        slug="football",
        name="Test Dynasty League",
        sport="nfl",
        sleeper_league_id="300",
        drive_folder_id="1i0QV1CRCyPdwr6vjL6_GjchuLtUy7ok3",
        chat_guid="iMessage;+;chat1",
        flag_handles=("+15555550100",),
    )
    assert "dynasty" in said[0]

    settings = Settings(data_dir=tmp_path)
    add_league(settings, league)
    assert get_league(settings, "football") == league
    with pytest.raises(LeagueConfigError, match="already exists"):
        add_league(settings, league)


def test_interactive_add_relay_league_by_id_without_drive():
    answers = Answers(
        "nba", "300", "Hoops", "hoops", "none", "later", "relay", "Their Commish", "later"
    )
    league = interactive_league(answers, _sleeper(), print)
    assert (league.drive_folder_id, league.chat_guid, league.ruling_authority) == (
        None,
        None,
        "relay",
    )
    assert league.commissioner_name == "Their Commish"


def test_get_unknown_league_lists_known(tmp_path):
    with pytest.raises(LeagueConfigError, match="configured: none"):
        get_league(Settings(data_dir=tmp_path), "football")


@pytest.fixture
def store(tmp_path):
    s = LeagueStore(tmp_path, "football")
    yield s
    s.close()


LEAGUE = League(
    slug="football", name="T", sport="nfl", sleeper_league_id="300", drive_folder_id="root"
)


def test_run_sync_without_drive_credentials_still_syncs_sleeper(store):
    report = run_sync(store, LEAGUE, _sleeper(), None, None)
    assert report["drive"] == {"skipped": "no Drive credentials (google_service_account_json)"}
    assert report["sleeper"]["seasons"] == ["2026", "2025", "2024"]
    assert "Drive: skipped" in format_sync_report(report)
    assert store.last_sync()["report"]["league"] == "football"


def test_run_sync_with_drive(store):
    drive = FakeDrive()
    drive.add_file("root", "doc1", "Rules", d.GDOC, {d.MARKDOWN: b"# A\nRule one."})
    report = run_sync(store, LEAGUE, _sleeper(), drive, None)
    text = format_sync_report(report)
    assert "ingested  Rules (1 sections)" in text
    status = review_status(store, LEAGUE)
    assert "citable records:  0" in status and "last sync:" in status


def test_run_sync_skips_drive_for_sleeper_only_league(store):
    league = League(slug="football", name="T", sport="nfl", sleeper_league_id="300")
    report = run_sync(store, league, _sleeper(), FakeDrive(), None)
    assert report["drive"] == {"skipped": "no Drive folder configured"}
