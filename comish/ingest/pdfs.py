"""Extract page text from PDFs.

Pages without extractable text (scans) can't be quoted verbatim, so they are reported
instead of guessed at. Transcribing scanned PDFs is not supported yet; export those
pages as images into the Drive folder and they go through screenshot review.
"""

import io
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from comish.ingest.docs import clean_markdown


class PdfError(ValueError):
    pass


@dataclass(frozen=True)
class PdfPages:
    pages: list[tuple[int, str]]  # (1-based page number, text)
    empty_pages: list[int]


def extract_pages(data: bytes) -> PdfPages:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages: list[tuple[int, str]] = []
        empty: list[int] = []
        for number, page in enumerate(reader.pages, start=1):
            text = clean_markdown(page.extract_text() or "")
            if text:
                pages.append((number, text))
            else:
                empty.append(number)
    except (PdfReadError, ValueError, KeyError) as exc:
        raise PdfError(f"unreadable PDF: {exc}") from exc
    return PdfPages(pages, empty)
