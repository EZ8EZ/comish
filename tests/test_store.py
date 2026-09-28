import pytest

from comish.kb.store import LeagueStore, NewRecord, ReviewError


@pytest.fixture
def store(tmp_path):
    s = LeagueStore(tmp_path, "football")
    yield s
    s.close()


def _doc(store, external_id="doc-1", content_hash="h1"):
    source, _ = store.upsert_source(
        kind="gdoc", external_id=external_id, name="Constitution", content_hash=content_hash
    )
    return source


def _sections(*texts):
    return [NewRecord("doc_section", f"Constitution > S{i}", t) for i, t in enumerate(texts)]


def test_migrations_are_idempotent(tmp_path):
    LeagueStore(tmp_path, "football").close()
    store = LeagueStore(tmp_path, "football")
    assert store.list_sources() == []
    store.close()


def test_records_not_citable_until_approved(store):
    source = _doc(store)
    store.replace_records(source.id, _sections("Trades close at the deadline."))
    assert store.citable_records() == []


def test_doc_needs_date_decision_before_approval(store):
    source = _doc(store)
    store.replace_records(source.id, _sections("A rule."))
    with pytest.raises(ReviewError, match="set a date or mark it undated"):
        store.approve_source(source.id)

    store.mark_undated(source.id)
    store.approve_source(source.id)
    (record,) = store.citable_records()
    assert record.text == "A rule."
    assert store.get_source(source.id).date_basis == "undated"


def test_proposed_in_document_date_is_confirmed_by_approval(store):
    source = _doc(store)
    store.propose_source_date(source.id, "2025-02-01")
    store.approve_source(source.id)
    approved = store.get_source(source.id)
    assert (approved.effective_date, approved.date_basis) == ("2025-02-01", "in-document")


def test_commissioner_date_overrides_and_is_not_overwritten_by_proposals(store):
    source = _doc(store)
    store.set_source_date(source.id, "2024-08-10")
    store.propose_source_date(source.id, "2020-01-01")
    got = store.get_source(source.id)
    assert (got.effective_date, got.date_basis) == ("2024-08-10", "commish-set")


def test_bad_date_rejected(store):
    source = _doc(store)
    with pytest.raises(ReviewError):
        store.set_source_date(source.id, "08/10/2024")


def test_unchanged_resync_keeps_review_state(store):
    source = _doc(store)
    store.replace_records(source.id, _sections("One.", "Two."))
    store.mark_undated(source.id)
    store.approve_source(source.id)
    changes = store.replace_records(source.id, _sections("One.", "Two."))
    assert (changes.added, changes.unchanged, changes.archived) == (0, 2, 0)
    assert len(store.citable_records()) == 2


def test_changed_section_goes_back_to_pending(store):
    source = _doc(store)
    store.replace_records(source.id, _sections("One.", "Two."))
    store.mark_undated(source.id)
    store.approve_source(source.id)

    changes = store.replace_records(source.id, _sections("One.", "Two, amended."))
    assert (changes.added, changes.unchanged, changes.archived) == (1, 1, 1)
    assert [r.text for r in store.citable_records()] == ["One."]
    pending = [r for r in store.records_for_source(source.id) if r.status == "pending_review"]
    assert [r.text for r in pending] == ["Two, amended."]

    store.approve_record(pending[0].id)
    assert {r.text for r in store.citable_records()} == {"One.", "Two, amended."}


def test_removed_section_is_archived(store):
    source = _doc(store)
    store.replace_records(source.id, _sections("One.", "Two."))
    changes = store.replace_records(source.id, _sections("One."))
    assert changes.archived == 1
    assert [r.text for r in store.records_for_source(source.id)] == ["One."]


def test_rejected_record_is_never_citable(store):
    source = _doc(store)
    store.replace_records(source.id, _sections("Keep.", "Wrong."))
    store.mark_undated(source.id)
    store.approve_source(source.id)
    wrong = next(r for r in store.records_for_source(source.id) if r.text == "Wrong.")
    store.reject_record(wrong.id)
    assert [r.text for r in store.citable_records()] == ["Keep."]
    # Re-approving the source doesn't resurrect a rejected record.
    store.approve_source(source.id)
    assert [r.text for r in store.citable_records()] == ["Keep."]


