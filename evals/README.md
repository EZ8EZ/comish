# Evals

The eval harness is Comish's release gate. No prompt, model or corpus change ships unless every gate run is clean.

## Where cases live

| File | Committed | What |
|---|---|---|
| `comish/evals/adversarial.yaml` | yes | League-agnostic questions that must always abstain. They run against every league. |
| `evals/example/cases.yaml` | yes | A synthetic example of the format. |
| `data/<slug>/evals/*.yaml` | **no** (gitignored) | Each league's real cases. They quote league records, and this repo is public. |
| `data/<slug>/evals/verdicts.yaml` | **no** | Your manual verdicts for answers the grader couldn't confirm. |
| `data/<slug>/evals/runs/<name>.jsonl` | **no** | Checkpoints of every attempt, so runs resume after a quota stop. |

## Writing a case

```yaml
cases:
  - id: taxi-trade-in-season        # unique across all files
    question: Can I trade a taxi player during the season?
    expected: answer                # or: abstain
    category: rule_lookup           # see CATEGORIES in comish/evals/cases.py
    must_include: ["offseason"]     # facts the answer must state
    acceptable_sources:             # record label, section path prefix, or Sleeper field
      - "Constitution > Trades > 4.2"
      - "sleeper:settings.trade_deadline"
    notes: Why this is the right answer, for future you.
```

Every `answer` case needs both `must_include` and `acceptable_sources`.

Write the adversarial cases from your own records:
- **superseded:** an old rule a later vote replaced
- **doc and vote conflicts**
- **Sleeper vs. doc mismatches**
- **only an undated screenshot says so**
- **false premises**
- **judgment calls:** "is this trade fair?"

## Grading (conservative by design)

| Verdict | Meaning | Counts as a false answer? |
|---|---|---|
| `correct_answer` | Answered, with the required facts and an acceptable citation | no |
| `correct_abstain` | Abstained on a should-abstain case | no |
| `missed_answer` | Abstained on an answerable case | no (it lowers coverage) |
| `false_answer` | Answered when it should have abstained | **yes** |
| `uncited_answer` | Answered with no citation | **yes**, always; a manual verdict can't rescue it |
| `needs_review` | Answered, but a fact is missing or the citation isn't listed | **yes**, until you add a manual verdict |

To accept a correct answer the grader flagged, add its key from the report to `verdicts.yaml`, for example `taxi-trade-in-season:1a2b3c4d5e6f7a8b: correct_answer`.

## The gate

Every one of the 3 runs must have:
- 0 false answers
- 0 uncited answers
- 100% abstain recall
- at least 75% coverage

And the set must hold at least 40 answerable and 40 should-abstain cases.

Even a clean gate only bounds the false-answer rate. With 0 errors in `n` answered questions, the 95% upper bound is about `3/n`, and the report prints it.

```bash
uv run comish eval football                  # always-abstain baseline for now
uv run comish eval football --max-attempts 200 --pause 4   # stay inside a free-tier quota
```

The always-abstain baseline must score 0 false answers and 0% coverage. That proves the grader works. It never passes the gate.
