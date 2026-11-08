"""A short, conservative list of questions Comish can never answer in v1.

Live scores, NFL stat leaders, invented stat lines and who owns a pick right now are
not league records, so the pipeline can only abstain on them. Flagging them to the
commissioner just adds noise, so they get a plain reply and no flag instead.

This check can only divert a question away from the commissioner flag, never toward
an answer. A missed match costs one extra flag; a false match would hide a real rules
question. So each pattern is narrow and tested, and anything that doesn't clearly
match goes down the normal path.
"""

import re

OUT_OF_SCOPE_REPLY = (
    "That's live or outside league data, which I don't cover. "
    "I only answer league rules and history."
)

PATTERNS: dict[str, re.Pattern[str]] = {
    "live scores": re.compile(
        r"\b(current|live) (scores?|matchups?)\b"
        r"|\bscores? (right )?now\b"
        r"|\bwho(?:'s| is) (winning|leading)\b",
        re.IGNORECASE,
    ),
    "live points needed": re.compile(
        r"\bpoints? (do i |would i |will i )?(need|needed)\b.*\bto win\b"
        r"|\bneed to score to win\b",
        re.IGNORECASE,
    ),
    "players left to play": re.compile(r"\bplayers? left to play\b", re.IGNORECASE),
    "who owns right now": re.compile(
        r"\bwho owns\b.*\b(pick|player)\b.*\bright now\b", re.IGNORECASE
    ),
    "nfl stat leaders": re.compile(
        r"\b(yac|yards after (the )?catch)\b"
        r"|\bwhich (rb|wr|qb|te|running back|receiver|quarterback)\b.*\b(most|highest)\b",
        re.IGNORECASE,
    ),
    "invented stat lines": re.compile(
        r"\bstat line\b|\bcombination of (plays|stats)\b", re.IGNORECASE
    ),
}


def out_of_scope(question: str) -> str | None:
    """The name of the pattern the question matches, or None."""
    for name, pattern in PATTERNS.items():
        if pattern.search(question):
            return name
    return None
