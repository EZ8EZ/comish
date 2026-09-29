"""Run eval cases through an answerer, resumably.

Free-tier LLM quotas mean a full gate (about 120 cases x 2 calls x 3 runs) may take
days, so every attempt is checkpointed to a JSONL file as it completes. Re-running the
same command skips finished attempts and continues where it stopped. Any exception
from the answerer is recorded as an abstention with the error, never as an answer.
"""

import json
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from comish.evals.cases import EvalCase
from comish.evals.grader import Citation, Grade, Outcome, Verdict, grade
from comish.evals.metrics import run_metrics

Answerer = Callable[[str], Outcome]


def always_abstain(question: str) -> Outcome:
    """The sanity baseline: it must score zero false answers and zero coverage."""
    return Outcome("abstained", "I can't confirm this from league records.", (), "baseline")


def _outcome(raw: dict[str, Any]) -> Outcome:
    return Outcome(
        decision=raw["decision"],
        reply=raw["reply"],
        citations=tuple(Citation(**c) for c in raw.get("citations", [])),
        abstain_reason=raw.get("abstain_reason", ""),
        error=raw.get("error", ""),
    )


class Checkpoint:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[tuple[int, str], Outcome]:
        done: dict[tuple[int, str], Outcome] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    done[(item["run"], item["case"])] = _outcome(item["outcome"])
        return done

    def append(self, run: int, case_id: str, outcome: Outcome) -> None:
        item = {"run": run, "case": case_id, "outcome": asdict(outcome), "ts": time.time()}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item) + "\n")


def run_evals(
    cases: list[EvalCase],
    answer: Answerer,
    checkpoint: Checkpoint,
    repeat: int = 1,
    pause_s: float = 0.0,
    max_attempts: int | None = None,
    overrides: dict[str, Verdict] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Run (or resume) `repeat` runs; returns per-run metrics.

    max_attempts caps new attempts in this invocation, to stay inside a daily quota.
    """
    done = checkpoint.load()
    attempts = 0
    for run in range(1, repeat + 1):
        for case in cases:
            if (run, case.id) in done:
                continue
            if max_attempts is not None and attempts >= max_attempts:
                break
            try:
                outcome = answer(case.question)
            except Exception as exc:  # an error is an abstention, never an answer
                outcome = Outcome("abstained", "", (), "error", f"{type(exc).__name__}: {exc}")
            checkpoint.append(run, case.id, outcome)
            done[(run, case.id)] = outcome
            attempts += 1
            if pause_s:
                sleep(pause_s)
    results = []
    for run in range(1, repeat + 1):
        grades: dict[str, Grade] = {
            c.id: grade(c, done[(run, c.id)], overrides) for c in cases if (run, c.id) in done
        }
        metrics = run_metrics(cases, grades)
        metrics["run"] = run
        metrics["grades"] = {cid: asdict(g) for cid, g in grades.items()}
        results.append(metrics)
    return results
