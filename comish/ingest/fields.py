"""The Sleeper field dictionary: which settings the bot may cite, and how to phrase them.

Sleeper's API documents field names but not most of their meanings, so every field
here must be verified by the commissioner against the Sleeper app before a setting
record built from it becomes citable. Fields not in the dictionary are never cited.
"""

from collections import Counter
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal

import yaml

Kind = Literal["int", "week", "days", "bool", "money", "enum", "points", "slots"]
KINDS = {"int", "week", "days", "bool", "money", "enum", "points", "slots"}


class FieldDictionaryError(ValueError):
    pass


@dataclass(frozen=True)
class Field:
    path: str
    label: str
    kind: Kind
    meaning: str
    app_enforced: bool
    values: dict[int, str] | None = None
    note: str = ""

    def lookup(self, league: dict[str, Any]) -> Any:
        """The raw value at this field's path, or None if the league doesn't have it."""
        node: Any = league
        for part in self.path.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def render(self, raw: Any) -> str:
        if self.kind == "slots":
            return render_slots(raw)
        if self.kind == "enum":
            return (self.values or {}).get(int(raw), f"unknown code {raw}")
        if self.kind == "bool":
            return "yes" if raw else "no"
        if self.kind == "week":
            return f"week {raw}"
        if self.kind == "days":
            return f"{raw} day" + ("" if raw == 1 else "s")
        if self.kind == "money":
            return f"${raw}"
        if self.kind == "points":
            return _points(raw)
        return str(raw)


def _points(raw: Any) -> str:
    value = float(raw)
    text = f"{value:g}"
    return f"{text} point" + ("" if abs(value) == 1 else "s")


def render_slots(positions: list[str]) -> str:
    counts = Counter(positions)
    starters = [p for p in dict.fromkeys(positions) if p not in ("BN", "IR", "TAXI")]
    parts = [f"{pos} x{counts[pos]}" for pos in starters]
    if counts.get("BN"):
        parts.append(f"bench x{counts['BN']}")
    return ", ".join(parts)


def _parse(raw: dict[str, Any], origin: str) -> Field:
    try:
        kind = raw["kind"]
        if kind not in KINDS:
            raise FieldDictionaryError(f"{origin}: {raw['path']}: unknown kind {kind!r}")
        values = raw.get("values")
        if kind == "enum" and not values:
            raise FieldDictionaryError(f"{origin}: {raw['path']}: enum needs values")
        return Field(
            path=raw["path"],
            label=raw["label"],
            kind=kind,
            meaning=raw["meaning"],
            app_enforced=bool(raw["app_enforced"]),
            values={int(k): str(v) for k, v in values.items()} if values else None,
            note=raw.get("note", ""),
        )
    except KeyError as exc:
        raise FieldDictionaryError(f"{origin}: missing key {exc}") from exc


def load_fields(sport: str) -> list[Field]:
    package = resources.files("comish.ingest") / "sleeper_fields"
    fields: list[Field] = []
    for name in ("common.yaml", f"{sport}.yaml"):
        data = yaml.safe_load((package / name).read_text(encoding="utf-8"))
        fields.extend(_parse(item, name) for item in data["fields"])
    paths = [f.path for f in fields]
    duplicates = [p for p, n in Counter(paths).items() if n > 1]
    if duplicates:
        raise FieldDictionaryError(f"duplicate field paths: {duplicates}")
    return fields
