import datetime as dt

import pytest

from comish.answer.checks import normalize, numbers
from comish.answer.pipeline import AnswerPipeline
from comish.answer.render import ABSTAIN_REPLY
from comish.kb.store import LeagueStore, NewRecord
from comish.leagues import League
from comish.llm.base import LLMError

LEAGUE = League(slug="football", name="Test Dynasty", sport="nfl", sleeper_league_id="300")
NOW = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
TAXI = "Taxi squad players may only be traded between the championship and the rookie draft."


class ScriptedLLM:
    def __init__(self, model, *responses):
        self.model = model
        self.responses = list(responses)
        self.calls = []

    def generate_json(self, prompt, schema, images=None, system=None):
        self.calls.append((prompt, system))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def store(tmp_path):
    s = LeagueStore(tmp_path, "football")
    doc, _ = s.upsert_source(kind="gdoc", external_id="doc1", name="Constitution")
    s.replace_records(doc.id, [NewRecord("doc_section", "Constitution > Trades > 4.2", TAXI)])
    s.set_source_date(doc.id, "2025-02-01")
    s.approve_source(doc.id)
    sleeper, _ = s.upsert_source(
        kind="sleeper", external_id="sleeper:league-chain", name="Sleeper", status="approved"
    )
    s.set_source_basis(sleeper.id, None, "sleeper-season")
    s.upsert_snapshot("300", "2026", "200", {"season": "2026"})
    s.upsert_snapshot("200", "2025", None, {"season": "2025"})
    s.replace_records(
        sleeper.id,
        [
            NewRecord(
                "sleeper_setting",
                f"{season} > settings.trade_deadline",
                f"Sleeper league settings, {season} season: Trade deadline = week 11 "
                "[settings.trade_deadline = 11]",
                season=season,
                meta={"path": "settings.trade_deadline", "league_id": lid, "app_enforced": True},
            )
            for season, lid in (("2026", "300"), ("2025", "200"))
        ],
    )
    s.verify_field("settings.trade_deadline")
    yield s
    s.close()


def label(store, text_start):
    return next(r.label for r in store.citable_records() if r.text.startswith(text_start))


def draft(answer_text, *cites, conflicts=()):
    return {
        "decision": "answer",
        "abstain_reason": "",
        "answer_text": answer_text,
        "claims": [
            {"text": answer_text, "citations": [{"record_id": r, "quote": q} for r, q in cites]}
        ],
        "conflicting_record_ids": list(conflicts),
        "superseding_check": "checked newer records",
    }


PASS = {
    "claims": [{"claim_index": 0, "supported": True, "note": "quoted"}],
    "newer_record_may_supersede": False,
    "sources_conflict": False,
    "requires_judgment": False,
    "fully_answers_question": True,
    "question_in_scope": True,
    "verdict": "pass",
    "reason": "",
}


def pipeline(store, gen_responses, ver_responses=(), fetched_at=None):
    if fetched_at:
        store._db.execute("UPDATE sleeper_snapshots SET fetched_at = ?", (fetched_at,))
    gen = ScriptedLLM("gen", *gen_responses)
    ver = ScriptedLLM("ver", *ver_responses)
    return AnswerPipeline(store, LEAGUE, gen, ver, clock=lambda: NOW), gen, ver


def test_happy_path_answers_with_citation_and_date(store):
    taxi = label(store, "Taxi squad")
    p, gen, _ = pipeline(
        store,
        [draft("Taxi players can only be traded in the offseason.", (taxi, TAXI))],
        [PASS],
    )
    result = p.answer("Can I trade a taxi player in season?", asked_by="+15555550101")
    assert result.decision == "answered"
    assert result.reply == (
        "Taxi players can only be traded in the offseason.\n"
        f'Source: Constitution > Trades > 4.2 (eff. 2025-02-01): "{TAXI}"'
    )
    assert result.citations[0].source == "Constitution > Trades > 4.2"
    assert "Test Dynasty" in gen.calls[0][1] and TAXI in gen.calls[0][1]
    (attempt,) = store.attempts()
    assert (attempt["decision"], attempt["asked_by"]) == ("answered", "+15555550101")


def test_sleeper_answer_renders_season(store):
    deadline = label(store, "Sleeper league settings, 2026")
    quote = "Trade deadline = week 11"
    p, _, _ = pipeline(
        store,
        [draft("The trade deadline is week 11.", (deadline, quote))],
        [PASS],
        fetched_at=NOW.isoformat(),
    )
    result = p.answer("When is the trade deadline?")
    assert result.decision == "answered"
    assert result.reply.endswith(
        'Source: Sleeper league settings, 2026 season: "Trade deadline = week 11"'
    )
    assert result.citations[0].source == "sleeper:settings.trade_deadline"


