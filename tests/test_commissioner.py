import datetime as dt

import pytest

from comish.answer.corpus import build_corpus
from comish.answer.render import source_line
from comish.commissioner.commands import CommissionerDesk, flag_message
from comish.commissioner.ops import OpsStore
from comish.kb.store import LeagueStore
from comish.leagues import League

ME = "+15555550100"
FOOTBALL = League(
    slug="football", name="Dynasty FB", sport="nfl", sleeper_league_id="1", flag_handles=(ME,)
)
HOOPS = League(
    slug="hoops",
    name="Hoops",
    sport="nba",
    sleeper_league_id="2",
    ruling_authority="relay",
    commissioner_name="Hoops Commish",
    flag_handles=(ME,),
)


@pytest.fixture
def desk(tmp_path):
    stores = {}

    def open_store(slug):
        if slug not in stores:
            stores[slug] = LeagueStore(tmp_path, slug)
        return stores[slug]

    ops = OpsStore(tmp_path)
    d = CommissionerDesk(
        {"football": FOOTBALL, "hoops": HOOPS}, ops, open_store, today=lambda: dt.date(2026, 10, 2)
    )
    yield d, open_store
    for s in stores.values():
        s.close()
    ops.close()


def flag(desk_tuple, league="football", question="Can I trade my taxi guy now?"):
    desk, open_store = desk_tuple
    flag_id = desk.ops.new_flag(league)
    open_store(league).create_flag(flag_id, question, "+15555550101", "sources conflict")
    return flag_id


def test_rule_requires_confirmation_then_becomes_citable(desk):
    d, open_store = desk
    fid = flag(desk)
    reply = d.handle(ME, f"rule F{fid} Taxi players can't be traded in season.")
    assert "Reply yes to save" in reply and "Commissioner ruling F1 (2026-10-02)" in reply
    assert open_store("football").citable_records() == []
    assert d.handle(ME, "yes").startswith("Saved as R-")
    (record,) = open_store("football").citable_records()
    assert record.text == "Taxi players can't be traded in season."
    assert record.record_type == "ruling"
    assert open_store("football").get_flag(fid)["status"] == "ruled"
    assert d.handle(ME, f"rule F{fid} again") == f"F{fid} is ruled."


def test_no_cancels(desk):
    d, open_store = desk
    fid = flag(desk)
    d.handle(ME, f"rule F{fid} Something.")
    assert d.handle(ME, "no") == "Cancelled."
    assert d.handle(ME, "yes") == "Nothing waiting for confirmation."
    assert open_store("football").citable_records() == []


def test_skip(desk):
    d, open_store = desk
    fid = flag(desk)
    assert d.handle(ME, f"skip F{fid}") == f"Dismissed F{fid}."
    assert open_store("football").get_flag(fid)["status"] == "dismissed"


def test_non_commissioners_get_no_reply(desk):
    d, _ = desk
    fid = flag(desk)
    assert d.handle("+15555550199", f"rule F{fid} Anything goes.") == ""


def test_flag_from_a_league_you_dont_run_is_refused(desk, tmp_path):
    d, open_store = desk
    other = League(slug="other", name="O", sport="nfl", sleeper_league_id="3", flag_handles=("x",))
    d.leagues["other"] = other
    fid = d.ops.new_flag("other")
    open_store("other").create_flag(fid, "q", None, "r")
    assert d.handle(ME, f"rule F{fid} Mine now.") == f"F{fid} isn't one of your flags."


def test_relay_ruling_is_cited_as_relayed(desk):
    d, open_store = desk
    fid = flag(desk, league="hoops", question="Can we trade injured players?")
    assert "relayed from Hoops Commish" in d.handle(
        ME, f"rule F{fid} Yes, IR players can be traded."
    )
    d.handle(ME, "yes")
    store = open_store("hoops")
    corpus = build_corpus(store)
    (entry,) = corpus.entries.values()
    line = source_line(entry, "Yes, IR players can be traded.", HOOPS)
    assert line == (
        "Source: Commissioner ruling F1, relayed from Hoops Commish (eff. 2026-10-02): "
        '"Yes, IR players can be traded."'
    )


def test_status_and_help(desk):
    d, _ = desk
    flag(desk)
    status = d.handle(ME, "status")
    assert "Dynasty FB" in status and "open flags: F1" in status
    assert d.handle(ME, "what?").startswith("Commands:")


def test_flag_message():
    text = flag_message(17, HOOPS, "+15555550101", "Can we trade IR guys?", "no record covers it")
    assert text.startswith('Flag F17 [Hoops] +15555550101 asked: "Can we trade IR guys?"')
    assert "Relay the Hoops Commish's ruling." in text
    assert text.endswith("Reply: rule F17 <ruling>  or  skip F17")
