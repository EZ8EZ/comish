"""Sync a league's Drive folder into its knowledge base.

Every file ends up in the report as exactly one of: ingested, unchanged, changed,
skipped (unsupported type, with the reason) or failed (with the reason). Nothing is
silently dropped, and a second run over an unchanged folder changes nothing.

New and changed content always lands as pending review. Files removed from Drive are
archived, which takes their records out of what the bot can cite.
"""

import hashlib
import mimetypes
from dataclasses import dataclass, field
from typing import Any

from comish.ingest import drive as d
from comish.ingest.docs import document_date, split_markdown
from comish.ingest.images import Transcriber, TranscriptionError
from comish.ingest.pdfs import PdfError, extract_pages
from comish.ingest.sheets import SheetError, parse_workbook
from comish.kb.store import LeagueStore, NewRecord, RecordChanges

DRIVE_KINDS = ("gdoc", "pdf", "sheet", "image")


@dataclass
class FileResult:
    path: str
    kind: str
    status: str  # ingested | unchanged | changed | skipped | failed
    detail: str = ""


@dataclass
class DriveSyncReport:
    files: list[FileResult] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(f.status == status for f in self.files)

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": [f.__dict__ for f in self.files],
            "archived": self.archived,
            "totals": {
                s: self.count(s) for s in ("ingested", "unchanged", "changed", "skipped", "failed")
            },
        }


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _kind(mime_type: str) -> str | None:
    if mime_type == d.GDOC:
        return "gdoc"
    if mime_type == d.GSHEET:
        return "sheet"
    if mime_type == d.PDF:
        return "pdf"
    if mime_type in d.IMAGE_TYPES:
        return "image"
    return None


def _status(is_new: bool, changes: RecordChanges | None, content_changed: bool) -> str:
    if is_new:
        return "ingested"
    if content_changed and (changes is None or changes.changed):
        return "changed"
    return "unchanged"