@pytest.mark.parametrize(
    "make_draft,reason",
    [
        (
            lambda t: draft("Offseason only.", (t, "Taxi players may be traded any time.")),
            "not verbatim",
        ),
        (lambda t: draft("Offseason only.", ("R-9999", TAXI)), "not an approved record"),
        (lambda t: draft("Offseason only.", (t, "Taxi")), "not verbatim"),
        (
            lambda t: draft("Offseason only, since 2021.", (t, TAXI)),
            "numbers not in any cited quote",
        ),
        (
            lambda t: draft("Offseason only.", (t, TAXI), conflicts=["R-0005"]),
            "conflicting records",
        ),
        (lambda t: {**draft("Offseason only."), "claims": []}, "no claims"),
        (lambda t: draft("", (t, TAXI)), "empty answer text"),
    ],
)
def test_deterministic_checks_force_abstain(store, make_draft, reason):
    taxi = label(store, "Taxi squad")
    p, _, ver = pipeline(store, [make_draft(taxi)], [PASS])
    result = p.answer("Can I trade a taxi player in season?")
    assert (result.decision, result.reply) == ("abstained", ABSTAIN_REPLY)
    assert reason in result.reason
    assert ver.calls == []  # the verifier never sees a draft that failed code checks


@pytest.mark.parametrize(
    "verdict,reason",
    [
        ({**PASS, "verdict": "fail", "reason": "quote is about keepers"}, "verifier failed"),
        ({**PASS, "newer_record_may_supersede": True}, "newer record"),
        ({**PASS, "sources_conflict": True}, "conflict"),
        ({**PASS, "requires_judgment": True}, "judgment"),
        ({**PASS, "fully_answers_question": False}, "fully answer"),
        ({**PASS, "question_in_scope": False}, "out of scope"),
        ({**PASS, "claims": [{"claim_index": 0, "supported": False, "note": "no"}]}, "confirm"),
    ],
)
def test_verifier_objections_force_abstain(store, verdict, reason):
    taxi = label(store, "Taxi squad")
    p, _, _ = pipeline(store, [draft("Offseason only.", (taxi, TAXI))], [verdict])
    result = p.answer("Can I trade a taxi player?")
    assert result.decision == "abstained" and reason in result.reason


def test_llm_errors_abstain(store):
    p, _, _ = pipeline(store, [LLMError("429 quota")])
    assert "generator error" in p.answer("Anything?").reason
    taxi = label(store, "Taxi squad")
    p, _, _ = pipeline(store, [draft("Offseason only.", (taxi, TAXI))], [LLMError("timeout")])
    assert "verifier error" in p.answer("Anything?").reason


def test_model_abstention_is_respected(store):
    p, _, ver = pipeline(
        store, [{**draft("x"), "decision": "abstain", "abstain_reason": "judgment call"}]
    )
    result = p.answer("Is this trade fair?")
    assert result.decision == "abstained" and "judgment call" in result.reason
    assert ver.calls == []


def test_superseded_record_cannot_be_cited(store):
    taxi = label(store, "Taxi squad")
    store._db.execute("UPDATE records SET status = 'superseded' WHERE id = ?", (int(taxi[2:]),))
    p, gen, _ = pipeline(store, [draft("Offseason only.", (taxi, TAXI))], [PASS])
    result = p.answer("Can I trade a taxi player?")
    assert result.decision == "abstained" and "superseded" in result.reason
    assert "SUPERSEDED" in gen.calls[0][1]  # shown as history, never citable


def test_stale_current_season_snapshot_abstains_but_past_seasons_do_not(store):
    stale = (NOW - dt.timedelta(days=3)).isoformat()
    current = label(store, "Sleeper league settings, 2026")
    past = label(store, "Sleeper league settings, 2025")
    quote = "Trade deadline = week 11"
    p, _, _ = pipeline(store, [draft("Week 11.", (current, quote))], [PASS], fetched_at=stale)
    assert "stale" in p.answer("Deadline?").reason
    p, _, _ = pipeline(store, [draft("Week 11.", (past, quote))], [PASS], fetched_at=stale)
    assert p.answer("What was the 2025 deadline?").decision == "answered"


def test_empty_league_abstains_without_calling_the_model(tmp_path):
    empty = LeagueStore(tmp_path, "football")
    p, gen, _ = pipeline(empty, [])
    assert "no approved records" in p.answer("Deadline?").reason
    assert gen.calls == []
    empty.close()


def test_record_from_another_league_is_rejected(store, tmp_path):
    other = LeagueStore(tmp_path / "other", "hoops")
    src, _ = other.upsert_source(kind="gdoc", external_id="h", name="Hoops rules")
    other.replace_records(
        src.id, [NewRecord("doc_section", "Hoops > Trades", "Hoops trades are allowed year-round.")]
    )
    other.mark_undated(src.id)
    other.approve_source(src.id)
    hoops_label = other.citable_records()[0].label
    # The same label may exist in football as a different record; force a mismatch.
    p, _, _ = pipeline(
        store, [draft("Year-round.", ("R-0999", "Hoops trades are allowed year-round."))], [PASS]
    )
    assert p.answer("Can I trade?").decision == "abstained"
    assert hoops_label  # the other league's record exists, but football can't see it
    other.close()


def test_store_and_league_must_match(store):
    with pytest.raises(ValueError):
        AnswerPipeline(
            store,
            League(slug="hoops", name="H", sport="nba", sleeper_league_id="1"),
            ScriptedLLM("g"),
            ScriptedLLM("v"),
        )


def test_normalize_and_numbers():
    assert normalize("“Taxi”  players" + chr(0x2014) + "rookies") == '"Taxi" players-rookies'
    assert numbers("$1,000 and 0.50 points, week 011") == {"1000", "0.5", "11"}
