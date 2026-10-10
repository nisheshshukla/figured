"""What came back: every figure, whether it traced, and how."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Literal

from figured.derive import Match, fmt
from figured.extract import Figure

Status = Literal["grounded", "ungrounded", "ignored"]


@dataclass(frozen=True)
class Result:
    figure: Figure
    status: Status
    match: Match | None = None
    reason: str = ""

    @property
    def literal(self) -> str:
        return self.figure.literal

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "literal": self.figure.literal,
            "value": self.figure.value,
            "span": [self.figure.start, self.figure.end],
            "status": self.status,
        }
        if self.match:
            d["match"] = {
                "kind": self.match.kind,
                "value": self.match.value,
                "error": self.match.error,
                "explanation": self.match.explanation,
            }
        if self.reason:
            d["reason"] = self.reason
        return d


@dataclass
class Report:
    text: str
    results: list[Result]
    evidence_cells: int
    _index: Any = field(default=None, repr=False, compare=False)
    _policy: Any = field(default=None, repr=False, compare=False)
    _coincidence: float | None = field(default=None, repr=False, compare=False)

    def coincidence(self, draws: int = 200, seed: int = 7) -> float | None:
        """How much a green result means: the fraction of random figures, drawn between the smallest
        and largest cell, that this table and policy would also call grounded. 0.02 means a made-up
        figure has a 2% chance of passing; 0.9 means the check is not telling you anything. Computed
        on request, cached; None without evidence."""
        if self._index is None or self._coincidence is not None:
            return self._coincidence
        values = [v for v in self._index.flat_abs if v]
        if len(values) < 2:
            self._coincidence = 0.0
            return 0.0
        lo, hi = min(values), max(values)
        rng = random.Random(seed)
        hits = sum(
            1 for _ in range(draws) if self._index.lookup(rng.uniform(lo, hi), self._policy) is not None
        )
        self._coincidence = hits / draws
        return self._coincidence

    @property
    def ok(self) -> bool:
        return not self.ungrounded

    @property
    def grounded(self) -> list[Result]:
        return [r for r in self.results if r.status == "grounded"]

    @property
    def ungrounded(self) -> list[str]:
        return [r.figure.literal for r in self.results if r.status == "ungrounded"]

    @property
    def ignored(self) -> list[Result]:
        return [r for r in self.results if r.status == "ignored"]

    @property
    def checked(self) -> int:
        return sum(1 for r in self.results if r.status != "ignored")

    def caveat(self, limit: int = 4) -> str:
        if self.ok:
            return ""
        shown = ", ".join(self.ungrounded[:limit])
        more = len(self.ungrounded) - limit
        tail = f" and {more} more" if more > 0 else ""
        return (
            f"Note: these figures could not be traced to the data: {shown}{tail}. Treat them as approximate."
        )

    def explain(self) -> str:
        head = "OK" if self.ok else "UNGROUNDED"
        lines = [f"{head} · {self.checked} checked · {len(self.ungrounded)} untraceable"]
        rate = self.coincidence()
        if rate is not None:
            lines[0] += f" · coincidence {rate:.0%}"
        for r in self.results:
            if r.status == "grounded" and r.match:
                lines.append(f"  ✓ {r.literal:<16} {r.match.kind:<14} {r.match.explanation}")
            elif r.status == "ungrounded":
                lines.append(f"  ✗ {r.literal:<16} no cell, sum, difference, or ratio within tolerance")
            else:
                lines.append(f"  · {r.literal:<16} ignored ({r.reason})")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "ungrounded": self.ungrounded,
            "evidence_cells": self.evidence_cells,
            "coincidence": self.coincidence(),
            "figures": [r.to_dict() for r in self.results],
        }

    def __repr__(self) -> str:
        return f"Report(ok={self.ok}, checked={self.checked}, ungrounded={self.ungrounded})"


__all__ = ["Report", "Result", "Status", "fmt"]
