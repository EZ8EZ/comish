"""In-memory Drive plus builders for the file types the sync understands."""

import io
from typing import Any

from openpyxl import Workbook

from comish.ingest import drive as d


def minimal_pdf(pages: list[str]) -> bytes:
    """A valid PDF whose pages contain the given text (Helvetica, one line each)."""
    objects: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font_id = 3 + 2 * len(pages)
    for i, text in enumerate(pages):
        content_id = 4 + 2 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode() if text else b""
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    )
    return out.getvalue()


def workbook(tabs: dict[str, list[list[Any]]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in tabs.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class FakeDrive:
    """Folders map to children; files carry content keyed by export mime type or 'raw'."""

    def __init__(self) -> None:
        self.children: dict[str, list[dict[str, Any]]] = {"root": []}
        self.content: dict[str, dict[str, bytes]] = {}
        self.downloads: list[str] = []

    def add_folder(self, parent: str, folder_id: str, name: str) -> None:
        self.children[parent].append({"id": folder_id, "name": name, "mimeType": d.FOLDER})
        self.children[folder_id] = []

    def add_file(
        self,
        parent: str,
        file_id: str,
        name: str,
        mime: str,
        content: dict[str, bytes],
        md5: str | None = None,
        modified: str = "2026-01-01T00:00:00Z",
    ) -> None:
        self.children[parent] = [c for c in self.children[parent] if c["id"] != file_id]
        item = {"id": file_id, "name": name, "mimeType": mime, "modifiedTime": modified}
        if md5:
            item["md5Checksum"] = md5
        self.children[parent].append(item)
        self.content[file_id] = content

    def remove(self, parent: str, file_id: str) -> None:
        self.children[parent] = [c for c in self.children[parent] if c["id"] != file_id]

    def list_children(self, folder_id: str) -> list[dict[str, Any]]:
        return list(self.children.get(folder_id, []))

    def export(self, file_id: str, mime_type: str) -> bytes:
        return self.content[file_id][mime_type]

    def download(self, file_id: str) -> bytes:
        self.downloads.append(file_id)
        return self.content[file_id]["raw"]
