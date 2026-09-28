import pytest

from comish.ingest import drive as d
from comish.ingest.images import Transcript, TranscriptionError
from comish.ingest.pdfs import extract_pages
from comish.ingest.sheets import parse_workbook
from comish.ingest.sync import DriveSync
from comish.kb.store import LeagueStore
from tests.drive_fixtures import FakeDrive, minimal_pdf, workbook

CONSTITUTION = b"""League Constitution
Last updated: 2025-02-01

# Trades
Trades close at the Sleeper trade deadline.

# Taxi
Two taxi slots per team.
"""


def fake_transcribe(data: bytes, mime: str, name: str) -> Transcript:
    return Transcript(
        text=f"Vote from {name}: taxi expansion passed 8-4.",
        proposed_date="2024-08-10",
        pass_a={"outcome": "passed", "vote_for": 8},
        pass_b={"outcome": "passed", "vote_for": 9},
        disagreements=["vote_for"],
        model="fake-model",
    )


@pytest.fixture
def store(tmp_path):
    s = LeagueStore(tmp_path, "football")
    yield s
    s.close()


@pytest.fixture
def drive():
    fake = FakeDrive()
    fake.add_file("root", "doc1", "Constitution", d.GDOC, {d.MARKDOWN: CONSTITUTION})
    fake.add_file(
        "root",
        "sheet1",
        "Dues Tracker",
        d.GSHEET,
        {d.XLSX: workbook({"2026": [["Team", "Paid"], ["Hawks", "yes"], [], ["Owls", "no"]]})},
    )
    fake.add_folder("root", "sub", "24-25")
    fake.add_file("sub", "img1", "IMG_1.png", "image/png", {"raw": b"png-bytes"}, md5="m1")
    fake.add_file(
        "sub", "pdf1", "Old Rules.pdf", d.PDF, {"raw": minimal_pdf(["Old rule text."])}, md5="p1"
    )
    fake.add_file("root", "vid", "highlight.mov", "video/quicktime", {"raw": b""})
    return fake


def _statuses(report):
    return {f.path: f.status for f in report.files}


def test_first_sync_accounts_for_every_file(store, drive):
    report = DriveSync(store, drive, fake_transcribe).run("root")
    assert _statuses(report) == {
        "Constitution": "ingested",
        "Dues Tracker": "ingested",
        "24-25/IMG_1.png": "ingested",
        "24-25/Old Rules.pdf": "ingested",
        "highlight.mov": "skipped",
    }
    # Nothing is citable until the commissioner reviews it.
    assert store.citable_records() == []


def test_second_sync_is_idempotent(store, drive):
    DriveSync(store, drive, fake_transcribe).run("root")
    drive.downloads.clear()
    report = DriveSync(store, drive, fake_transcribe).run("root")
    assert set(_statuses(report).values()) == {"unchanged", "skipped"}
    # Unchanged binaries (same md5) aren't downloaded or re-transcribed.
    assert drive.downloads == []


def test_doc_date_is_proposed_not_approved(store, drive):
    DriveSync(store, drive, fake_transcribe).run("root")
    doc = store.source_by_external_id("doc1")
    assert (doc.effective_date, doc.date_basis, doc.status) == (
        "2025-02-01",
        "in-document",
        "pending_review",
    )
    assert doc.date_hint == "2026-01-01T00:00:00Z"  # modified time is only a hint


def test_image_transcription_is_stored_for_review(store, drive, tmp_path):
    DriveSync(store, drive, fake_transcribe).run("root")
    image = store.source_by_external_id("img1")
    assert (image.effective_date, image.date_basis) == ("2024-08-10", "visible-in-image")
    (record,) = store.records_for_source(image.id)
    assert record.status == "pending_review"
    assert record.text == "Vote from IMG_1.png: taxi expansion passed 8-4."
    saved = store.transcription(record.id)
    assert saved["disagreements"] == ["vote_for"]
    assert (store.dir / "files" / "img1.png").read_bytes() == b"png-bytes"


