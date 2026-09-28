"""Gate metrics for eval runs (docs/PLAN.md section 4)."""

from collections import Counter
from dataclasses import dataclass
from typing import Any

from comish.evals.cases import EvalCase
from comish.evals.grader import FAILING, Grade


@dataclass(frozen=True)
class GateThresholds:
    coverage: float = 0.75
    min_answerable: int = 40
    min_abstain: int = 40
    runs: int = 3


def zero_failure_upper_bound(n: int, confidence: float = 0.95) -> float | None:
    """With 0 failures in n trials, the true rate is below this at the given confidence.

    The exact form of the "rule of three" (about 3/n at 95%). A clean eval bounds the
    false-answer rate; it doesn't prove it is zero.
    """
    if n <= 0:
        return None
    return float(1 - (1 - confidence) ** (1 / n))


def run_metrics(cases: list[EvalCase], grades: dict[str, Grade]) -> dict[str, Any]:
    verdicts = Counter(g.verdict for g in grades.values())
    answerable = [c for c in cases if c.expected == "answer"]
    should_abstain = [c for c in cases if c.expected == "abstain"]
    answered = sum(n for v, n in verdicts.items() if v not in ("correct_abstain", "missed_answer"))
    failures = sum(verdicts[v] for v in FAILING)
    correct_abstains = sum(
        1 for c in should_abstain if grades.get(c.id) and grades[c.id].verdict == "correct_abstain"
    )
    return {
        "cases": len(cases),
        "graded": len(grades),
        "answerable": len(answerable),
        "should_abstain": len(should_abstain),
        "answered": answered,
        "verdicts": dict(verdicts),
        "false_answers": failures,
        "false_answer_rate": failures / answered if answered else 0.0,
        "false_answer_rate_upper_bound": (
            zero_failure_upper_bound(answered) if failures == 0 else None
        ),
        "uncited_answers": verdicts["uncited_answer"],
        "abstain_recall": correct_abstains / len(should_abstain) if should_abstain else None,
        "coverage": verdicts["correct_answer"] / len(answerable) if answerable else None,
        "failing_cases": sorted(cid for cid, g in grades.items() if g.verdict in FAILING),
    }


DEFAULT_THRESHOLDS = GateThresholds()


def gate(
    runs: list[dict[str, Any]], thresholds: GateThresholds = DEFAULT_THRESHOLDS
) -> dict[str, Any]:
    """The ship gate: every run clean, with enough cases and enough coverage."""
    problems: list[str] = []
    if len(runs) < thresholds.runs:
        problems.append(f"only {len(runs)} of {thresholds.runs} required runs")
    for i, m in enumerate(runs, start=1):
        if m["graded"] < m["cases"]:
            problems.append(f"run {i}: {m['cases'] - m['graded']} cases not graded yet")
        if m["false_answers"]:
            problems.append(f"run {i}: {m['false_answers']} false answers {m['failing_cases']}")
        if m["uncited_answers"]:
            problems.append(f"run {i}: {m['uncited_answers']} uncited answers")
        if m["abstain_recall"] is not None and m["abstain_recall"] < 1.0:
            problems.append(f"run {i}: abstain recall {m['abstain_recall']:.0%} (needs 100%)")
        if m["coverage"] is None or m["coverage"] < thresholds.coverage:
            cov = "n/a" if m["coverage"] is None else f"{m['coverage']:.0%}"
            problems.append(f"run {i}: coverage {cov} (needs {thresholds.coverage:.0%})")
    if runs:
        if runs[0]["answerable"] < thresholds.min_answerable:
            problems.append(
                f"{runs[0]['answerable']} answerable cases (needs {thresholds.min_answerable})"
            )
        if runs[0]["should_abstain"] < thresholds.min_abstain:
            problems.append(
                f"{runs[0]['should_abstain']} should-abstain cases (needs {thresholds.min_abstain})"
            )
    return {"passed": not problems, "problems": problems}
