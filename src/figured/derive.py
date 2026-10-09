"""Everything the rows could legitimately produce: cells, sums, differences, ratios, percentages."""

from __future__ import annotations

import bisect
import itertools
from dataclasses import dataclass

from figured.evidence import Cell, Evidence
from figured.policy import Policy

RANK = {
    "cell": 0,
    "column_sum": 1,
    "row_sum": 2,
    "difference": 3,
    "ratio": 4,
    "percent": 4,
    "percent_change": 5,
}


@dataclass(frozen=True)
class Candidate:
    value: float
    kind: str
    explanation: str

    @property
    def rank(self) -> int:
        return RANK[self.kind]


@dataclass(frozen=True)
class Match:
    candidate: Candidate
    error: float

    @property
    def kind(self) -> str:
        return self.candidate.kind

    @property
    def explanation(self) -> str:
        return self.candidate.explanation

    @property
    def value(self) -> float:
        return self.candidate.value


def fmt(v: float) -> str:
    if abs(v) >= 1e15:
        return f"{v:.3g}"
    if float(v).is_integer():
        return f"{int(v):,}"
    if abs(v) >= 100:
        return f"{v:,.1f}"
    return f"{v:,.4g}"


def build_candidates(ev: Evidence, policy: Policy) -> list[Candidate]:
    allowed = policy.derivations
    out: list[Candidate] = []

    for rs in ev.results:
        for row in rs.rows:
            for c in row:
                if "cell" in allowed:
                    out.append(Candidate(c.value, "cell", f"{c.ref()} = {fmt(c.value)}"))
            if "row_sum" in allowed:
                out.extend(_row_sums(row))
        if "column_sum" in allowed:
            out.extend(_column_sums(rs.rows))

    flat = [c for rs in ev.results for row in rs.rows[: policy.max_rows] for c in row][: policy.max_cells]
    if any(k in allowed for k in ("difference", "ratio", "percent", "percent_change")):
        for a, b in itertools.permutations(flat, 2):
            out.extend(_pair(a, b, allowed))
    return out


def _row_sums(row: list[Cell]) -> list[Candidate]:
    out: list[Candidate] = []
    n = len(row)
    for i in range(n):
        for j in range(i + 2, min(n, i + 6) + 1):
            part = row[i:j]
            total = sum(c.value for c in part)
            out.append(
                Candidate(
                    total,
                    "row_sum",
                    f"{part[0].column}..{part[-1].column}[{part[0].label}] summed = {fmt(total)}",
                )
            )
    return out


def _column_sums(rows: list[list[Cell]]) -> list[Candidate]:
    by_col: dict[str, list[Cell]] = {}
    for row in rows:
        for c in row:
            by_col.setdefault(c.column, []).append(c)
    out: list[Candidate] = []
    for col, cells in by_col.items():
        if len(cells) < 2:
            continue
        total = sum(c.value for c in cells)
        out.append(Candidate(total, "column_sum", f"sum of {col} over {len(cells)} rows = {fmt(total)}"))
    return out


def _pair(a: Cell, b: Cell, allowed: frozenset[str]) -> list[Candidate]:
    out: list[Candidate] = []
    if "difference" in allowed:
        d = a.value - b.value
        out.append(
            Candidate(d, "difference", f"{a.ref()} − {b.ref()} = {fmt(a.value)} − {fmt(b.value)} = {fmt(d)}")
        )
    if b.value:
        if "ratio" in allowed:
            r = a.value / b.value
            out.append(Candidate(r, "ratio", f"{a.ref()} ÷ {b.ref()} = {fmt(r)}"))
        if "percent" in allowed:
            p = a.value / b.value * 100
            out.append(Candidate(p, "percent", f"{a.ref()} ÷ {b.ref()} = {fmt(p)}%"))
        if "percent_change" in allowed:
            pc = (a.value - b.value) / b.value * 100
            out.append(Candidate(pc, "percent_change", f"({a.ref()} − {b.ref()}) ÷ {b.ref()} = {fmt(pc)}%"))
    return out


class Index:
    """Sorted candidates for tolerance lookups by absolute value."""

    def __init__(self, candidates: list[Candidate]) -> None:
        self._items = sorted(((abs(c.value), c) for c in candidates), key=lambda t: t[0])
        self._keys = [k for k, _ in self._items]

    def __len__(self) -> int:
        return len(self._items)

    def lookup(self, value: float, policy: Policy) -> Match | None:
        v = abs(value)
        lo = min(v * (1 - policy.rel_tolerance), v - policy.abs_tolerance)
        hi = max(v * (1 + policy.rel_tolerance), v + policy.abs_tolerance)
        best: Match | None = None
        for k, c in self._between(lo, hi):
            if not policy.close(v, k):
                continue
            err = abs(v - k) / k if k else abs(v - k)
            if best is None or (c.rank, err) < (best.candidate.rank, best.error):
                best = Match(c, err)
        return best

    def lookup_range(self, lo: float, hi: float, policy: Policy) -> Match | None:
        """A stated range is grounded when some candidate lies inside it (with tolerance at the edges)."""
        lo_t = min(lo * (1 - policy.rel_tolerance), lo - policy.abs_tolerance)
        hi_t = max(hi * (1 + policy.rel_tolerance), hi + policy.abs_tolerance)
        best: Match | None = None
        for k, c in self._between(lo_t, hi_t):
            err = 0.0 if lo <= k <= hi else min(abs(k - lo), abs(k - hi)) / max(k, 1e-12)
            if best is None or (c.rank, err) < (best.candidate.rank, best.error):
                best = Match(c, err)
        return best

    def _between(self, lo: float, hi: float) -> list[tuple[float, Candidate]]:
        start = bisect.bisect_left(self._keys, lo)
        end = bisect.bisect_right(self._keys, hi)
        return self._items[start:end]
