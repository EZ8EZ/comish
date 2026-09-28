"""Transport-neutral message types.

Everything above this layer (intake, answering, flags) only sees InboundMessage and
Transport, so BlueBubbles can be swapped for imsg or the web fallback without touching it.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class InboundMessage:
    guid: str
    chat_guid: str
    is_group: bool
    sender: str | None  # phone number or email; None for our own messages
    text: str | None
    is_from_me: bool
    # Reactions, edits, unsends, group events and system messages are "non-content".
    is_reaction: bool = False
    is_retracted: bool = False
    is_system: bool = False
    has_attachments: bool = False
    sent_at_ms: int | None = None


class Transport(Protocol):
    async def send_text(self, chat_guid: str, text: str) -> None: ...
