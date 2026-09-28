import pytest

from comish.leagues import (
    League,
    LeagueConfigError,
    load_leagues,
    parse_drive_folder_id,
    save_leagues,
)


def _league(**overrides):
    base = dict(slug="football", name="Test League", sport="nfl", sleeper_league_id="123")
    base.update(overrides)
    return League(**base)


def test_round_trip(tmp_path):
    path = tmp_path / "leagues.yaml"
    leagues = {
        "football": _league(drive_folder_id="abcdefghij12", flag_handles=("+15555550100",)),
        "hoops": _league(
            slug="hoops",
            sport="nba",
            sleeper_league_id="456",
            ruling_authority="relay",
            commissioner_name="Their Commish",
        ),
    }
    save_leagues(path, leagues)
    assert load_leagues(path) == leagues


def test_missing_file_is_empty(tmp_path):
    assert load_leagues(tmp_path / "nope.yaml") == {}


@pytest.mark.parametrize(
    "overrides",
    [
        {"slug": "Bad Slug"},
        {"sport": "mlb"},
        {"sleeper_league_id": "abc"},
        {"ruling_authority": "relay"},  # relay needs commissioner_name
        {"ruling_authority": "dictator"},
    ],
)
def test_validation(overrides):
    with pytest.raises(LeagueConfigError):
        _league(**overrides)


def test_chat_guid_bound_to_one_league_only(tmp_path):
    path = tmp_path / "leagues.yaml"
    save_leagues(
        path,
        {
            "a": _league(slug="a", chat_guid="iMessage;+;chat1"),
            "b": _league(slug="b", chat_guid="iMessage;+;chat1"),
        },
    )
    with pytest.raises(LeagueConfigError, match="more than one league"):
        load_leagues(path)


def test_unknown_keys_rejected(tmp_path):
    path = tmp_path / "leagues.yaml"
    path.write_text(
        "leagues:\n  - {slug: a, name: A, sport: nfl, sleeper_league_id: '1', typo: x}\n"
    )
    with pytest.raises(LeagueConfigError, match="unknown league keys"):
        load_leagues(path)


@pytest.mark.parametrize(
    "value",
    [
        "https://drive.google.com/drive/folders/1i0QV1CRCyPdwr6vjL6_GjchuLtUy7ok3?usp=sharing",
        "https://drive.google.com/drive/u/0/folders/1i0QV1CRCyPdwr6vjL6_GjchuLtUy7ok3",
        "1i0QV1CRCyPdwr6vjL6_GjchuLtUy7ok3",
    ],
)
def test_parse_drive_folder_id(value):
    assert parse_drive_folder_id(value) == "1i0QV1CRCyPdwr6vjL6_GjchuLtUy7ok3"


def test_parse_drive_folder_id_rejects_junk():
    with pytest.raises(LeagueConfigError):
        parse_drive_folder_id("https://example.com/not-drive")
