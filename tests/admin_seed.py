"""A seeded league for admin UI tests: an undated doc, a disputed screenshot, Sleeper data."""

from pathlib import Path

from comish.ingest.images import Transcript
from comish.ingest.sleeper import SleeperClient, sync_sleeper
from comish.ingest.sync import DriveSync
from comish.kb.store import LeagueStore
from comish.leagues import League
from tests.drive_fixtures import FakeDrive
from tests.sleeper_fixtures import FakeSleeper

PASSWORD = "test-admin-password"
LEAGUE = League(slug="football", name="Test Dynasty", sport="nfl", sleeper_league_id="300")

DOC = b"""# Trades
Trades close at the Sleeper trade deadline.

# Taxi
Two taxi slots per team.
"""

# A tiny valid 1x1 PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def transcribe(data: bytes, mime: str, name: str) -> Transcript:
    return Transcript(
        text=f"Screenshot {name}.\nDecision: Expand taxi to 3.\nOutcome: passed",
        proposed_date=None,
        pass_a={"decision": "Expand taxi to 3.", "outcome": "passed", "votes_for": 8},
        pass_b={"decision": "Expand taxi to 3.", "outcome": "passed", "votes_for": 9},
        disagreements=["votes_for"],
        model="fake",
    )


def seed(data_dir: Path) -> LeagueStore:
    from comish.ingest import drive as d

    store = LeagueStore(data_dir, LEAGUE.slug)
    drive = FakeDrive()
    drive.add_file("root", "doc1", "Constitution", d.GDOC, {d.MARKDOWN: DOC})
    drive.add_file("root", "img1", "IMG_1.png", "image/png", {"raw": PNG}, md5="m1")
    DriveSync(store, drive, transcribe).run("root")
    fake = FakeSleeper()
    sync_sleeper(store, SleeperClient(fake.client(), min_interval_s=0), "300", "nfl")
    return store
