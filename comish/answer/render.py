"""Turn a checked, verified draft into the iMessage reply."""

from comish.answer.checks import CheckedCitation, normalize
from comish.answer.corpus import Corpus, Entry
from comish.leagues import League

ABSTAIN_REPLY = "I can't confirm this from league records. Flagging for the commissioner."
MAX_SOURCES = 2


def _date(entry: Entry) -> str:
    if entry.effective_date:
        return f"eff. {entry.effective_date}"
    return "undated"


def source_line(entry: Entry, quote: str, league: League) -> str:
    r = entry.record
    quote = normalize(quote)
    if r.record_type == "sleeper_setting":
        return f'Source: Sleeper league settings, {r.season} season: "{quote}"'
    if r.record_type == "ruling":
        relay = (
            f", relayed from {league.commissioner_name}"
            if league.ruling_authority == "relay" and league.commissioner_name
            else ""
        )
        return f'Source: {r.section_path}{relay} ({_date(entry)}): "{quote}"'
    if r.record_type == "vote":
        where = entry.source.path or entry.source.name
        return f'Source: screenshot {where} ({_date(entry)}): "{quote}"'
    return f'Source: {r.section_path} ({_date(entry)}): "{quote}"'


def render_answer(
    answer_text: str, cited: list[CheckedCitation], corpus: Corpus, league: League
) -> str:
    lines = [normalize(answer_text)]
    seen: set[str] = set()
    for c in cited:
        if c.label in seen or len(seen) >= MAX_SOURCES:
            continue
        entry = corpus.citable(c.label)
        if entry is None:  # run_checks already guarantees this can't happen
            raise ValueError(f"uncitable record {c.label} reached rendering")
        seen.add(c.label)
        lines.append(source_line(entry, c.quote, league))
    return "\n".join(lines)
