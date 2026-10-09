"""A promptfoo Python assertion.

In promptfooconfig.yaml:

    tests:
      - vars:
          question: "What is the population of California?"
          rows: [{"state": "CA", "population": 39346023}]
        assert:
          - type: python
            value: file://examples/promptfoo_assert.py

promptfoo calls get_assert(output, context) and uses the returned dict.
"""

from __future__ import annotations

from typing import Any

from figured import trace


def get_assert(output: str, context: dict[str, Any]) -> dict[str, Any]:
    rows = context.get("vars", {}).get("rows", [])
    report = trace(output, rows)
    return {
        "pass": report.ok,
        "score": 1.0 if report.ok else max(0.0, 1 - len(report.ungrounded) / max(report.checked, 1)),
        "reason": report.caveat() or f"{report.checked} figures traced",
        "componentResults": [
            {
                "pass": r.status != "ungrounded",
                "score": 1.0 if r.status != "ungrounded" else 0.0,
                "reason": r.literal,
            }
            for r in report.results
            if r.status != "ignored"
        ],
    }
