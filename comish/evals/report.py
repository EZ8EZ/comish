"""Human-readable eval report."""

from typing import Any


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def format_report(runs: list[dict[str, Any]], gate_result: dict[str, Any]) -> str:
    lines = ["Eval report", ""]
    for m in runs:
        bound = m["false_answer_rate_upper_bound"]
        bound_text = f" (95% upper bound {bound:.1%})" if bound is not None else ""
        lines += [
            f"Run {m['run']}: {m['graded']}/{m['cases']} graded, {m['answered']} answered",
            f"  false answers:   {m['false_answers']} "
            f"({_pct(m['false_answer_rate'])} of answered){bound_text}",
            f"  uncited answers: {m['uncited_answers']}",
            f"  abstain recall:  {_pct(m['abstain_recall'])} of {m['should_abstain']}",
            f"  coverage:        {_pct(m['coverage'])} of {m['answerable']} answerable",
        ]
        for cid in m["failing_cases"]:
            g = m["grades"][cid]
            lines.append(f"  FAIL {cid}: {g['verdict']} {'; '.join(g['reasons'])}")
        lines.append("")
    lines.append("GATE: " + ("PASSED" if gate_result["passed"] else "NOT PASSED"))
    lines += [f"  - {p}" for p in gate_result["problems"]]
    return "\n".join(lines)
