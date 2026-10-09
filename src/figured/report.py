"""What came back: every figure, whether it traced, and how."""

from __future__ import annotations

from dataclasses import dataclass
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
            "figures": [r.to_dict() for r in self.results],
        }

    def __repr__(self) -> str:
        return f"Report(ok={self.ok}, checked={self.checked}, ungrounded={self.ungrounded})"


__all__ = ["Report", "Result", "Status", "fmt"]
