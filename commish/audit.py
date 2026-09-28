"""Append-only JSONL event log.

Only messages addressed to the bot are logged with sender/text; ignored messages are
logged by GUID and reason only, so ordinary group chatter is never stored.
"""

import json
import threading
import time
from pathlib import Path
from typing import Any


class EventLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event: str, **fields: Any) -> None:
        record = {"ts_ms": int(time.time() * 1000), "event": event, **fields}
        line = json.dumps(record, ensure_ascii=False)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
