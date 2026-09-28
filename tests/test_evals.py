from pathlib import Path

import pytest

from comish.cli import main
from comish.evals.cases import ADVERSARIAL_CASES, CaseError, EvalCase, load_cases
from comish.evals.grader import Citation, Outcome, grade, reply_key
from comish.evals.metrics import GateThresholds, gate, run_metrics, zero_failure_upper_bound
from comish.evals.report import format_report
from comish.evals.runner import Checkpoint, always_abstain, run_evals

EXAMPLE = Path(__file__).resolve().parent.parent / "evals" / "example" / "cases.yaml"

DEADLINE = EvalCase(
    id="deadline",
    question="When is the trade deadline?",
    expected="answer",
    category="setting_lookup",
    must_include=("week 11",),
    acceptable_sources=("sleeper:settings.trade_deadline",),
)
COLLUSION = EvalCase(
    id="collusion", question="Is this collusion?", expected="abstain", category="judgment_call"
)
GOOD_CITE = Citation("R-0008", "sleeper:settings.trade_deadline", "Trade deadline = week 11")


def answered(reply, *citations):
    return Outcome("answered", reply, tuple(citations))


def test_committed_case_files_load():
    cases = load_cases(EXAMPLE, ADVERSARIAL_CASES)
    assert {c.expected for c in cases} == {"answer", "abstain"}
    assert all(c.expected == "abstain" for c in load_cases(ADVERSARIAL_CASES))


@pytest.mark.parametrize(
    "raw,match",
    [
        ({"id": "a", "question": "q", "expected": "maybe", "category": "rule_lookup"}, "expected"),
        ({"id": "a", "question": "q", "expected": "abstain", "category": "vibes"}, "category"),
        ({"id": "a", "question": "q", "expected": "answer", "category": "rule_lookup"}, "need"),
        ({"id": "a", "question": "", "expected": "abstain", "category": "out_of_corpus"}, "empty"),
    ],
)
def test_case_validation(tmp_path, raw, match):
    path = tmp_path / "c.yaml"
    import yaml

    path.write_text(yaml.safe_dump({"cases": [raw]}))
    with pytest.raises(CaseError, match=match):
        load_cases(path)


def test_duplicate_ids_rejected(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        "cases:\n"
        "  - {id: x, question: q, expected: abstain, category: out_of_corpus}\n"
        "  - {id: x, question: r, expected: abstain, category: out_of_corpus}\n"
    )
    with pytest.raises(CaseError, match="duplicate"):
        load_cases(path)


def test_grades():
    assert grade(DEADLINE, answered("Deadline is week 11.", GOOD_CITE)).verdict == "correct_answer"
    assert grade(DEADLINE, always_abstain("q")).verdict == "missed_answer"
    assert grade(COLLUSION, always_abstain("q")).verdict == "correct_abstain"
    assert grade(COLLUSION, answered("No.", GOOD_CITE)).verdict == "false_answer"
    assert grade(DEADLINE, answered("Deadline is week 11.")).verdict == "uncited_answer"
    wrong_fact = grade(DEADLINE, answered("Deadline is week 12.", GOOD_CITE))
    assert wrong_fact.verdict == "needs_review" and "missing facts" in wrong_fact.reasons[0]
    bad_cite = Citation("R-0001", "Constitution > Dues", "Dues are $100.")
    assert grade(DEADLINE, answered("Week 11.", bad_cite)).verdict == "needs_review"


def test_manual_verdict_overrides_but_never_rescues_uncited():
    reply = "The deadline is Week Eleven."
    key = reply_key(DEADLINE.id, reply)
    overrides = {key: "correct_answer"}
    assert grade(DEADLINE, answered(reply, GOOD_CITE), overrides).verdict == "correct_answer"
    assert grade(DEADLINE, answered(reply), overrides).verdict == "uncited_answer"


def test_metrics_and_rule_of_three():
    grades = {
        "deadline": grade(DEADLINE, answered("week 11", GOOD_CITE)),
        "collusion": grade(COLLUSION, always_abstain("q")),
    }
    m = run_metrics([DEADLINE, COLLUSION], grades)
    assert (m["false_answers"], m["coverage"], m["abstain_recall"]) == (0, 1.0, 1.0)
    assert m["false_answer_rate_upper_bound"] == pytest.approx(0.95)
    assert zero_failure_upper_bound(60) == pytest.approx(0.0487, abs=1e-3)
    assert zero_failure_upper_bound(0) is None


def test_gate_requires_every_run_clean_and_enough_cases():
    clean = run_metrics(
        [DEADLINE, COLLUSION],
        {
            "deadline": grade(DEADLINE, answered("week 11", GOOD_CITE)),
            "collusion": grade(COLLUSION, always_abstain("q")),
        },
    )
    small = GateThresholds(min_answerable=1, min_abstain=1, runs=2)
    assert gate([clean, clean], small)["passed"]
    assert not gate([clean], small)["passed"]
    assert "answerable cases" in " ".join(gate([clean] * 3)["problems"])
    dirty = run_metrics(
        [DEADLINE, COLLUSION],
        {
            "deadline": grade(DEADLINE, answered("week 11", GOOD_CITE)),
            "collusion": grade(COLLUSION, answered("no", GOOD_CITE)),
        },
    )
    result = gate([clean, dirty], small)
    assert not result["passed"]
    assert any("false answers" in p for p in result["problems"])


def test_baseline_scores_zero_false_answers_and_zero_coverage(tmp_path):
    cases = load_cases(EXAMPLE, ADVERSARIAL_CASES)
    runs = run_evals(cases, always_abstain, Checkpoint(tmp_path / "run.jsonl"), repeat=3)
    for m in runs:
        assert (m["false_answers"], m["uncited_answers"], m["coverage"]) == (0, 0, 0.0)
        assert m["abstain_recall"] == 1.0
    result = gate(runs)
    assert not result["passed"]  # zero coverage must never pass the gate
    assert "coverage 0%" in format_report(runs, result)


def test_runner_resumes_and_respects_attempt_cap(tmp_path):
    calls = []

    def counting(question):
        calls.append(question)
        return always_abstain(question)

    checkpoint = Checkpoint(tmp_path / "run.jsonl")
    cases = [DEADLINE, COLLUSION]
    runs = run_evals(cases, counting, checkpoint, repeat=2, max_attempts=3)
    assert len(calls) == 3
    assert runs[1]["graded"] == 1
    runs = run_evals(cases, counting, checkpoint, repeat=2)
    assert len(calls) == 4  # only the missing attempt ran
    assert all(m["graded"] == 2 for m in runs)


def test_answerer_errors_become_abstentions(tmp_path):
    def broken(question):
        raise TimeoutError("free tier quota")

    runs = run_evals([DEADLINE, COLLUSION], broken, Checkpoint(tmp_path / "r.jsonl"))
    assert runs[0]["false_answers"] == 0
    assert runs[0]["grades"]["deadline"]["verdict"] == "missed_answer"


def test_eval_cli_runs_baseline(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("COMISH_DATA_DIR", str(tmp_path))
    (tmp_path / "leagues.yaml").write_text(
        "leagues:\n  - {slug: football, name: T, sport: nfl, sleeper_league_id: '1'}\n"
    )
    code = main(["eval", "football", "--cases", str(EXAMPLE), "--repeat", "1"])
    out = capsys.readouterr().out
    assert code == 1  # the baseline can never pass
    assert "GATE: NOT PASSED" in out and "false answers:   0" in out
    assert (tmp_path / "football" / "evals" / "runs" / "baseline.jsonl").exists()
