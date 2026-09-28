"""Decide whether an inbound message is addressed to the bot.

Everything not addressed to @comish in a bound chat is dropped here, before any
storage or LLM call.
"""

import re
from collections import OrderedDict
from dataclasses import dataclass

from comish.transport.base import InboundMessage

# "@comish" as a whole word: not "@comishbot", not "email@comish.com". The legacy
# spelling "@commish" also triggers, so a typo or autocorrect never drops a question.
MENTION_RE = re.compile(r"(?<![\w@.])@comm?ish(?![\w@])", re.IGNORECASE)


@dataclass(frozen=True)
class IntakeDecision:
    handle: bool
    reason: str
    question: str = ""


def is_mention(text: str | None) -> bool:
    return bool(text and MENTION_RE.search(text))


def strip_mention(text: str) -> str:
    return " ".join(MENTION_RE.sub(" ", text).split())


class SeenMessages:
    """Bounded set of message GUIDs, so a re-delivered webhook is answered once."""

    def __init__(self, maxlen: int = 5000):
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._maxlen = maxlen

    def add(self, guid: str) -> bool:
        """Record guid; return False if it was already seen."""
        if guid in self._seen:
            return False
        self._seen[guid] = None
        if len(self._seen) > self._maxlen:
            self._seen.popitem(last=False)
        return True


def evaluate(
    msg: InboundMessage, allowed_chat_guids: frozenset[str], seen: SeenMessages
) -> IntakeDecision:
    # Order matters: cheapest, most common rejections first. The bot's own messages are
    # rejected before anything else so a reply containing "@comish" can never loop.
    if msg.is_from_me:
        return IntakeDecision(False, "from_me")
    if msg.chat_guid not in allowed_chat_guids:
        return IntakeDecision(False, "unbound_chat")
    if msg.is_reaction:
        return IntakeDecision(False, "reaction")
    if msg.is_retracted:
        return IntakeDecision(False, "retracted")
    if msg.is_system:
        return IntakeDecision(False, "system")
    if not is_mention(msg.text):
        return IntakeDecision(False, "no_mention")
    if not seen.add(msg.guid):
        return IntakeDecision(False, "duplicate")
    return IntakeDecision(True, "mention", strip_mention(msg.text or ""))
