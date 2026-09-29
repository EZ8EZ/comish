import re

import pytest
from fastapi.testclient import TestClient

from comish.admin.app import create_admin_app
from tests.admin_seed import LEAGUE, PASSWORD, seed


@pytest.fixture
def setup(tmp_path):
    store = seed(tmp_path)
    app = create_admin_app({LEAGUE.slug: LEAGUE}, lambda slug: store, PASSWORD)
    client = TestClient(app, follow_redirects=False)
    yield client, store
    store.close()


AUTH = ("admin", PASSWORD)


def csrf(client):
    page = client.get("/l/football/fields", auth=AUTH).text
    return re.search(r'name="csrf" value="([0-9a-f]+)"', page).group(1)


def test_every_page_requires_auth(setup):
    client, store = setup
    doc = store.source_by_external_id("doc1")
    for url in ["/", "/l/football", f"/l/football/s/{doc.id}", "/l/football/fields"]:
        assert client.get(url).status_code == 401
        assert client.get(url, auth=("admin", "wrong")).status_code == 401
        assert client.get(url, auth=AUTH).status_code == 200


def test_posts_require_csrf(setup):
    client, store = setup
    doc = store.source_by_external_id("doc1")
    resp = client.post(f"/l/football/s/{doc.id}/undated", data={"csrf": "bad"}, auth=AUTH)
    assert resp.status_code == 403
    assert store.get_source(doc.id).date_basis == "none"


def test_unknown_league_is_404(setup):
    client, _ = setup
    assert client.get("/l/other", auth=AUTH).status_code == 404


def test_approval_without_date_shows_error(setup):
    client, store = setup
    doc = store.source_by_external_id("doc1")
    resp = client.post(f"/l/football/s/{doc.id}/approve", data={"csrf": csrf(client)}, auth=AUTH)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    assert store.citable_records() == []


def test_file_route_rejects_traversal(setup):
    client, _ = setup
    assert client.get("/l/football/file/..%2Fleague.db", auth=AUTH).status_code == 404
    assert client.get("/l/football/file/img1.png", auth=AUTH).status_code == 200


def test_verify_rejects_unknown_field(setup):
    client, _ = setup
    resp = client.post(
        "/l/football/fields/verify",
        data={"csrf": csrf(client), "path": "settings.not_real"},
        auth=AUTH,
    )
    assert resp.status_code == 400
