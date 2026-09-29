"""Opt-in smoke test against the real Sleeper API: `uv run pytest -m live`."""

import pytest

from comish.ingest.sleeper import SleeperClient, sync_sleeper
from comish.kb.store import LeagueStore

# The example league from Sleeper's own API documentation.
DOCS_EXAMPLE_LEAGUE = "289646328504385536"


@pytest.mark.live
def test_real_chain_syncs(tmp_path):
    store = LeagueStore(tmp_path, "live")
    report = sync_sleeper(store, SleeperClient(), DOCS_EXAMPLE_LEAGUE, "nfl")
    assert report.seasons and report.records_added > 0
    again = sync_sleeper(store, SleeperClient(), DOCS_EXAMPLE_LEAGUE, "nfl")
    assert (again.records_added, again.records_archived) == (0, 0)
    store.close()
