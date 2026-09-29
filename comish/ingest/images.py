"""Two-pass screenshot transcription for review.

Each screenshot (a poll, a vote tally, a commissioner announcement) is transcribed
twice, independently, with different instructions. Code then compares the key fields;
any disagreement is shown to the commissioner, and a date is proposed only when both
passes read the same date. Every transcription starts pending review: nothing from an
image is citable until the commissioner approves it (docs/PLAN.md decision).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from comish.ingest.dates import parse_date
from comish.llm.base import LLM, Image, LLMError


class TranscriptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Transcript:
    text: str
    proposed_date: str | None
    pass_a: dict[str, Any] = field(default_factory=dict)
    pass_b: dict[str, Any] = field(default_factory=dict)
    disagreements: list[str] = field(default_factory=list)
    model: str = ""


# (image bytes, mime type, file name) -> Transcript; raises TranscriptionError.
Transcriber = Callable[[bytes, str, str], Transcript]

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["poll", "vote_tally", "announcement", "chat", "other"],
        },
        "is_league_decision": {"type": "boolean"},
        "decision": {"type": "string"},
        "outcome": {
            "type": "string",
            "enum": ["passed", "failed", "tied", "announced", "unclear", "not_a_decision"],
        },
        "votes_for": {"type": ["integer", "null"]},
        "votes_against": {"type": ["integer", "null"]},
        "date_text": {"type": ["string", "null"]},
        "verbatim_text": {"type": "string"},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "kind",
        "is_league_decision",
        "decision",
        "outcome",
        "votes_for",
        "votes_against",
        "date_text",
        "verbatim_text",
        "uncertainties",
    ],
}

COMPARED = ("is_league_decision", "outcome", "votes_for", "votes_against", "date")

SYSTEM = (
    "You transcribe screenshots from a fantasy sports league's group chat for the "
    "league's records. Report only what is visible. Never infer a date, a vote count, "
    "or an outcome that is not shown; use null and add an uncertainty instead."
)

PROMPT_A = (
    "Transcribe this screenshot. In `verbatim_text`, copy all legible text exactly, "
    "line by line. In `decision`, state in one sentence what the league decided or what "
    "was proposed, using the screenshot's own words where possible. `votes_for` and "
    "`votes_against` are counts shown in the image, or null. `date_text` is any date "
    "or timestamp exactly as displayed, or null. List anything illegible, cropped, or "
    "ambiguous in `uncertainties`."
)

PROMPT_B = (
    "Audit this screenshot as if checking someone else's transcription. Read every "
    "visible word, number, and timestamp. Decide whether it records a league decision "
    "(a poll result, a vote tally, or a commissioner announcement). Count votes only "
    "from what is displayed. Copy any displayed date exactly into `date_text`. Put the "
    "legible text in `verbatim_text` and every doubt, including cut-off content, in "
    "`uncertainties`. Summarize the decision in `decision`."
)


def _normalized(result: dict[str, Any]) -> dict[str, Any]:
    date = parse_date(result["date_text"]) if result.get("date_text") else None
    return {
        "is_league_decision": result["is_league_decision"],
        "outcome": result["outcome"],
        "votes_for": result["votes_for"],
        "votes_against": result["votes_against"],
        "date": date.isoformat() if date else None,
    }


def compare(pass_a: dict[str, Any], pass_b: dict[str, Any]) -> list[str]:
    a, b = _normalized(pass_a), _normalized(pass_b)
    return [key for key in COMPARED if a[key] != b[key]]


def render_record(name: str, result: dict[str, Any]) -> str:
    lines = [f"Screenshot {name}."]
    lines.append(f"Decision: {result['decision'].strip() or 'none stated'}")
    lines.append(f"Outcome: {result['outcome'].replace('_', ' ')}")
    if result["votes_for"] is not None or result["votes_against"] is not None:
        lines.append(
            f"Votes: {result['votes_for'] if result['votes_for'] is not None else '?'} for, "
            f"{result['votes_against'] if result['votes_against'] is not None else '?'} against"
        )
    if result["date_text"]:
        lines.append(f"Date shown: {result['date_text']}")
    text = result["verbatim_text"].strip()
    if text:
        lines.append(f'Text in image: "{text}"')
    return "\n".join(lines)


def make_transcriber(llm: LLM) -> Transcriber:
    def transcribe(data: bytes, mime_type: str, name: str) -> Transcript:
        images = [Image(data, mime_type)]
        try:
            pass_a = llm.generate_json(PROMPT_A, SCHEMA, images, SYSTEM)
            pass_b = llm.generate_json(PROMPT_B, SCHEMA, images, SYSTEM)
        except LLMError as exc:
            raise TranscriptionError(str(exc)) from exc
        disagreements = compare(pass_a, pass_b)
        date = _normalized(pass_a)["date"]
        return Transcript(
            text=render_record(name, pass_a),
            proposed_date=date if "date" not in disagreements else None,
            pass_a=pass_a,
            pass_b=pass_b,
            disagreements=disagreements,
            model=llm.model,
        )

    return transcribe
