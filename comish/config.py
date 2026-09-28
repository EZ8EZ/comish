"""Non-secret runtime settings, read from COMISH_* environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _csv(value: str) -> frozenset[str]:
    return frozenset(v.strip() for v in value.split(",") if v.strip())


@dataclass(frozen=True)
class Settings:
    bluebubbles_url: str = "http://127.0.0.1:1234"
    # "apple-script" (SIP on) or "private-api" (SIP off, BlueBubbles helper installed).
    send_method: str = "apple-script"
    # Only these chat GUIDs are ever answered. Empty means the bot answers nowhere.
    allowed_chat_guids: frozenset[str] = field(default_factory=frozenset)
    max_per_sender_per_10min: int = 20
    max_outbound_per_day: int = 100
    log_dir: Path = Path("logs")
    # Per-league databases, downloaded files and leagues.yaml. Gitignored.
    data_dir: Path = Path("data")

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            bluebubbles_url=env.get("COMISH_BLUEBUBBLES_URL", cls.bluebubbles_url).rstrip("/"),
            send_method=env.get("COMISH_SEND_METHOD", cls.send_method),
            allowed_chat_guids=_csv(env.get("COMISH_ALLOWED_CHAT_GUIDS", "")),
            max_per_sender_per_10min=int(
                env.get("COMISH_MAX_PER_SENDER_PER_10MIN", cls.max_per_sender_per_10min)
            ),
            max_outbound_per_day=int(
                env.get("COMISH_MAX_OUTBOUND_PER_DAY", cls.max_outbound_per_day)
            ),
            log_dir=Path(env.get("COMISH_LOG_DIR", str(cls.log_dir))),
            data_dir=Path(env.get("COMISH_DATA_DIR", str(cls.data_dir))),
        )

    @property
    def leagues_path(self) -> Path:
        return self.data_dir / "leagues.yaml"
