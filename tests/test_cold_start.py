"""First run: an empty data directory, no leagues file and no secrets.

Nothing may crash or invent state. Commands report an empty setup and exit cleanly,
the admin UI loads with zero leagues, and the server answers its health check.
"""

import pytest

from comish.admin.app import create_admin_app
from comish.audit import EventLog
from comish.cli import main
from comish.config import Settings
from comish.server import create_app
from tests.servers import serve


@pytest.fixture
def empty_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COMISH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COMISH_LOG_DIR", str(tmp_path / "logs"))
    return tmp_path


def test_league_list_is_empty_not_an_error(empty_env, capsys):
    assert main(["league", "list"]) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "argv",
    [
        ["review-status", "football"],
        ["ask", "football", "what is the trade deadline?"],
        ["sync", "football"],
        ["verify-field", "football", "settings.trade_deadline"],
    ],
)
def test_commands_on_an_unconfigured_league_fail_cleanly(empty_env, capsys, argv):
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert "unknown league 'football' (configured: none)" in captured.err
    assert "Traceback" not in captured.err
    # A mistyped slug must not leave a database directory behind.
    assert not (empty_env / "data" / "football").exists()


def test_admin_dashboard_loads_with_no_leagues(empty_env):
    import httpx

    app = create_admin_app({}, lambda slug: None, "pw")
    with serve(app) as url:
        resp = httpx.get(url, auth=("admin", "pw"))
    assert resp.status_code == 200


def test_pong_server_starts_and_reports_healthy(empty_env):
    import httpx

    class NoSend:
        async def send_text(self, chat_guid, text):
            raise AssertionError("nothing should be sent")

    app = create_app(Settings.from_env(), NoSend(), "tok", EventLog(empty_env / "logs" / "e.jsonl"))
    with serve(app) as url:
        assert httpx.get(f"{url}/healthz").json() == {"status": "ok"}
