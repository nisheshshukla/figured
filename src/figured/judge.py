"""Optional second opinion from a model, for what arithmetic cannot see: wrong words around right numbers.

Requires the `judge` extra: pip install "figured[judge]". The judge receives the question, the
rows, and the answer, nothing else, and returns a strict verdict.
"""

from __future__ import annotations

import json
from typing import Any, Literal

JUDGE_SYSTEM = """You audit answers produced by a data assistant. You are given the user's question, the rows the assistant retrieved, and the assistant's answer. Judge strictly and only from what is shown.

faithful: every number in the answer is supported by the rows, allowing rounding and values derived from them (sums, differences, ratios, percentages), and every comparative word (higher, lower, most, fewer) agrees with the rows.
responsive: the answer addresses the question that was asked, or clearly explains what part cannot be answered.
caveats_ok: if the answer relies on an approximation or omits part of the question, it says so. True when no caveat was needed.
issues: short, specific strings; empty when none.
verdict: "pass" only if faithful and responsive are both true."""


def judge(
    question: str,
    answer: str,
    rows: Any,
    *,
    model: str = "claude-opus-5",
    client: Any = None,
    max_rows: int = 30,
) -> dict[str, Any]:
    """Return {"verdict", "faithful", "responsive", "caveats_ok", "issues", "model"}."""
    try:
        import anthropic
        from pydantic import BaseModel
    except ImportError as exc:  # pragma: no cover
        raise ImportError('the judge needs the extra: pip install "figured[judge]"') from exc

    class Verdict(BaseModel):  # type: ignore[misc]
        faithful: bool
        responsive: bool
        caveats_ok: bool
        issues: list[str]
        verdict: Literal["pass", "fail"]

    from figured.evidence import build_evidence

    ev = build_evidence(rows)
    evidence = [
        {
            "columns": rs.columns,
            "rows": [[c.value for c in row] for row in rs.rows[:max_rows]],
        }
        for rs in ev.results
    ]
    prompt = (
        f"Question:\n{question}\n\nRetrieved rows (JSON):\n{json.dumps(evidence)[:12000]}"
        f"\n\nAssistant answer:\n{answer}"
    )
    client = client or anthropic.Anthropic()
    resp = client.messages.parse(
        model=model,
        max_tokens=600,
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=Verdict,
    )
    parsed = resp.parsed_output
    if parsed is None:
        return {"verdict": "error", "issues": ["no parsed output"], "model": model}
    return {**parsed.model_dump(), "model": model}