class DriveSync:
    def __init__(
        self, store: LeagueStore, client: d.DriveClient, transcribe: Transcriber | None = None
    ):
        self.store = store
        self.client = client
        self.transcribe = transcribe
        self.files_dir = store.dir / "files"

    def run(self, folder_id: str) -> DriveSyncReport:
        report = DriveSyncReport()
        files = d.walk(self.client, folder_id)
        seen: set[str] = set()
        for f in files:
            kind = _kind(f.mime_type)
            if kind is None:
                report.files.append(
                    FileResult(f.path, "unknown", "skipped", f"unsupported type {f.mime_type}")
                )
                continue
            seen.add(f.id)
            try:
                report.files.append(self._sync_file(f, kind))
            except (d.DriveError, PdfError, SheetError) as exc:
                self._fail(f, kind, str(exc))
                report.files.append(FileResult(f.path, kind, "failed", str(exc)))
        for source in self.store.list_sources():
            if source.kind in DRIVE_KINDS and source.external_id not in seen:
                self.store.archive_source(source.id, "removed from Drive")
                report.archived.append(source.path or source.name)
        return report

    def _fail(self, f: d.DriveFile, kind: str, reason: str) -> None:
        self.store.upsert_source(
            kind=kind,
            external_id=f.id,
            name=f.name,
            path=f.path,
            mime_type=f.mime_type,
            modified_time=f.modified_time,
            content_hash=None,
            date_hint=f.modified_time,
            status="failed",
            status_reason=reason,
        )

    def _upsert(self, f: d.DriveFile, kind: str, content_hash: str) -> tuple[int, bool, bool]:
        existing = self.store.source_by_external_id(f.id)
        source, changed = self.store.upsert_source(
            kind=kind,
            external_id=f.id,
            name=f.name,
            path=f.path,
            mime_type=f.mime_type,
            modified_time=f.modified_time,
            content_hash=content_hash,
            date_hint=f.modified_time,
        )
        return source.id, existing is None, changed

    def _sync_file(self, f: d.DriveFile, kind: str) -> FileResult:
        if kind == "gdoc":
            return self._sync_doc(f)
        if kind == "sheet":
            return self._sync_sheet(f)
        if kind == "pdf":
            return self._sync_pdf(f)
        return self._sync_image(f)

    def _sync_doc(self, f: d.DriveFile) -> FileResult:
        markdown = self.client.export(f.id, d.MARKDOWN).decode("utf-8")
        source_id, is_new, changed = self._upsert(f, "gdoc", _sha(markdown.encode()))
        doc_name = f.name.removesuffix(".md")
        records = [
            NewRecord(
                "doc_section",
                s.path,
                s.text,
                ordinal=s.ordinal,
                effective_date=s.proposed_date,
                date_basis="in-document" if s.proposed_date else None,
            )
            for s in split_markdown(doc_name, markdown)
        ]
        changes = self.store.replace_records(source_id, records)
        detail = f"{len(records)} sections"
        found = document_date(markdown)
        if found and not self.store.propose_source_date(source_id, found):
            detail += f"; the document now says {found}, review its date"
        return FileResult(f.path, "gdoc", _status(is_new, changes, changed), detail)

    def _sync_sheet(self, f: d.DriveFile) -> FileResult:
        rows = parse_workbook(f.name, self.client.export(f.id, d.XLSX))
        # Hash the parsed rows, not the file: xlsx exports differ byte-for-byte every time.
        digest = _sha("\n".join(f"{r.path}\t{r.text}" for r in rows).encode())
        source_id, is_new, changed = self._upsert(f, "sheet", digest)
        records = [NewRecord("sheet_row", r.path, r.text) for r in rows]
        changes = self.store.replace_records(source_id, records)
        return FileResult(f.path, "sheet", _status(is_new, changes, changed), f"{len(rows)} rows")

    def _sync_pdf(self, f: d.DriveFile) -> FileResult:
        existing = self.store.source_by_external_id(f.id)
        if existing and f.md5 and existing.content_hash == f.md5 and existing.status != "failed":
            return FileResult(f.path, "pdf", "unchanged")
        data = self.client.download(f.id)
        pdf = extract_pages(data)
        if not pdf.pages:
            raise PdfError("no extractable text (scanned PDF); export pages as images instead")
        source_id, is_new, changed = self._upsert(f, "pdf", f.md5 or _sha(data))
        records = [NewRecord("doc_section", f"{f.name} > page {n}", text) for n, text in pdf.pages]
        changes = self.store.replace_records(source_id, records)
        detail = f"{len(records)} pages"
        found = document_date(pdf.pages[0][1])
        if found and not self.store.propose_source_date(source_id, found):
            detail += f"; the document now says {found}, review its date"
        if pdf.empty_pages:
            detail += f"; no text on pages {pdf.empty_pages} (not ingested)"
        return FileResult(f.path, "pdf", _status(is_new, changes, changed), detail)

    def _sync_image(self, f: d.DriveFile) -> FileResult:
        existing = self.store.source_by_external_id(f.id)
        if existing and f.md5 and existing.content_hash == f.md5 and existing.status != "failed":
            return FileResult(f.path, "image", "unchanged")
        if self.transcribe is None:
            reason = "not transcribed: no LLM configured (set gemini_api_key)"
            self._fail(f, "image", reason)
            return FileResult(f.path, "image", "failed", reason)
        data = self.client.download(f.id)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        ext = mimetypes.guess_extension(f.mime_type) or ""
        (self.files_dir / f"{f.id}{ext}").write_bytes(data)
        try:
            transcript = self.transcribe(data, f.mime_type, f.name)
        except TranscriptionError as exc:
            reason = f"transcription failed: {exc}"
            self._fail(f, "image", reason)
            return FileResult(f.path, "image", "failed", reason)
        source_id, is_new, changed = self._upsert(f, "image", f.md5 or _sha(data))
        self.store.replace_records(
            source_id,
            [
                NewRecord(
                    "vote",
                    f.path,
                    transcript.text,
                    meta={"image_file": f"{f.id}{ext}"},
                )
            ],
        )
        (record,) = self.store.records_for_source(source_id)
        self.store.save_transcription(
            record.id,
            transcript.pass_a,
            transcript.pass_b,
            transcript.disagreements,
            transcript.model,
        )
        source = self.store.get_source(source_id)
        if transcript.proposed_date and source.date_basis == "none":
            self.store.set_source_basis(source_id, transcript.proposed_date, "visible-in-image")
        detail = "needs review"
        if transcript.disagreements:
            detail += f"; passes disagree on {', '.join(transcript.disagreements)}"
        return FileResult(f.path, "image", _status(is_new, None, changed), detail)
