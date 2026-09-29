"""Cross-league operations state: flag numbering and pending ruling confirmations.

Flag numbers (F17) are unique across leagues so a commissioner running two leagues can
reply "rule F17 ..." without naming the league. This database holds only which league
each flag belongs to and unconfirmed commands; the question and the ruling itself live
in that league's own database, so league knowledge never mixes.
"""

import sqlite3
import threading
from pathlib import Path
from typing import Any

from comish.kb.store import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS flags (
    id INTEGER PRIMARY KEY,
    league TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_rulings (
    handle TEXT PRIMARY KEY,
    flag_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


class OpsStore:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(data_dir / "ops.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    self._db.execute(statement)

    def close(self) -> None:
        self._db.close()

    def new_flag(self, league: str) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO flags (league, created_at) VALUES (?, ?)", (league, now_iso())
            )
            return int(cur.lastrowid or 0)

    def flag_league(self, flag_id: int) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT league FROM flags WHERE id = ?", (flag_id,)).fetchone()
        return str(row["league"]) if row else None

    def set_pending(self, handle: str, flag_id: int, text: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO pending_rulings (handle, flag_id, text, created_at) "
                "VALUES (?, ?, ?, ?)",
                (handle, flag_id, text, now_iso()),
            )

    def pop_pending(self, handle: str) -> dict[str, Any] | None:
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT * FROM pending_rulings WHERE handle = ?", (handle,)
            ).fetchone()
            if row is None:
                return None
            self._db.execute("DELETE FROM pending_rulings WHERE handle = ?", (handle,))
            return dict(row)
