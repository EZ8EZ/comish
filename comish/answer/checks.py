"""Deterministic checks on a drafted answer. They can only reject, never approve.

Each check that fails adds a reason; any reason means the bot abstains. These run
before the LLM verifier and don't depend on what either model was told, so a prompt
injection can't talk its way past them.
"""

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from comish.answer.corpus import Corpus

MIN_QUOTE_CHARS = 20
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
QUOTE_CHARS = str.maketrans(
    {
        chr(0x2018): "'",  # left single quote
        chr(0x2019): "'",  # right single quote
        chr(0x201C): '"',  # left double quote
        chr(0x201D): '"',  # right double quote
        chr(0x2013): "-",  # en dash
        chr(0x2014): "-",  # em dash
        chr(0x00A0): " ",  # no-break space
    }
)


@dataclass(frozen=True)
class CheckedCitation:
    label: str
    quote: str


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(QUOTE_CHARS)).strip()


def numbers(text: str) -> set[str]:
    """Numbers as written, ignoring thousands separators: '$1,000' -> {'1000'}."""
    found = set()
    for token in NUMBER_RE.findall(text):
        token = token.replace(",", "")
        found.add(token.rstrip("0").rstrip(".") if "." in token else token.lstrip("0") or "0")
    return found


def citations(draft: dict[str, Any]) -> list[CheckedCitation]:
    return [
        CheckedCitation(str(c.get("record_id", "")), str(c.get("quote", "")))
        for claim in draft.get("claims", [])
        for c in claim.get("citations", [])
    ]


def _quote_ok(quote: str, text: str) -> bool:
    q, t = normalize(quote), normalize(text)
    if not q or q not in t:
        return False
    return len(q) >= MIN_QUOTE_CHARS or q == t


def run_checks(
    draft: dict[str, Any],
    corpus: Corpus,
    current_snapshot: tuple[str, str] | None,
    now: dt.datetime,
    max_snapshot_age: dt.timedelta = dt.timedelta(hours=24),
) -> list[str]:
    """Reasons to reject the draft; an empty list means these checks found nothing.

    current_snapshot is (league_id, fetched_at) for the in-progress season. Completed
    seasons never change, so only the current season's settings can go stale.
    """
    failures: list[str] = []
    if draft.get("decision") != "answer":
        return ["drafted as abstain"]
    if not str(draft.get("answer_text", "")).strip():
        failures.append("empty answer text")
    claims = draft.get("claims") or []
    if not claims:
        failures.append("no claims")
    for i, claim in enumerate(claims):
        if not claim.get("citations"):
            failures.append(f"claim {i + 1} has no citation")
    if draft.get("conflicting_record_ids"):
        failures.append(f"model reported conflicting records {draft['conflicting_record_ids']}")

    cited = citations(draft)
    quotes: list[str] = []
    for c in cited:
        entry = corpus.entries.get(c.label)
        if entry is None:
            failures.append(f"{c.label} is not an approved record in this league")
            continue
        if entry.superseded:
            failures.append(f"{c.label} is superseded")
            continue
        if not _quote_ok(c.quote, entry.record.text):
            failures.append(f"quote for {c.label} is not verbatim from the record")
            continue
        quotes.append(c.quote)
        if entry.undated and draft.get("conflicting_record_ids"):
            failures.append(f"{c.label} is undated and conflicts exist")
        if entry.record.record_type == "sleeper_setting":
            league_id = str(entry.record.meta.get("league_id", ""))
            if current_snapshot is not None and league_id == current_snapshot[0]:
                fetched = dt.datetime.fromisoformat(current_snapshot[1])
                if now - fetched > max_snapshot_age:
                    failures.append(f"{c.label}: Sleeper snapshot is stale; re-sync first")

    stated = numbers(str(draft.get("answer_text", "")))
    supported = set().union(*(numbers(q) for q in quotes)) if quotes else set()
    unsupported = sorted(stated - supported)
    if unsupported:
        failures.append(f"numbers not in any cited quote: {unsupported}")
    return _dedupe(failures)


def _dedupe(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))
