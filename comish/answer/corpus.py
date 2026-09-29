"""Build the full-context corpus the answer model sees.

The whole approved corpus goes into every prompt (docs/PLAN.md section 3): with a
league's 10-50 files it fits easily, and full context means the model always sees any
newer record that might supersede an older one. Pending, rejected and archived
records are left out entirely; superseded ones are shown, marked, so the model can
explain history but code refuses to let them be cited.
"""

import hashlib
from dataclasses import dataclass

from comish.kb.store import LeagueStore, Record, Source

SOURCE_WORD = {
    "doc_section": "League document",
    "sheet_row": "League spreadsheet",
    "vote": "Screenshot",
    "ruling": "Commissioner ruling",
    "sleeper_setting": "Sleeper league settings",
}


@dataclass(frozen=True)
class Entry:
    record: Record
    source: Source
    effective_date: str | None
    undated: bool

    @property
    def label(self) -> str:
        return self.record.label

    @property
    def superseded(self) -> bool:
        return self.record.status == "superseded"

    @property
    def cite_source(self) -> str:
        """How eval cases and citations name this record's source."""
        if self.record.record_type == "sleeper_setting":
            return f"sleeper:{self.record.meta.get('path', '')}"
        return self.record.section_path


@dataclass(frozen=True)
class Corpus:
    entries: dict[str, Entry]
    text: str

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:16]

    def citable(self, label: str) -> Entry | None:
        entry = self.entries.get(label)
        return entry if entry and not entry.superseded else None


def _entry(record: Record, source: Source) -> Entry:
    date = record.effective_date or source.effective_date
    if record.record_type == "sleeper_setting":
        return Entry(record, source, None, undated=False)
    return Entry(record, source, date, undated=date is None)


def _render(entry: Entry) -> str:
    r = entry.record
    parts = [f"[{entry.label}]", SOURCE_WORD.get(r.record_type, r.record_type)]
    if r.record_type == "sleeper_setting":
        parts.append(f"season={r.season}")
        parts.append("app_enforced=yes" if r.meta.get("app_enforced") else "app_enforced=no")
    else:
        parts.append(f'section="{r.section_path}"')
        parts.append(f"effective={entry.effective_date}" if entry.effective_date else "UNDATED")
    if entry.superseded:
        parts.append(
            f"SUPERSEDED by R-{r.superseded_by:04d}, never cite"
            if r.superseded_by
            else "SUPERSEDED, never cite"
        )
    return " ".join(parts) + "\n" + r.text.strip()


def build_corpus(store: LeagueStore) -> Corpus:
    sources = {s.id: s for s in store.list_sources()}
    entries: dict[str, Entry] = {}
    for record in store.context_records():
        source = sources.get(record.source_id)
        if source is not None:
            entries[record.label] = _entry(record, source)
    text = "\n\n".join(_render(e) for e in entries.values())
    return Corpus(entries, text)
