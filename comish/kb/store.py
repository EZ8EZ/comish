"""The per-league knowledge base.

One SQLite file per league at <data_dir>/<slug>/league.db. A LeagueStore is opened
for exactly one league and has no way to reach another league's file, which is how
league isolation is enforced in code.

Review rules enforced here, not in the UI:
- A record is citable only if it and its source are both approved.
- A document source can't be approved until its date is decided: a date is set
  (from the document or by the commissioner) or it is explicitly marked undated.
- Re-syncing a changed section archives the old record and adds the new text as
  pending, so edits on Drive never silently change what the bot can cite.
"""

import datetime as dt
import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from comish.kb.schema import MIGRATIONS
from comish.leagues import SLUG_RE

UNDATED_KINDS_OK = {"sleeper", "ruling"}


class ReviewError(ValueError):
    """A review action that the rules don't allow."""


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_date(value: str) -> str:
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ReviewError(f"not an ISO date (YYYY-MM-DD): {value!r}") from exc


@dataclass(frozen=True)
class Source:
    id: int
    kind: str
    external_id: str
    name: str
    path: str
    mime_type: str
    modified_time: str | None
    content_hash: str | None
    effective_date: str | None
    date_basis: str
    date_hint: str | None
    status: str
    status_reason: str | None


@dataclass(frozen=True)
class Record:
    id: int
    source_id: int
    record_type: str
    section_path: str
    ordinal: int
    text: str
    season: str | None
    effective_date: str | None
    date_basis: str | None
    status: str
    superseded_by: int | None
    topic_tags: list[str]
    meta: dict[str, Any]

    @property
    def label(self) -> str:
        return f"R-{self.id:04d}"


@dataclass(frozen=True)
class NewRecord:
    record_type: str
    section_path: str
    text: str
    ordinal: int = 0
    season: str | None = None
    effective_date: str | None = None
    date_basis: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecordChanges:
    added: int = 0
    unchanged: int = 0
    archived: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.added or self.archived)


def _source(row: sqlite3.Row) -> Source:
    return Source(**{k: row[k] for k in Source.__dataclass_fields__})


def _record(row: sqlite3.Row) -> Record:
    data = {k: row[k] for k in Record.__dataclass_fields__ if k not in ("topic_tags", "meta")}
    return Record(**data, topic_tags=json.loads(row["topic_tags"]), meta=json.loads(row["meta"]))


