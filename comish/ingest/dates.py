"""Find effective dates written in league documents.

This is deliberately code, not the LLM: a date is only proposed when the document says
so explicitly ("Amended Aug 10, 2024", "Effective: 2025-02-01") or a heading carries a
date. Proposals are confirmed by the commissioner when the source is approved. File
timestamps are never treated as effective dates.
"""

import datetime as dt
import re

MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}
_MONTH = r"(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"

ISO = r"(?P<iso>\d{4}-\d{2}-\d{2})"
US = r"(?P<us>\d{1,2}/\d{1,2}/(?:\d{4}|\d{2}))"
MDY = rf"(?P<mdy>{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}})"
DMY = rf"(?P<dmy>\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH},?\s+\d{{4}})"
DATE_RE = re.compile(rf"\b(?:{ISO}|{US}|{MDY}|{DMY})", re.IGNORECASE)

MARKERS = (
    r"(?:last\s+updated|effective(?:\s+date)?|amended|updated|ratified|adopted|revised|as\s+of)"
)
MARKED_RE = re.compile(rf"\b{MARKERS}\b(?:\s+on)?[\s:,\-]{{0,4}}", re.IGNORECASE)


def _parse_match(match: re.Match[str]) -> dt.date | None:
    try:
        if match.group("iso"):
            return dt.date.fromisoformat(match.group("iso"))
        if match.group("us"):
            month, day, year = (int(p) for p in match.group("us").split("/"))
            if year < 100:
                year += 2000
            return dt.date(year, month, day)
        text = match.group("mdy") or match.group("dmy")
        words = re.findall(r"[a-z]+|\d+", text.lower())
        numbers = [int(w) for w in words if w.isdigit()]
        month_name = next(w for w in words if w in MONTHS)
        day, year = (numbers[0], numbers[1]) if len(numbers) == 2 else (0, 0)
        return dt.date(year, MONTHS[month_name], day)
    except (ValueError, StopIteration):
        return None


def parse_date(text: str) -> dt.date | None:
    """The first valid date anywhere in text."""
    for match in DATE_RE.finditer(text):
        parsed = _parse_match(match)
        if parsed:
            return parsed
    return None


def marked_dates(text: str) -> list[dt.date]:
    """Dates that directly follow a marker word such as "Amended" or "Effective"."""
    found = []
    for marker in MARKED_RE.finditer(text):
        match = DATE_RE.match(text, marker.end())
        if match:
            parsed = _parse_match(match)
            if parsed:
                found.append(parsed)
    return found


def latest_marked_date(text: str) -> dt.date | None:
    """The latest explicitly marked date: a doc "adopted 2023, amended 2025" is effective 2025."""
    dates = marked_dates(text)
    return max(dates) if dates else None
