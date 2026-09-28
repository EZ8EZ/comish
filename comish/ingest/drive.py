"""Google Drive access, read-only, through a service account.

The commissioner shares the league folder with the service account's email as a
Viewer; nothing else in their Drive is visible to it. The key JSON lives in the
Keychain as `google_service_account_json`.

Everything else talks to the DriveClient protocol, so tests use an in-memory fake.
"""

import json
from dataclasses import dataclass
from typing import Any, Protocol

FOLDER = "application/vnd.google-apps.folder"
GDOC = "application/vnd.google-apps.document"
GSHEET = "application/vnd.google-apps.spreadsheet"
PDF = "application/pdf"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MARKDOWN = "text/markdown"
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"}
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FIELDS = "nextPageToken, files(id, name, mimeType, modifiedTime, md5Checksum)"
MAX_DEPTH = 8


class DriveError(RuntimeError):
    pass


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime_type: str
    modified_time: str | None
    md5: str | None
    path: str  # folder-relative, e.g. "24-25/IMG_2740.PNG"


class DriveClient(Protocol):
    def list_children(self, folder_id: str) -> list[dict[str, Any]]: ...
    def export(self, file_id: str, mime_type: str) -> bytes: ...
    def download(self, file_id: str) -> bytes: ...


def walk(client: DriveClient, folder_id: str) -> list[DriveFile]:
    """Every file under the folder, recursively, with folder-relative paths."""
    files: list[DriveFile] = []
    seen: set[str] = set()

    def visit(fid: str, prefix: str, depth: int) -> None:
        if fid in seen or depth > MAX_DEPTH:
            return
        seen.add(fid)
        for item in sorted(client.list_children(fid), key=lambda i: str(i.get("name", ""))):
            path = f"{prefix}{item['name']}"
            if item["mimeType"] == FOLDER:
                visit(item["id"], f"{path}/", depth + 1)
            else:
                files.append(
                    DriveFile(
                        id=item["id"],
                        name=item["name"],
                        mime_type=item["mimeType"],
                        modified_time=item.get("modifiedTime"),
                        md5=item.get("md5Checksum"),
                        path=path,
                    )
                )

    visit(folder_id, "", 0)
    return files


class GoogleDriveClient:
    """The real client. Imports the Google libraries lazily so tests don't need them."""

    def __init__(self, service_account_json: str):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        try:
            info = json.loads(service_account_json)
        except json.JSONDecodeError as exc:
            raise DriveError("google_service_account_json is not valid JSON") from exc
        credentials = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
            info, scopes=SCOPES
        )
        self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self.email = str(info.get("client_email", ""))

    def _run(self, request: Any) -> Any:
        from googleapiclient.errors import HttpError

        try:
            return request.execute(num_retries=2)
        except HttpError as exc:
            raise DriveError(f"Drive API error: {exc}") from exc

    def list_children(self, folder_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            resp = self._run(
                self._service.files().list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields=FIELDS,
                    pageSize=200,
                    pageToken=token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
            )
            items.extend(resp.get("files", []))
            token = resp.get("nextPageToken")
            if not token:
                return items

    def export(self, file_id: str, mime_type: str) -> bytes:
        return bytes(self._run(self._service.files().export(fileId=file_id, mimeType=mime_type)))

    def download(self, file_id: str) -> bytes:
        return bytes(
            self._run(self._service.files().get_media(fileId=file_id, supportsAllDrives=True))
        )
