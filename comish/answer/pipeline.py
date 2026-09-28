"""The answer pipeline for one league.

    corpus -> generator draft -> deterministic checks -> independent verifier -> reply

Every path that isn't a clean pass ends in the abstain reply: a draft that abstains,
any failed check, a verifier that finds any problem, or any LLM error. Every attempt,
answered or not, is written to the league's audit log.
"""

import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from comish.answer.checks import citations, run_checks
from comish.answer.corpus import Corpus, build_corpus
from comish.answer.prompts import (
    GENERATOR_SCHEMA,
    GENERATOR_SYSTEM,
    PROMPT_VERSION,
    VERIFIER_SCHEMA,
    VERIFIER_SYSTEM,
    generator_prompt,
    verifier_prompt,
)
from comish.answer.render import ABSTAIN_REPLY, render_answer
from comish.kb.store import LeagueStore
from comish.leagues import League
from comish.llm.base import LLM, LLMError


@dataclass(frozen=True)
class CitedSource:
    label: str
    source: str
    quote: str


@dataclass
class AnswerResult:
    decision: str  # "answered" | "abstained"
    reply: str
    reason: str = ""
    citations: list[CitedSource] = field(default_factory=list)
    draft: dict[str, Any] | None = None
    checks: list[str] = field(default_factory=list)
    verifier: dict[str, Any] | None = None


def verifier_passes(verdict: dict[str, Any], claim_count: int) -> list[str]:
    problems = []
    if verdict.get("verdict") != "pass":
        problems.append(f"verifier failed it: {verdict.get('reason', '')}")
    supported = {c.get("claim_index") for c in verdict.get("claims", []) if c.get("supported")}
    unsupported = [i for i in range(claim_count) if i not in supported and i + 1 not in supported]
    if unsupported:
        problems.append(f"verifier did not confirm claims {unsupported}")
    for flag, message in [
        ("newer_record_may_supersede", "a newer record may supersede the sources"),
        ("sources_conflict", "sources conflict"),
        ("requires_judgment", "needs judgment"),
    ]:
        if verdict.get(flag):
            problems.append(message)
    if not verdict.get("fully_answers_question"):
        problems.append("doesn't fully answer the question")
    if not verdict.get("question_in_scope"):
        problems.append("question out of scope")
    return problems


class AnswerPipeline:
    def __init__(
        self,
        store: LeagueStore,
        league: League,
        generator: LLM,
        verifier: LLM,
        clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ):
        if store.slug != league.slug:
            raise ValueError("pipeline store and league must be the same league")
        self.store = store
        self.league = league
        self.generator = generator
        self.verifier = verifier
        self.clock = clock

    def _current_snapshot(self) -> tuple[str, str] | None:
        snapshots = self.store.snapshots()
        if not snapshots:
            return None
        latest = snapshots[0]
        return str(latest["league_id"]), str(latest["fetched_at"])

    def _system(self, template: str, corpus: Corpus) -> str:
        return template.format(
            league=self.league.name,
            sport=self.league.sport.upper(),
            today=self.clock().date().isoformat(),
            corpus=corpus.text,
        )

    def answer(
        self, question: str, asked_by: str | None = None, shadow: bool = False
    ) -> AnswerResult:
        started = time.monotonic()
        corpus = build_corpus(self.store)
        result = self._answer(question, corpus)
        self.store.log_attempt(
            {
                "question": question,
                "asked_by": asked_by,
                "received_at": self.clock().isoformat(),
                "corpus_hash": f"{corpus.hash}:{PROMPT_VERSION}",
                "generator_model": self.generator.model,
                "verifier_model": self.verifier.model,
                "draft": result.draft,
                "checks": result.checks,
                "verifier": result.verifier,
                "decision": result.decision,
                "reason": result.reason,
                "reply": result.reply,
                "citations": [c.__dict__ for c in result.citations],
                "latency_ms": int((time.monotonic() - started) * 1000),
                "shadow": int(shadow),
            }
        )
        return result

    def _abstain(self, reason: str, **details: Any) -> AnswerResult:
        return AnswerResult("abstained", ABSTAIN_REPLY, reason, **details)

    def _answer(self, question: str, corpus: Corpus) -> AnswerResult:
        if not question.strip():
            return self._abstain("empty question")
        if not corpus.entries:
            return self._abstain("no approved records in this league yet")
        try:
            draft = self.generator.generate_json(
                generator_prompt(question),
                GENERATOR_SCHEMA,
                system=self._system(GENERATOR_SYSTEM, corpus),
            )
        except LLMError as exc:
            return self._abstain(f"generator error: {exc}")
        if draft.get("decision") != "answer":
            return self._abstain(f"model abstained: {draft.get('abstain_reason', '')}", draft=draft)

        failures = run_checks(draft, corpus, self._current_snapshot(), self.clock())
        if failures:
            return self._abstain("; ".join(failures), draft=draft, checks=failures)

        try:
            verdict = self.verifier.generate_json(
                verifier_prompt(question, draft),
                VERIFIER_SCHEMA,
                system=self._system(VERIFIER_SYSTEM, corpus),
            )
        except LLMError as exc:
            return self._abstain(f"verifier error: {exc}", draft=draft)
        problems = verifier_passes(verdict, len(draft.get("claims", [])))
        if problems:
            return self._abstain("; ".join(problems), draft=draft, verifier=verdict)

        cited = citations(draft)
        reply = render_answer(str(draft["answer_text"]), cited, corpus, self.league)
        sources = []
        for c in cited:
            entry = corpus.citable(c.label)
            if entry is not None:
                sources.append(CitedSource(c.label, entry.cite_source, c.quote))
        return AnswerResult("answered", reply, "", sources, draft, [], verdict)
