"""What the bot does with a message, independent of how messages arrive.

Two modes after the Phase 0 spike:
- shadow: nothing is posted to the group. Every question produces a private DM to the
  flag handles showing what the bot would have replied, so the commissioner can check
  answers before managers ever see one.
- live: the reply (an answer with its citation, or the abstain message) goes to the
  group, and every abstention is flagged to the commissioner by DM.

Handlers return the messages to send instead of sending them, so the server applies
the outbound cap in one place and tests can check behavior without a transport.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from comish.answer.pipeline import AnswerPipeline
from comish.commissioner.commands import CommissionerDesk, flag_message
from comish.commissioner.ops import OpsStore
from comish.kb.store import LeagueStore
from comish.leagues import League
from comish.transport.base import InboundMessage

Mode = Literal["shadow", "live"]
Outgoing = list[tuple[str, str]]  # (chat GUID, text)


def dm_chat_guid(handle: str) -> str:
    """BlueBubbles' GUID for a 1:1 iMessage chat with a phone number or email."""
    return f"iMessage;-;{handle}"


@dataclass
class Bot:
    mode: Mode
    leagues: dict[str, League]
    pipeline_for: Callable[[str], AnswerPipeline]
    open_store: Callable[[str], LeagueStore]
    desk: CommissionerDesk
    ops: OpsStore

    @property
    def chat_to_league(self) -> dict[str, League]:
        return {lg.chat_guid: lg for lg in self.leagues.values() if lg.chat_guid}

    @property
    def chat_guids(self) -> frozenset[str]:
        return frozenset(self.chat_to_league)

    def _to_handles(self, league: League, text: str) -> Outgoing:
        return [(dm_chat_guid(h), text) for h in league.flag_handles]

    def handle_question(self, msg: InboundMessage, question: str) -> Outgoing:
        league = self.chat_to_league[msg.chat_guid]
        result = self.pipeline_for(league.slug).answer(
            question, asked_by=msg.sender, shadow=self.mode == "shadow"
        )
        out: Outgoing = []
        if self.mode == "live":
            out.append((msg.chat_guid, result.reply))
        if result.decision == "abstained":
            flag_id = self.ops.new_flag(league.slug)
            self.open_store(league.slug).create_flag(flag_id, question, msg.sender, result.reason)
            text = flag_message(flag_id, league, msg.sender, question, result.reason)
            if self.mode == "shadow":
                text = "[shadow, nothing posted]\n" + text
            out += self._to_handles(league, text)
        elif self.mode == "shadow":
            out += self._to_handles(
                league,
                f"[shadow, nothing posted] [{league.name}] {msg.sender or 'someone'} asked: "
                f'"{question}"\nI would reply:\n{result.reply}',
            )
        return out

    def handle_dm(self, msg: InboundMessage, text: str) -> Outgoing:
        reply = self.desk.handle(msg.sender or "", text)
        return [(msg.chat_guid, reply)] if reply else []
