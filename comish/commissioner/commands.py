"""Commissioner DM commands and flag messages.

Commands are accepted only from a league's configured flag handles, and only in a
1:1 DM with the bot, never in a group. A ruling takes two steps: `rule F17 <text>`
shows exactly what will be saved, and `yes` saves it. That guards against typos
becoming the league's permanent record.

    rule F17 <text>   propose a ruling for flag F17
    yes / no          confirm or cancel the proposed ruling
    skip F17          dismiss a flag without a ruling
    status            open flags and review counts for your leagues
    help              this list
"""

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass

from comish.commissioner.ops import OpsStore
from comish.kb.store import LeagueStore, ReviewError
from comish.leagues import League

RULE_RE = re.compile(r"^\s*rule\s+F(\d+)\s*[:,-]?\s+(.+)$", re.IGNORECASE | re.DOTALL)
SKIP_RE = re.compile(r"^\s*skip\s+F(\d+)\s*$", re.IGNORECASE)

HELP = (
    "Commands: rule F17 <ruling text> | skip F17 | status | help. "
    "A ruling is saved only after you reply yes."
)


def flag_message(
    flag_id: int, league: League, asked_by: str | None, question: str, reason: str
) -> str:
    who = asked_by or "someone"
    authority = (
        f" Relay the {league.commissioner_name}'s ruling."
        if league.ruling_authority == "relay"
        else ""
    )
    return (
        f'Flag F{flag_id} [{league.name}] {who} asked: "{question}"\n'
        f"Why I didn't answer: {reason}.{authority}\n"
        f"Reply: rule F{flag_id} <ruling>  or  skip F{flag_id}"
    )


@dataclass
class CommissionerDesk:
    leagues: dict[str, League]
    ops: OpsStore
    open_store: Callable[[str], LeagueStore]
    today: Callable[[], dt.date] = lambda: dt.datetime.now(dt.UTC).date()

    def leagues_for(self, handle: str) -> list[League]:
        return [lg for lg in self.leagues.values() if handle in lg.flag_handles]

    def is_commissioner(self, handle: str | None) -> bool:
        return bool(handle) and bool(self.leagues_for(handle or ""))

    def _flag_league(self, handle: str, flag_id: int) -> League | None:
        slug = self.ops.flag_league(flag_id)
        league = self.leagues.get(slug or "")
        if league is None or handle not in league.flag_handles:
            return None
        return league

    def handle(self, handle: str, text: str) -> str:
        """Process one DM from a commissioner handle; returns the reply to send."""
        if not self.is_commissioner(handle):
            return ""  # never respond to non-commissioners in DMs
        body = text.strip()
        lowered = body.lower()
        if lowered in ("yes", "y", "confirm"):
            return self._confirm(handle)
        if lowered in ("no", "n", "cancel"):
            return "Cancelled." if self.ops.pop_pending(handle) else "Nothing to cancel."
        if lowered == "help":
            return HELP
        if lowered == "status":
            return self._status(handle)
        if m := RULE_RE.match(body):
            return self._propose(handle, int(m.group(1)), m.group(2).strip())
        if m := SKIP_RE.match(body):
            return self._skip(handle, int(m.group(1)))
        return HELP

    def _propose(self, handle: str, flag_id: int, text: str) -> str:
        league = self._flag_league(handle, flag_id)
        if league is None:
            return f"F{flag_id} isn't one of your flags."
        store = self.open_store(league.slug)
        flag = store.get_flag(flag_id)
        if flag is None or flag["status"] != "open":
            return f"F{flag_id} is {'unknown' if flag is None else flag['status']}."
        self.ops.set_pending(handle, flag_id, text)
        cited = f"Commissioner ruling F{flag_id}"
        if league.ruling_authority == "relay":
            cited += f", relayed from {league.commissioner_name}"
        return (
            f"Save this as {cited} ({self.today().isoformat()}) for {league.name}?\n"
            f'"{text}"\n'
            "Reply yes to save or no to cancel. The bot will cite it word for word."
        )

    def _confirm(self, handle: str) -> str:
        pending = self.ops.pop_pending(handle)
        if pending is None:
            return "Nothing waiting for confirmation."
        league = self._flag_league(handle, int(pending["flag_id"]))
        if league is None:
            return "That flag is no longer yours."
        store = self.open_store(league.slug)
        try:
            record = store.add_ruling(
                int(pending["flag_id"]), str(pending["text"]), self.today().isoformat()
            )
        except ReviewError as exc:
            return f"Not saved: {exc}"
        return f"Saved as {record.label}. The bot can cite it from now on."

    def _skip(self, handle: str, flag_id: int) -> str:
        league = self._flag_league(handle, flag_id)
        if league is None:
            return f"F{flag_id} isn't one of your flags."
        store = self.open_store(league.slug)
        flag = store.get_flag(flag_id)
        if flag is None or flag["status"] != "open":
            return f"F{flag_id} is {'unknown' if flag is None else flag['status']}."
        store.dismiss_flag(flag_id)
        return f"Dismissed F{flag_id}."

    def _status(self, handle: str) -> str:
        lines = []
        for league in self.leagues_for(handle):
            store = self.open_store(league.slug)
            counts = store.review_counts()
            flags = ", ".join(f"F{f['id']}" for f in store.open_flags()) or "none"
            lines.append(
                f"{league.name}: {counts['citable_records']} citable, "
                f"{counts['pending_records']} pending review, open flags: {flags}"
            )
        return "\n".join(lines)
