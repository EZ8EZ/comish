"""Grade one answer attempt against its case, conservatively.

The key metric is the false-answer rate, so grading errs toward "false": an answer
that is uncited, cites an unacceptable source, or misses a required fact counts as
false until the commissioner records a manual verdict saying otherwise.
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

from comish.evals.cases import EvalCase

Verdict = Literal[
    "correct_answer",  # answered, required facts present, acceptable citation
    "correct_abstain",  # abstained on a should-abstain case
    "missed_answer",  # abstained on an answerable case (coverage loss, not an error)
    "false_answer",  # answered a should-abstain case, or answered wrongly
    "uncited_answer",  # answered with no citation: always a failure
    "needs_review",  # answered, but auto-grading can't confirm it; counts as false
]
FAILING: frozenset[Verdict] = frozenset({"false_answer", "uncited_answer", "needs_review"})


@dataclass(frozen=True)
class Citation:
    label: str  # "R-0042"
    source: str  # section path, e.g. "Constitution > Trades", or "sleeper:<field>"
    quote: str


@dataclass(frozen=True)
class Outcome:
    decision: Literal["answered", "abstained"]
    reply: str
    citations: tuple[Citation, ...] = ()
    abstain_reason: str = ""
    error: str = ""


@dataclass(frozen=True)
class Grade:
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def reply_key(case_id: str, reply: str) -> str:
    """Identifies one specific answer, for attaching manual verdicts."""
    return f"{case_id}:{hashlib.sha256(_norm(reply).encode()).hexdigest()[:16]}"


def _acceptable(citation: Citation, accepted: tuple[str, ...]) -> bool:
    return any(item == citation.label or citation.source.startswith(item) for item in accepted)


def grade(case: EvalCase, outcome: Outcome, overrides: dict[str, Verdict] | None = None) -> Grade:
    if outcome.decision == "abstained":
        if case.expected == "abstain":
            return Grade("correct_abstain")
        return Grade("missed_answer", [outcome.abstain_reason or "abstained"])

    if not outcome.citations:
        return Grade("uncited_answer", ["answer has no citation"])
    manual = (overrides or {}).get(reply_key(case.id, outcome.reply))
    if manual:
        return Grade(manual, ["manual verdict"])
    if case.expected == "abstain":
        return Grade("false_answer", [f"should abstain ({case.category})"])

    reasons = []
    reply = _norm(outcome.reply)
    missing = [fact for fact in case.must_include if _norm(fact) not in reply]
    if missing:
        reasons.append(f"missing facts: {missing}")
    if not any(_acceptable(c, case.acceptable_sources) for c in outcome.citations):
        reasons.append("no acceptable citation")
    if reasons:
        return Grade("needs_review", reasons)
    return Grade("correct_answer")
