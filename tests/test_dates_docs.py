import datetime as dt

import pytest

from comish.ingest.dates import latest_marked_date, marked_dates, parse_date
from comish.ingest.docs import MAX_SECTION_CHARS, clean_markdown, document_date, split_markdown


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2024-08-10", dt.date(2024, 8, 10)),
        ("8/10/2024", dt.date(2024, 8, 10)),
        ("8/10/24", dt.date(2024, 8, 10)),
        ("Aug 10, 2024", dt.date(2024, 8, 10)),
        ("August 10th, 2024", dt.date(2024, 8, 10)),
        ("Sept. 3 2025", dt.date(2025, 9, 3)),
        ("10 August 2024", dt.date(2024, 8, 10)),
        ("no date here", None),
        ("2/30/2024", None),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_marked_dates_need_a_marker():
    assert marked_dates("We met on 2024-08-10 to talk.") == []
    assert marked_dates("Amended: Aug 10, 2024") == [dt.date(2024, 8, 10)]
    assert marked_dates("Last updated on 2025-02-01") == [dt.date(2025, 2, 1)]
    assert marked_dates("Effective date - 3/1/2025") == [dt.date(2025, 3, 1)]


def test_latest_marked_date_wins():
    text = "Adopted 2023-06-01. Amended 2024-08-10. Amended 2025-02-01."
    assert latest_marked_date(text) == dt.date(2025, 2, 1)


def test_clean_markdown_unescapes_and_strips_emphasis():
    assert clean_markdown(r"Taxi \- **max 2** players\.") == "Taxi - max 2 players."


DOC = """Dynasty Constitution
Last updated: 2025-02-01

# Trades

## 4.1 Deadline
Trades close at the Sleeper trade deadline.

## 4.2 Taxi squad (amended Aug 10, 2024)
Taxi squad players may only be traded in the offseason.

# Dues

Dues are \\$100 per season.

# Dues

A second Dues heading.
"""


def test_split_markdown_paths_and_dates():
    sections = split_markdown("Constitution", DOC)
    by_path = {(s.path, s.ordinal): s for s in sections}
    assert by_path[("Constitution", 0)].text.startswith("Dynasty Constitution")
    deadline = by_path[("Constitution > Trades > 4.1 Deadline", 0)]
    assert deadline.text == "Trades close at the Sleeper trade deadline."
    assert deadline.proposed_date is None
    taxi = by_path[("Constitution > Trades > 4.2 Taxi squad (amended Aug 10, 2024)", 0)]
    assert taxi.proposed_date == "2024-08-10"
    assert by_path[("Constitution > Dues", 0)].text == "Dues are $100 per season."
    # Repeated heading paths get distinct ordinals rather than colliding.
    assert by_path[("Constitution > Dues", 1)].text == "A second Dues heading."
    # Headings with no body produce no empty records.
    assert ("Constitution > Trades", 0) not in by_path


def test_document_date_reads_the_top_of_the_doc():
    assert document_date(DOC) == "2025-02-01"
    assert document_date("# Rules\nNo date.") is None


def test_long_sections_are_chunked_on_paragraphs():
    para = "x" * 600
    md = "# Big\n" + "\n\n".join([para] * 5)
    chunks = split_markdown("Doc", md)
    assert len(chunks) > 1
    assert all(len(c.text) <= MAX_SECTION_CHARS for c in chunks)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