def test_rejected_source_hides_all_records(store):
    source = _doc(store)
    store.replace_records(source.id, _sections("One."))
    store.mark_undated(source.id)
    store.approve_source(source.id)
    store.reject_source(source.id, "stale doc")
    assert store.citable_records() == []


def test_record_approval_requires_approved_source(store):
    source = _doc(store)
    store.replace_records(source.id, _sections("One."))
    (record,) = store.records_for_source(source.id)
    with pytest.raises(ReviewError, match="approve the source"):
        store.approve_record(record.id)


def test_edit_returns_record_to_pending(store):
    source, _ = store.upsert_source(kind="image", external_id="img-1", name="IMG_1.png")
    store.replace_records(source.id, [NewRecord("vote", "IMG_1.png", "Vote passed 8-4.")])
    store.set_source_basis(source.id, "2024-08-10", "visible-in-image")
    store.approve_source(source.id)
    (record,) = store.citable_records()
    store.edit_record_text(record.id, "Vote passed 9-3.")
    assert store.citable_records() == []
    store.approve_record(record.id)
    assert store.citable_records()[0].text == "Vote passed 9-3."


def test_upsert_reports_content_change(store):
    _, changed = store.upsert_source(kind="gdoc", external_id="d", name="D", content_hash="a")
    assert changed
    _, changed = store.upsert_source(kind="gdoc", external_id="d", name="D", content_hash="a")
    assert not changed
    _, changed = store.upsert_source(kind="gdoc", external_id="d", name="D", content_hash="b")
    assert changed


def test_archived_source_cannot_be_approved(store):
    source = _doc(store)
    store.archive_source(source.id, "deleted from Drive")
    with pytest.raises(ReviewError, match="archived"):
        store.approve_source(source.id)


def test_sleeper_records_follow_field_verification(store):
    source, _ = store.upsert_source(
        kind="sleeper", external_id="sleeper:1", name="Sleeper", status="approved"
    )
    store.set_source_basis(source.id, None, "sleeper-season")
    store.replace_records(
        source.id,
        [
            NewRecord(
                "sleeper_setting",
                "2025 > settings.trade_deadline",
                "Trade deadline = week 11",
                meta={"path": "settings.trade_deadline"},
            )
        ],
    )
    store.refresh_sleeper_record_status()
    assert store.citable_records() == []
    store.verify_field("settings.trade_deadline")
    assert len(store.citable_records()) == 1
    store.unverify_field("settings.trade_deadline")
    assert store.citable_records() == []


def test_snapshot_change_detection(store):
    assert store.upsert_snapshot("1", "2025", None, {"a": 1})
    assert not store.upsert_snapshot("1", "2025", None, {"a": 1})
    assert store.upsert_snapshot("1", "2025", None, {"a": 2})
    assert store.snapshots()[0]["league"] == {"a": 2}


def test_leagues_are_isolated(tmp_path):
    football = LeagueStore(tmp_path, "football")
    hoops = LeagueStore(tmp_path, "hoops")
    source = _doc(football)
    football.replace_records(source.id, _sections("Football only."))
    football.mark_undated(source.id)
    football.approve_source(source.id)
    assert len(football.citable_records()) == 1
    assert hoops.citable_records() == []
    assert hoops.list_sources() == []
    assert football.path != hoops.path
    football.close()
    hoops.close()


def test_invalid_slug_cannot_escape_data_dir(tmp_path):
    with pytest.raises(ValueError):
        LeagueStore(tmp_path, "../other")


def test_proposal_never_overrides_approved_or_commissioner_dates(store):
    source = _doc(store)
    assert store.propose_source_date(source.id, "2025-01-01")
    store.approve_source(source.id)
    assert not store.propose_source_date(source.id, "2026-01-01")
    assert store.get_source(source.id).effective_date == "2025-01-01"
    assert store.propose_source_date(source.id, "2025-01-01")  # same date is fine
