import datetime as dt

import pytest
from fastapi.testclient import TestClient

from comish.answer.pipeline import AnswerPipeline
from comish.answer.render import ABSTAIN_REPLY
from comish.audit import EventLog
from comish.bot import Bot, dm_chat_guid
from comish.commissioner.commands import CommissionerDesk
from comish.commissioner.ops import OpsStore
from comish.config import Settings
from comish.kb.store import LeagueStore, NewRecord
from comish.leagues import League
from comish.server import create_app
from tests.fixtures import GROUP_GUID, OTHER_GROUP_GUID, new_message

ME = "+15555550100"
MANAGER = "+15555550101"
TOKEN = "hook-token"
TAXI = "Taxi squad players may only be traded between the championship and the rookie draft."
LEAGUE = League(
    slug="football",
    name="Dynasty FB",
    sport="nfl",
    sleeper_league_id="1",
    chat_guid=GROUP_GUID,
    flag_handles=(ME,),
)


class ScriptedLLM:
    def __init__(self, model, responses):
        self.model = model
        self.responses = responses

    def generate_json(self, prompt, schema, images=None, system=None):
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


PASS = {
    "claims": [{"claim_index": 0, "supported": True, "note": ""}],
    "newer_record_may_supersede": False,
    "sources_conflict": False,
    "requires_judgment": False,
    "fully_answers_question": True,
    "question_in_scope": True,
    "verdict": "pass",
    "reason": "",
}


class FakeTransport:
    def __init__(self):
        self.sent = []

    async def send_text(self, chat_guid, text):
        self.sent.append((chat_guid, text))


@pytest.fixture
def world(tmp_path):
    store = LeagueStore(tmp_path, "football")
    doc, _ = store.upsert_source(kind="gdoc", external_id="d", name="Constitution")
    store.replace_records(doc.id, [NewRecord("doc_section", "Constitution > 4.2", TAXI)])
    store.set_source_date(doc.id, "2025-02-01")
    store.approve_source(doc.id)
    label = store.citable_records()[0].label
    ops = OpsStore(tmp_path)
    gen, ver = [], []
    now = dt.datetime(2026, 9, 28, tzinfo=dt.UTC)

    def pipeline_for(slug):
        return AnswerPipeline(
            store, LEAGUE, ScriptedLLM("g", gen), ScriptedLLM("v", ver), clock=lambda: now
        )

    desk = CommissionerDesk(
        {"football": LEAGUE}, ops, lambda s: store, today=lambda: dt.date(2026, 10, 2)
    )

    def make(mode):
        bot = Bot(mode, {"football": LEAGUE}, pipeline_for, lambda s: store, desk, ops)
        transport = FakeTransport()
        app = create_app(Settings(), transport, TOKEN, EventLog(tmp_path / f"{mode}.jsonl"), bot)
        return TestClient(app), transport

    good_draft = {
        "decision": "answer",
        "abstain_reason": "",
        "answer_text": "Taxi players can only be traded in the offseason.",
        "claims": [{"text": "x", "citations": [{"record_id": label, "quote": TAXI}]}],
        "conflicting_record_ids": [],
        "superseding_check": "",
    }
    yield make, gen, ver, good_draft, store
    store.close()
    ops.close()


def post(client, payload):
    return client.post(f"/webhooks/bluebubbles?token={TOKEN}", json=payload)


def question(text, guid="q1"):
    return new_message(f"@comish {text}", guid=guid, sender=MANAGER)


def test_live_answer_goes_to_group(world):
    make, gen, ver, good, _ = world
    client, transport = make("live")
    gen.append(good)
    ver.append(PASS)
    assert post(client, question("can I trade taxi guys?")).json()["handled"]
    ((chat, text),) = transport.sent
    assert chat == GROUP_GUID
    assert text.startswith("Taxi players can only be traded in the offseason.\nSource:")


def test_live_abstain_posts_abstain_and_flags_commissioner(world):
    make, gen, _, _, store = world
    client, transport = make("live")
    gen.append(
        {
            "decision": "abstain",
            "abstain_reason": "judgment call",
            "answer_text": "",
            "claims": [],
            "conflicting_record_ids": [],
            "superseding_check": "",
        }
    )
    post(client, question("is this trade fair?"))
    assert transport.sent[0] == (GROUP_GUID, ABSTAIN_REPLY)
    chat, flag_text = transport.sent[1]
    assert chat == dm_chat_guid(ME)
    assert flag_text.startswith(f'Flag F1 [Dynasty FB] {MANAGER} asked: "is this trade fair?"')
    assert store.get_flag(1)["status"] == "open"


def test_shadow_never_posts_to_the_group(world):
    make, gen, ver, good, _ = world
    client, transport = make("shadow")
    gen.append(good)
    ver.append(PASS)
    post(client, question("can I trade taxi guys?"))
    ((chat, text),) = transport.sent
    assert chat == dm_chat_guid(ME)
    assert text.startswith("[shadow, nothing posted]") and "I would reply:" in text
    assert all(c != GROUP_GUID for c, _ in transport.sent)


def test_unbound_chat_and_chatter_ignored(world):
    make, *_ = world
    client, transport = make("live")
    assert (
        post(client, new_message("@comish hi", chat_guid=OTHER_GROUP_GUID)).json()["reason"]
        == "unbound_chat"
    )
    assert post(client, new_message("just chatting", guid="c2")).json()["reason"] == "no_mention"
    assert transport.sent == []


def test_commissioner_dm_ruling_flow(world):
    make, gen, _, _, store = world
    client, transport = make("live")
    gen.append(
        {
            "decision": "abstain",
            "abstain_reason": "no record",
            "answer_text": "",
            "claims": [],
            "conflicting_record_ids": [],
            "superseding_check": "",
        }
    )
    post(client, question("can IR players be traded?"))
    dm = f"iMessage;-;{ME}"
    post(
        client,
        new_message(
            "rule F1 IR players can be traded.",
            guid="d1",
            chat_guid=dm,
            sender=ME,
            chats=[{"guid": dm, "style": 45}],
        ),
    )
    post(
        client,
        new_message("yes", guid="d2", chat_guid=dm, sender=ME, chats=[{"guid": dm, "style": 45}]),
    )
    assert transport.sent[-1][1].startswith("Saved as R-")
    assert any(r.record_type == "ruling" for r in store.citable_records())


def test_dm_from_non_commissioner_is_ignored(world):
    make, *_ = world
    client, transport = make("live")
    dm = f"iMessage;-;{MANAGER}"
    resp = post(
        client,
        new_message(
            "rule F1 anything",
            guid="x",
            chat_guid=dm,
            sender=MANAGER,
            chats=[{"guid": dm, "style": 45}],
        ),
    )
    assert resp.json()["reason"] == "dm_not_commissioner"
    assert transport.sent == []
