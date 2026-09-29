"""Split a Google Doc (exported as Markdown) into citable sections.

Each heading starts a section whose path is the chain of headings above it, for
example "Constitution > Trades > 4.2 Taxi squad". Text is kept verbatim apart from
Markdown escapes and emphasis markers, so quotes the bot cites match what managers
see in the document.
"""

import re
from dataclasses import dataclass

from comish.ingest.dates import latest_marked_date, parse_date

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")  # a backslash before any ASCII punctuation
EMPHASIS_RE = re.compile(r"(\*\*|__)")
MAX_SECTION_CHARS = 1500
PREAMBLE_CHARS = 2000


@dataclass(frozen=True)
class Section:
    path: str
    ordinal: int
    text: str
    proposed_date: str | None = None


def clean_markdown(text: str) -> str:
    text = ESCAPE_RE.sub(r"\1", text)
    text = EMPHASIS_RE.sub("", text)
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _chunks(body: str) -> list[str]:
    if len(body) <= MAX_SECTION_CHARS:
        return [body]
    chunks: list[str] = []
    current = ""
    for para in body.split("\n\n"):
        if current and len(current) + len(para) + 2 > MAX_SECTION_CHARS:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


def split_markdown(doc_name: str, markdown: str) -> list[Section]:
    stack: list[tuple[int, str]] = []
    sections: list[Section] = []
    used: dict[str, int] = {}
    body: list[str] = []
    heading_text = ""

    def flush() -> None:
        text = clean_markdown("\n".join(body))
        if not text:
            return
        path = " > ".join([doc_name, *(title for _, title in stack)])
        date = parse_date(heading_text) if heading_text else None
        date = date or latest_marked_date(text)
        for chunk in _chunks(text):
            ordinal = used.get(path, 0)
            used[path] = ordinal + 1
            sections.append(Section(path, ordinal, chunk, date.isoformat() if date else None))

    for line in markdown.replace("\r\n", "\n").split("\n"):
        match = HEADING_RE.match(line)
        if match:
            flush()
            body = []
            level = len(match.group(1))
            title = clean_markdown(match.group(2))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            heading_text = title
        else:
            body.append(line)
    flush()
    return sections


def document_date(markdown: str) -> str | None:
    """A date the document states about itself, looked for near the top."""
    date = latest_marked_date(clean_markdown(markdown[:PREAMBLE_CHARS]))
    return date.isoformat() if date else None
