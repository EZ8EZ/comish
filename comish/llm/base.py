"""The provider-neutral LLM interface.

Callers must treat LLMError as "no answer": ingestion marks the item failed and
retries later, and the answer pipeline abstains. Nothing downstream may guess.
"""

from dataclasses import dataclass
from typing import Any, Protocol


class LLMError(RuntimeError):
    """Any provider failure: network, quota, refusal, timeout, or invalid output."""


@dataclass(frozen=True)
class Image:
    data: bytes
    mime_type: str


class LLM(Protocol):
    model: str

    def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        images: list[Image] | None = None,
        system: str | None = None,
    ) -> dict[str, Any]: ...


def check_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Minimal JSON-schema validation for the subset we use; raises LLMError.

    Providers enforce schemas too, but output is never trusted: a missing field or a
    wrong type must fail loudly rather than flow into a record.
    """
    types = schema.get("type")
    allowed: list[str] = types if isinstance(types, list) else [str(types)]
    checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    if types is not None and not any(checks[t](value) for t in allowed):
        raise LLMError(f"{path}: expected {types}, got {type(value).__name__}")
    if "enum" in schema and value not in schema["enum"]:
        raise LLMError(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise LLMError(f"{path}: missing {key!r}")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                check_schema(value[key], sub, f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            check_schema(item, schema["items"], f"{path}[{i}]")
