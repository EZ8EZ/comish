"""Eval cases: real and adversarial questions with the correct behavior for each.

Real league case files live in data/<slug>/evals/ (gitignored: this repo is public and
cases quote league records). Only the case format, generic adversarial templates and a
synthetic example set are committed.
"""

from collections import Counter
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml

Expected = Literal["answer", "abstain"]

CATEGORIES = {
    # Answerable
    "rule_lookup",
    "setting_lookup",
    "ruling_lookup",
    "history_lookup",
    # Must abstain (see docs/PLAN.md section 4)
    "superseded_rule",
    "doc_vote_conflict",
    "sleeper_doc_mismatch",
    "undated_only_source",
    "unapproved_only_source",
    "judgment_call",
    "uncovered_hypothetical",
    "out_of_corpus",
    "other_league",
    "prompt_injection",
    "false_premise",
    "partially_answerable",
    "ambiguous_referent",
    "live_data_out_of_scope",
}


# Generic should-abstain cases that run against every league.
ADVERSARIAL_CASES = Path(str(resources.files("comish.evals") / "adversarial.yaml"))


class CaseError(ValueError):
    pass


@dataclass(frozen=True)
class EvalCase:
    id: str
    question: str
    expected: Expected
    category: str
    # Facts the answer must state (case- and whitespace-insensitive substring match).
    must_include: tuple[str, ...] = ()
    # Acceptable citations: record labels ("R-0042"), source path prefixes
    # ("Constitution > Trades"), or Sleeper fields ("sleeper:settings.trade_deadline").
    acceptable_sources: tuple[str, ...] = ()
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


def _case(raw: dict[str, Any], origin: str) -> EvalCase:
    try:
        case = EvalCase(
            id=str(raw["id"]),
            question=str(raw["question"]).strip(),
            expected=raw["expected"],
            category=raw["category"],
            must_include=tuple(raw.get("must_include") or ()),
            acceptable_sources=tuple(raw.get("acceptable_sources") or ()),
            notes=str(raw.get("notes", "")),
            tags=tuple(raw.get("tags") or ()),
        )
    except KeyError as exc:
        raise CaseError(f"{origin}: case missing {exc}") from exc
    if case.expected not in ("answer", "abstain"):
        raise CaseError(f"{origin}: {case.id}: expected must be answer or abstain")
    if case.category not in CATEGORIES:
        raise CaseError(f"{origin}: {case.id}: unknown category {case.category!r}")
    if case.expected == "answer" and not (case.must_include and case.acceptable_sources):
        raise CaseError(
            f"{origin}: {case.id}: answer cases need must_include and acceptable_sources"
        )
    if not case.question:
        raise CaseError(f"{origin}: {case.id}: empty question")
    return case


def load_cases(*paths: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cases.extend(_case(item, path.name) for item in data.get("cases", []))
    dupes = [cid for cid, n in Counter(c.id for c in cases).items() if n > 1]
    if dupes:
        raise CaseError(f"duplicate case ids: {dupes}")
    return cases
