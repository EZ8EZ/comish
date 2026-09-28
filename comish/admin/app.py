"""Commissioner review UI.

A small server-rendered app (FastAPI + Jinja2, plain HTML forms, no JavaScript). It
binds to localhost and is reached over Tailscale. Every page needs HTTP Basic auth
(password from the Keychain as `admin_password`), and every form carries a CSRF token.

All review rules live in LeagueStore; this layer only calls it and shows the result,
so the UI can't approve anything the rules don't allow.
"""

import hashlib
import hmac
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from jinja2 import Environment, PackageLoader, select_autoescape

from comish.ingest.fields import load_fields
from comish.kb.store import LeagueStore, ReviewError
from comish.leagues import League

SAFE_FILE_RE = re.compile(r"^[A-Za-z0-9_-]+\.[a-z0-9]{2,5}$")

security = HTTPBasic()


def create_admin_app(
    leagues: dict[str, League],
    open_store: Callable[[str], LeagueStore],
    password: str,
) -> FastAPI:
    app = FastAPI(title="Comish admin", docs_url=None, redoc_url=None, openapi_url=None)
    env = Environment(
        loader=PackageLoader("comish.admin", "templates"),
        autoescape=select_autoescape(["html"]),
    )
    csrf_secret = secrets.token_bytes(32)
    csrf_token = hmac.new(csrf_secret, b"comish-admin", hashlib.sha256).hexdigest()
    stores: dict[str, LeagueStore] = {}

    def auth(creds: Annotated[HTTPBasicCredentials, Depends(security)]) -> None:
        if not hmac.compare_digest(creds.password.encode(), password.encode()):
            raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})

    def league_store(slug: str) -> tuple[League, LeagueStore]:
        league = leagues.get(slug)
        if league is None:
            raise HTTPException(404, "unknown league")
        if slug not in stores:
            stores[slug] = open_store(slug)
        return league, stores[slug]

    def render(name: str, request: Request, **context: Any) -> HTMLResponse:
        template = env.get_template(name)
        return HTMLResponse(
            template.render(
                csrf=csrf_token,
                error=request.query_params.get("error"),
                notice=request.query_params.get("notice"),
                **context,
            )
        )

    def back(url: str, *, error: str | None = None, notice: str | None = None) -> RedirectResponse:
        if error:
            url += f"?error={quote(error)}"
        elif notice:
            url += f"?notice={quote(notice)}"
        return RedirectResponse(url, status_code=303)

    def check_csrf(token: str) -> None:
        if not hmac.compare_digest(token, csrf_token):
            raise HTTPException(403, "bad CSRF token")

    Auth = Depends(auth)
    Csrf = Annotated[str, Form()]

    @app.get("/", response_class=HTMLResponse, dependencies=[Auth])
    def dashboard(request: Request) -> HTMLResponse:
        rows = []
        for slug, league in leagues.items():
            _, store = league_store(slug)
            rows.append(
                {"league": league, "counts": store.review_counts(), "sync": store.last_sync()}
            )
        return render("dashboard.html", request, rows=rows)

    @app.get("/l/{slug}", response_class=HTMLResponse, dependencies=[Auth])
    def league_page(request: Request, slug: str) -> HTMLResponse:
        league, store = league_store(slug)
        items = [s for s in store.list_sources() if s.kind != "sleeper"]
        order = {"failed": 0, "pending_review": 1, "rejected": 2, "approved": 3}
        items.sort(key=lambda s: (order.get(s.status, 9), s.path))
        sources = [{"source": s, "records": store.records_for_source(s.id)} for s in items]
        return render(
            "league.html", request, league=league, sources=sources, counts=store.review_counts()
        )

    @app.get("/l/{slug}/s/{source_id}", response_class=HTMLResponse, dependencies=[Auth])
    def source_page(request: Request, slug: str, source_id: int) -> HTMLResponse:
        league, store = league_store(slug)
        try:
            source = store.get_source(source_id)
        except KeyError as exc:
            raise HTTPException(404) from exc
        records = store.records_for_source(source_id)
        transcripts = {r.id: store.transcription(r.id) for r in records}
        return render(
            "source.html",
            request,
            league=league,
            source=source,
            records=records,
            transcripts=transcripts,
        )

    def source_action(slug: str, source_id: int, action: Callable[[LeagueStore], None]) -> Any:
        _, store = league_store(slug)
        url = f"/l/{slug}/s/{source_id}"
        try:
            action(store)
        except ReviewError as exc:
            return back(url, error=str(exc))
        return back(url, notice="Saved.")

    @app.post("/l/{slug}/s/{source_id}/date", dependencies=[Auth])
    def set_date(slug: str, source_id: int, csrf: Csrf, date: Annotated[str, Form()]) -> Any:
        check_csrf(csrf)
        return source_action(slug, source_id, lambda s: s.set_source_date(source_id, date))

    @app.post("/l/{slug}/s/{source_id}/undated", dependencies=[Auth])
    def mark_undated(slug: str, source_id: int, csrf: Csrf) -> Any:
        check_csrf(csrf)
        return source_action(slug, source_id, lambda s: s.mark_undated(source_id))

    @app.post("/l/{slug}/s/{source_id}/approve", dependencies=[Auth])
    def approve_source(slug: str, source_id: int, csrf: Csrf) -> Any:
        check_csrf(csrf)
        return source_action(slug, source_id, lambda s: s.approve_source(source_id))

    @app.post("/l/{slug}/s/{source_id}/reject", dependencies=[Auth])
    def reject_source(
        slug: str, source_id: int, csrf: Csrf, reason: Annotated[str, Form()] = ""
    ) -> Any:
        check_csrf(csrf)
        return source_action(slug, source_id, lambda s: s.reject_source(source_id, reason))

    def record_action(slug: str, record_id: int, action: Callable[[LeagueStore], None]) -> Any:
        _, store = league_store(slug)
        try:
            record = store.get_record(record_id)
        except KeyError as exc:
            raise HTTPException(404) from exc
        url = f"/l/{slug}/s/{record.source_id}"
        try:
            action(store)
        except ReviewError as exc:
            return back(url, error=str(exc))
        return back(url, notice="Saved.")

    @app.post("/l/{slug}/r/{record_id}/approve", dependencies=[Auth])
    def approve_record(slug: str, record_id: int, csrf: Csrf) -> Any:
        check_csrf(csrf)
        return record_action(slug, record_id, lambda s: s.approve_record(record_id))

    @app.post("/l/{slug}/r/{record_id}/reject", dependencies=[Auth])
    def reject_record(slug: str, record_id: int, csrf: Csrf) -> Any:
        check_csrf(csrf)
        return record_action(slug, record_id, lambda s: s.reject_record(record_id))

    @app.post("/l/{slug}/r/{record_id}/edit", dependencies=[Auth])
    def edit_record(slug: str, record_id: int, csrf: Csrf, text: Annotated[str, Form()]) -> Any:
        check_csrf(csrf)
        return record_action(slug, record_id, lambda s: s.edit_record_text(record_id, text))

    @app.get("/l/{slug}/fields", response_class=HTMLResponse, dependencies=[Auth])
    def fields_page(request: Request, slug: str) -> HTMLResponse:
        league, store = league_store(slug)
        snapshots = store.snapshots()
        verified = store.verified_fields()
        rows = []
        for f in load_fields(league.sport):
            values = []
            for snap in snapshots:
                raw = f.lookup(snap["league"])
                values.append(
                    {"season": snap["season"], "value": None if raw is None else f.render(raw)}
                )
            if any(v["value"] is not None for v in values):
                rows.append({"field": f, "cells": values, "verified": f.path in verified})
        seasons = [s["season"] for s in snapshots]
        return render("fields.html", request, league=league, rows=rows, seasons=seasons)

    @app.post("/l/{slug}/fields/verify", dependencies=[Auth])
    def verify_field(slug: str, csrf: Csrf, path: Annotated[str, Form()]) -> Any:
        check_csrf(csrf)
        league, store = league_store(slug)
        if path not in {f.path for f in load_fields(league.sport)}:
            raise HTTPException(400, "unknown field")
        store.verify_field(path)
        return back(f"/l/{slug}/fields", notice=f"Verified {path}.")

    @app.post("/l/{slug}/fields/unverify", dependencies=[Auth])
    def unverify_field(slug: str, csrf: Csrf, path: Annotated[str, Form()]) -> Any:
        check_csrf(csrf)
        _, store = league_store(slug)
        store.unverify_field(path)
        return back(f"/l/{slug}/fields", notice=f"Unverified {path}.")

    @app.get("/l/{slug}/file/{name}", dependencies=[Auth])
    def image_file(slug: str, name: str) -> FileResponse:
        _, store = league_store(slug)
        if not SAFE_FILE_RE.match(name):
            raise HTTPException(404)
        path: Path = store.dir / "files" / name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    return app
