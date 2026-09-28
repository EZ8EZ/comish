"""Screenshot transcription types. The two-pass LLM implementation is added in step 4."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


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