class LeagueStore:
    def __init__(self, data_dir: Path, slug: str):
        if not SLUG_RE.match(slug):
            raise ValueError(f"invalid league slug {slug!r}")
        self.slug = slug
        self.dir = data_dir / slug
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "league.db"
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def _migrate(self) -> None:
        with self._tx() as db:
            db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = db.execute("SELECT version FROM schema_version").fetchone()
            version = row["version"] if row else 0
            if row is None:
                db.execute("INSERT INTO schema_version (version) VALUES (0)")
            for number, sql in enumerate(MIGRATIONS[version:], start=version + 1):
                for statement in (s.strip() for s in sql.split(";")):
                    if statement:
                        db.execute(statement)
                db.execute("UPDATE schema_version SET version = ?", (number,))

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    # Sources

    def upsert_source(
        self,
        *,
        kind: str,
        external_id: str,
        name: str,
        path: str = "",
        mime_type: str = "",
        modified_time: str | None = None,
        content_hash: str | None = None,
        date_hint: str | None = None,
        status: str | None = None,
        status_reason: str | None = None,
    ) -> tuple[Source, bool]:
        """Insert or update a source. Returns (source, content_changed)."""
        ts = now_iso()
        with self._tx() as db:
            row = db.execute(
                "SELECT * FROM sources WHERE external_id = ?", (external_id,)
            ).fetchone()
            if row is None:
                db.execute(
                    """INSERT INTO sources (kind, external_id, name, path, mime_type,
                       modified_time, content_hash, date_hint, status, status_reason,
                       created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        kind,
                        external_id,
                        name,
                        path,
                        mime_type,
                        modified_time,
                        content_hash,
                        date_hint,
                        status or "pending_review",
                        status_reason,
                        ts,
                        ts,
                    ),
                )
                changed = True
            else:
                changed = row["content_hash"] != content_hash
                new_status = status or row["status"]
                if row["status"] in ("archived", "failed") and status is None:
                    new_status = "pending_review"
                db.execute(
                    """UPDATE sources SET name = ?, path = ?, mime_type = ?, modified_time = ?,
                       content_hash = ?, date_hint = ?, status = ?, status_reason = ?,
                       updated_at = ? WHERE id = ?""",
                    (
                        name,
                        path,
                        mime_type,
                        modified_time,
                        content_hash,
                        date_hint,
                        new_status,
                        status_reason,
                        ts,
                        row["id"],
                    ),
                )
                changed = changed or row["status"] in ("archived", "failed")
            source_row = db.execute(
                "SELECT * FROM sources WHERE external_id = ?", (external_id,)
            ).fetchone()
        return _source(source_row), changed

    def get_source(self, source_id: int) -> Source:
        rows = self._query("SELECT * FROM sources WHERE id = ?", (source_id,))
        if not rows:
            raise KeyError(f"no source {source_id}")
        return _source(rows[0])

    def source_by_external_id(self, external_id: str) -> Source | None:
        rows = self._query("SELECT * FROM sources WHERE external_id = ?", (external_id,))
        return _source(rows[0]) if rows else None

    def list_sources(self, kind: str | None = None, include_archived: bool = False) -> list[Source]:
        sql = "SELECT * FROM sources WHERE 1 = 1"
        params: list[Any] = []
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if not include_archived:
            sql += " AND status != 'archived'"
        return [_source(r) for r in self._query(sql + " ORDER BY path, name", params)]

    def propose_source_date(self, source_id: int, date: str) -> None:
        """Record a date found in the document itself; the commissioner confirms on approval."""
        date = validate_date(date)
        with self._tx() as db:
            db.execute(
                """UPDATE sources SET effective_date = ?, date_basis = 'in-document', updated_at = ?
                   WHERE id = ? AND date_basis IN ('none', 'in-document')""",
                (date, now_iso(), source_id),
            )

    def set_source_date(self, source_id: int, date: str) -> None:
        date = validate_date(date)
        with self._tx() as db:
            db.execute(
                """UPDATE sources SET effective_date = ?, date_basis = 'commish-set',
                   updated_at = ? WHERE id = ?""",
                (date, now_iso(), source_id),
            )

    def set_source_basis(self, source_id: int, date: str | None, basis: str) -> None:
        if date is not None:
            date = validate_date(date)
        with self._tx() as db:
            db.execute(
                "UPDATE sources SET effective_date = ?, date_basis = ?, updated_at = ? "
                "WHERE id = ?",
                (date, basis, now_iso(), source_id),
            )

    def mark_undated(self, source_id: int) -> None:
        with self._tx() as db:
            db.execute(
                """UPDATE sources SET effective_date = NULL, date_basis = 'undated',
                   updated_at = ? WHERE id = ?""",
                (now_iso(), source_id),
            )

    def approve_source(self, source_id: int) -> None:
        source = self.get_source(source_id)
        if source.status in ("archived", "failed"):
            raise ReviewError(f"{source.name}: can't approve a {source.status} source")
        if source.date_basis == "none" and source.kind not in UNDATED_KINDS_OK:
            raise ReviewError(f"{source.name}: set a date or mark it undated before approving")
        ts = now_iso()
        with self._tx() as db:
            db.execute(
                "UPDATE sources SET status = 'approved', reviewed_at = ?, updated_at = ? "
                "WHERE id = ?",
                (ts, ts, source_id),
            )
            db.execute(
                """UPDATE records SET status = 'approved', reviewed_at = ?, updated_at = ?
                   WHERE source_id = ? AND status = 'pending_review'""",
                (ts, ts, source_id),
            )

    def reject_source(self, source_id: int, reason: str = "") -> None:
        ts = now_iso()
        with self._tx() as db:
            db.execute(
                """UPDATE sources SET status = 'rejected', status_reason = ?, reviewed_at = ?,
                   updated_at = ? WHERE id = ?""",
                (reason or None, ts, ts, source_id),
            )
            db.execute(
                """UPDATE records SET status = 'rejected', reviewed_at = ?, updated_at = ?
                   WHERE source_id = ? AND status NOT IN ('archived', 'superseded')""",
                (ts, ts, source_id),
            )

    def archive_source(self, source_id: int, reason: str) -> None:
        ts = now_iso()
        with self._tx() as db:
            db.execute(
                "UPDATE sources SET status = 'archived', status_reason = ?, updated_at = ? "
                "WHERE id = ?",
                (reason, ts, source_id),
            )
            db.execute(
                "UPDATE records SET status = 'archived', updated_at = ? "
                "WHERE source_id = ? AND status != 'archived'",
                (ts, source_id),
            )

    # Records

    def replace_records(
        self, source_id: int, new_records: Sequence[NewRecord], approved: bool = False
    ) -> RecordChanges:
        """Make the source's active records match new_records.

        Identity is (section_path, ordinal). Unchanged text keeps its record (and its
        review state); changed text archives the old record and adds a pending one;
        records that disappeared are archived.
        """
        changes = RecordChanges()
        ts = now_iso()
        status = "approved" if approved else "pending_review"
        with self._tx() as db:
            active = {
                (r["section_path"], r["ordinal"]): r
                for r in db.execute(
                    "SELECT * FROM records WHERE source_id = ? AND status != 'archived'",
                    (source_id,),
                ).fetchall()
            }
            seen: set[tuple[str, int]] = set()
            for rec in new_records:
                key = (rec.section_path, rec.ordinal)
                if key in seen:
                    raise ValueError(f"duplicate record identity {key}")
                seen.add(key)
                digest = text_hash(rec.text)
                old = active.get(key)
                if old is not None and old["text_hash"] == digest:
                    changes.unchanged += 1
                    continue
                if old is not None:
                    db.execute(
                        "UPDATE records SET status = 'archived', updated_at = ? WHERE id = ?",
                        (ts, old["id"]),
                    )
                    changes.archived += 1
                db.execute(
                    """INSERT INTO records (source_id, record_type, section_path, ordinal, text,
                       text_hash, season, effective_date, date_basis, status, meta,
                       created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        source_id,
                        rec.record_type,
                        rec.section_path,
                        rec.ordinal,
                        rec.text,
                        digest,
                        rec.season,
                        rec.effective_date,
                        rec.date_basis,
                        status,
                        json.dumps(rec.meta, sort_keys=True),
                        ts,
                        ts,
                    ),
                )
                changes.added += 1
            for key, old in active.items():
                if key not in seen:
                    db.execute(
                        "UPDATE records SET status = 'archived', updated_at = ? WHERE id = ?",
                        (ts, old["id"]),
                    )
                    changes.archived += 1
        return changes

    def get_record(self, record_id: int) -> Record:
        rows = self._query("SELECT * FROM records WHERE id = ?", (record_id,))
        if not rows:
            raise KeyError(f"no record {record_id}")
        return _record(rows[0])

    def records_for_source(self, source_id: int, include_archived: bool = False) -> list[Record]:
        sql = "SELECT * FROM records WHERE source_id = ?"
        if not include_archived:
            sql += " AND status != 'archived'"
        return [_record(r) for r in self._query(sql + " ORDER BY id", (source_id,))]

    def approve_record(self, record_id: int) -> None:
        record = self.get_record(record_id)
        source = self.get_source(record.source_id)
        if source.status != "approved":
            raise ReviewError("approve the source before approving its records")
        if record.status not in ("pending_review", "rejected"):
            raise ReviewError(f"{record.label} is {record.status}")
        ts = now_iso()
        with self._tx() as db:
            db.execute(
                "UPDATE records SET status = 'approved', reviewed_at = ?, updated_at = ? "
                "WHERE id = ?",
                (ts, ts, record_id),
            )

    def reject_record(self, record_id: int) -> None:
        ts = now_iso()
        with self._tx() as db:
            db.execute(
                "UPDATE records SET status = 'rejected', reviewed_at = ?, updated_at = ? "
                "WHERE id = ? AND status != 'archived'",
                (ts, ts, record_id),
            )

    def edit_record_text(self, record_id: int, text: str) -> None:
        """Commissioner correction of a transcription; the record returns to pending."""
        text = text.strip()
        if not text:
            raise ReviewError("record text can't be empty")
        ts = now_iso()
        with self._tx() as db:
            db.execute(
                """UPDATE records SET text = ?, text_hash = ?, status = 'pending_review',
                   updated_at = ? WHERE id = ? AND status != 'archived'""",
                (text, text_hash(text), ts, record_id),
            )

    def citable_records(self) -> list[Record]:
        rows = self._query(
            """SELECT r.* FROM records r JOIN sources s ON s.id = r.source_id
               WHERE r.status = 'approved' AND s.status = 'approved' ORDER BY r.id"""
        )
        return [_record(r) for r in rows]

    # Image transcriptions

    def save_transcription(
        self,
        record_id: int,
        pass_a: dict[str, Any],
        pass_b: dict[str, Any],
        disagreements: list[str],
        model: str,
    ) -> None:
        with self._tx() as db:
            db.execute(
                """INSERT OR REPLACE INTO image_transcriptions
                   (record_id, pass_a, pass_b, disagreements, model, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record_id,
                    json.dumps(pass_a, sort_keys=True),
                    json.dumps(pass_b, sort_keys=True),
                    json.dumps(disagreements),
                    model,
                    now_iso(),
                ),
            )

    def transcription(self, record_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM image_transcriptions WHERE record_id = ?", (record_id,))
        if not rows:
            return None
        row = rows[0]
        return {
            "pass_a": json.loads(row["pass_a"]),
            "pass_b": json.loads(row["pass_b"]),
            "disagreements": json.loads(row["disagreements"]),
            "model": row["model"],
        }

    # Sleeper snapshots and field verification

    def upsert_snapshot(
        self, league_id: str, season: str, previous_league_id: str | None, league: dict[str, Any]
    ) -> bool:
        body = json.dumps(league, sort_keys=True)
        digest = text_hash(body)
        with self._tx() as db:
            row = db.execute(
                "SELECT content_hash FROM sleeper_snapshots WHERE league_id = ?", (league_id,)
            ).fetchone()
            if row is not None and row["content_hash"] == digest:
                return False
            db.execute(
                """INSERT OR REPLACE INTO sleeper_snapshots
                   (league_id, season, previous_league_id, content_hash, league_json, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (league_id, season, previous_league_id, digest, body, now_iso()),
            )
            return True

    def snapshots(self) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM sleeper_snapshots ORDER BY season DESC")
        return [
            {
                "league_id": r["league_id"],
                "season": r["season"],
                "previous_league_id": r["previous_league_id"],
                "fetched_at": r["fetched_at"],
                "league": json.loads(r["league_json"]),
            }
            for r in rows
        ]

    def verified_fields(self) -> set[str]:
        return {r["path"] for r in self._query("SELECT path FROM field_verifications")}

    def verify_field(self, path: str, note: str = "") -> None:
        with self._tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO field_verifications (path, verified_at, note) "
                "VALUES (?, ?, ?)",
                (path, now_iso(), note),
            )
        self.refresh_sleeper_record_status()

    def unverify_field(self, path: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM field_verifications WHERE path = ?", (path,))
        self.refresh_sleeper_record_status()

    def refresh_sleeper_record_status(self) -> None:
        """A Sleeper setting record is citable exactly when its field is verified."""
        verified = self.verified_fields()
        ts = now_iso()
        with self._tx() as db:
            rows = db.execute(
                """SELECT id, status, meta FROM records
                   WHERE record_type = 'sleeper_setting'
                   AND status IN ('approved', 'pending_review')"""
            ).fetchall()
            for row in rows:
                want = (
                    "approved"
                    if json.loads(row["meta"]).get("path") in verified
                    else ("pending_review")
                )
                if want != row["status"]:
                    db.execute(
                        "UPDATE records SET status = ?, updated_at = ? WHERE id = ?",
                        (want, ts, row["id"]),
                    )

    # Sync runs and summaries

    def record_sync_run(self, started_at: str, report: dict[str, Any]) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT INTO sync_runs (started_at, finished_at, report) VALUES (?, ?, ?)",
                (started_at, now_iso(), json.dumps(report, sort_keys=True)),
            )

    def last_sync(self) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1")
        if not rows:
            return None
        return {"finished_at": rows[0]["finished_at"], "report": json.loads(rows[0]["report"])}

    def review_counts(self) -> dict[str, int]:
        pending_sources = self._query(
            "SELECT COUNT(*) AS n FROM sources WHERE status = 'pending_review'"
        )[0]["n"]
        pending_records = self._query(
            "SELECT COUNT(*) AS n FROM records WHERE status = 'pending_review'"
        )[0]["n"]
        citable = self._query(
            """SELECT COUNT(*) AS n FROM records r JOIN sources s ON s.id = r.source_id
               WHERE r.status = 'approved' AND s.status = 'approved'"""
        )[0]["n"]
        failed = self._query("SELECT COUNT(*) AS n FROM sources WHERE status = 'failed'")[0]["n"]
        return {
            "pending_sources": pending_sources,
            "pending_records": pending_records,
            "citable_records": citable,
            "failed_sources": failed,
        }