def test_sheet_rows_use_headers_and_skip_blanks(store, drive):
    DriveSync(store, drive, fake_transcribe).run("root")
    sheet = store.source_by_external_id("sheet1")
    texts = {r.section_path: r.text for r in store.records_for_source(sheet.id)}
    assert texts == {
        "Dues Tracker > 2026 > row 2": "Team: Hawks; Paid: yes",
        "Dues Tracker > 2026 > row 4": "Team: Owls; Paid: no",
    }


def test_edited_doc_section_goes_back_to_pending(store, drive):
    DriveSync(store, drive, fake_transcribe).run("root")
    doc = store.source_by_external_id("doc1")
    store.approve_source(doc.id)
    assert len(store.citable_records()) == 3

    edited = CONSTITUTION.replace(b"Two taxi slots", b"Three taxi slots")
    drive.add_file("root", "doc1", "Constitution", d.GDOC, {d.MARKDOWN: edited})
    report = DriveSync(store, drive, fake_transcribe).run("root")
    assert _statuses(report)["Constitution"] == "changed"
    citable = [r.text for r in store.citable_records()]
    assert "Two taxi slots per team." not in citable
    assert "Three taxi slots per team." not in citable  # pending until re-approved
    assert "Trades close at the Sleeper trade deadline." in citable


def test_removed_file_is_archived(store, drive):
    DriveSync(store, drive, fake_transcribe).run("root")
    doc = store.source_by_external_id("doc1")
    store.approve_source(doc.id)
    drive.remove("root", "doc1")
    report = DriveSync(store, drive, fake_transcribe).run("root")
    assert report.archived == ["Constitution"]
    assert store.citable_records() == []


def test_transcription_failure_is_reported_and_retried(store, drive):
    def broken(data, mime, name):
        raise TranscriptionError("quota exceeded")

    report = DriveSync(store, drive, broken).run("root")
    result = next(f for f in report.files if f.path == "24-25/IMG_1.png")
    assert (result.status, result.detail) == ("failed", "transcription failed: quota exceeded")
    assert store.source_by_external_id("img1").status == "failed"

    retry = DriveSync(store, drive, fake_transcribe).run("root")
    assert _statuses(retry)["24-25/IMG_1.png"] in ("ingested", "changed")
    assert store.source_by_external_id("img1").status == "pending_review"


def test_no_llm_configured_marks_images_failed(store, drive):
    report = DriveSync(store, drive, None).run("root")
    result = next(f for f in report.files if f.path == "24-25/IMG_1.png")
    assert result.status == "failed" and "no LLM configured" in result.detail


def test_scanned_pdf_is_reported_not_guessed(store):
    drive = FakeDrive()
    drive.add_file("root", "scan", "Scan.pdf", d.PDF, {"raw": minimal_pdf([""])}, md5="s")
    report = DriveSync(store, drive, None).run("root")
    (result,) = report.files
    assert result.status == "failed" and "scanned PDF" in result.detail


def test_pdf_pages_extract():
    pages = extract_pages(minimal_pdf(["Page one.", "", "Page three."]))
    assert pages.pages == [(1, "Page one."), (3, "Page three.")]
    assert pages.empty_pages == [2]


def test_workbook_includes_every_tab():
    rows = parse_workbook("Tracker", workbook({"A": [["h"], ["1"]], "B": [["h"], ["2"]]}))
    assert [r.path for r in rows] == ["Tracker > A > row 2", "Tracker > B > row 2"]


def test_google_client_rejects_bad_key_json():
    with pytest.raises(d.DriveError, match="not valid JSON"):
        d.GoogleDriveClient("{not json")


def test_walk_handles_nesting_and_cycles():
    fake = FakeDrive()
    fake.add_folder("root", "a", "A")
    fake.add_folder("a", "b", "B")
    fake.children["b"].append({"id": "a", "name": "A-again", "mimeType": d.FOLDER})
    fake.add_file("b", "f", "deep.png", "image/png", {"raw": b""})
    assert [f.path for f in d.walk(fake, "root")] == ["A/B/deep.png"]
