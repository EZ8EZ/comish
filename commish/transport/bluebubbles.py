"""BlueBubbles Server adapter: webhook parsing + REST sending.

Field names follow BlueBubbles Server v1.9.x (MessageSerializer / ChatSerializer):
webhook body is {"type": "new-message", "data": <message>}, and messages are sent with
POST /api/v1/message/text?password=... {chatGuid, tempGuid, message, method}.
"""

import uuid
from typing import Any

import httpx

from commish.transport.base import InboundMessage

NEW_MESSAGE = "new-message"
GROUP_CHAT_STYLE = 43  # chat.db style for group chats (45 = 1:1)


class BlueBubblesError(RuntimeError):
    pass


def is_group_chat(chat: dict[str, Any]) -> bool:
    # GUIDs look like "iMessage;+;chat123" (group) or "iMessage;-;+15551234567" (1:1).
    # macOS 26 prefixes them with "any;" instead of the service name.
    return chat.get("style") == GROUP_CHAT_STYLE or ";+;" in chat.get("guid", "")


def parse_webhook(payload: dict[str, Any]) -> InboundMessage | None:
    """Return the message for a new-message event, or None for any other event."""
    if payload.get("type") != NEW_MESSAGE:
        return None
    data = payload.get("data") or {}
    chats = data.get("chats") or []
    if not data.get("guid") or not chats:
        return None
    chat = chats[0]
    handle = data.get("handle") or {}
    return InboundMessage(
        guid=data["guid"],
        chat_guid=chat.get("guid", ""),
        is_group=is_group_chat(chat),
        sender=None if data.get("isFromMe") else handle.get("address"),
        text=data.get("text"),
        is_from_me=bool(data.get("isFromMe")),
        # Tapbacks carry associatedMessageType (e.g. 2000-3005 or "love"); plain messages don't.
        is_reaction=data.get("associatedMessageType") not in (None, 0, ""),
        is_retracted=data.get("dateRetracted") is not None,
        is_system=bool(
            data.get("itemType") or data.get("isSystemMessage") or data.get("isServiceMessage")
        ),
        has_attachments=bool(data.get("attachments")),
        sent_at_ms=data.get("dateCreated"),
    )


class BlueBubblesTransport:
    def __init__(
        self,
        base_url: str,
        password: str,
        send_method: str = "apple-script",
        client: httpx.AsyncClient | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._password = password
        self._send_method = send_method
        self._client = client or httpx.AsyncClient(timeout=30)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = await self._client.request(
            method,
            f"{self._base_url}/api/v1/{path}",
            params={"password": self._password},
            **kwargs,
        )
        body = resp.json() if resp.content else {}
        if resp.status_code != 200:
            error = body.get("error") or body.get("message") or resp.text
            raise BlueBubblesError(f"{method} {path} -> {resp.status_code}: {error}")
        return body.get("data")

    async def send_text(self, chat_guid: str, text: str) -> None:
        await self._request(
            "POST",
            "message/text",
            json={
                "chatGuid": chat_guid,
                "tempGuid": f"commish-{uuid.uuid4()}",
                "message": text,
                "method": self._send_method,
            },
        )

    async def ping(self) -> Any:
        return await self._request("GET", "ping")

    async def list_chats(self, limit: int = 50) -> list[dict[str, Any]]:
        chats: list[dict[str, Any]] = await self._request(
            "POST",
            "chat/query",
            json={"limit": limit, "with": ["participants", "lastMessage"]},
        )
        return chats

    async def aclose(self) -> None:
        await self._client.aclose()
