"""Everything the rows could legitimately produce, searched on demand rather than enumerated.

Cells, column sums, and adjacent-cell sums are indexed once. Differences, ratios, percentages,
and percent changes are found per figure by solving for the partner cell and bisecting for it,
so the cost is O(cells · log cells) per figure instead of O(cells²) up front.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass

from figured.evidence import Cell, Evidence
from figured.policy import Policy

RANK = {
    "cell": 0,
    "column_sum": 1,
    "column_mean": 1,
    "share": 1,
    "row_sum": 2,
    "difference": 3,
    "sum": 3,
    "ratio": 4,
    "percent": 4,
    "percent_change": 5,
}


@dataclass(frozen=True, slots=True)
class Candidate:
    value: float
    kind: str
    explanation: str

    @property
    def rank(self) -> int:
        return RANK[self.kind]


@dataclass(frozen=True, slots=True)
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


Scorer = Callable[[float], float | None]


class Index:
    """Sorted views over the evidence plus on-demand derivation search."""

    def __init__(self, ev: Evidence, policy: Policy) -> None:
        self.ev = ev
        self.policy = policy
        allowed = policy.derivations
        self.allowed = allowed

        sets = ev.results
        flat_abs: list[float] = []
        self.set_start: list[int] = []
        for rs in sets:
            self.set_start.append(len(flat_abs))
            flat_abs.extend(map(abs, rs.values))
        self.flat_abs = flat_abs
        if "cell" in allowed and flat_abs:
            order = sorted(range(len(flat_abs)), key=flat_abs.__getitem__)
            self.cell_keys = [flat_abs[i] for i in order]
            self.cell_order = order
        else:
            self.cell_keys = []
            self.cell_order = []

        agg: list[tuple[float, str, str]] = []
        if "column_sum" in allowed:
            agg.extend(self._column_sums())
        if "column_mean" in allowed:
            agg.extend(self._column_means())
        agg.sort(key=lambda t: t[0])
        self.agg_keys = [a for a, _, _ in agg]
        self.agg_items = agg
        self._shares_built = "share" not in allowed
        self.share_keys: list[float] = []
        self.share_items: list[tuple[float, int, int, int]] = []
        self._row_sums: list[tuple[float, int, int, int, int]] | None = None
        self._row_sum_keys: list[float] = []

        pair_idx: list[int] = []
        for rs, base in zip(sets, self.set_start, strict=True):
            limit = rs.row_start[min(policy.max_rows, len(rs.row_start) - 1)]
            pair_idx.extend(range(base, base + limit))
        pair_idx = pair_idx[: policy.max_cells]
        signed = self._signed
        pair_idx.sort(key=signed)
        self.pair_vals = [signed(i) for i in pair_idx]
        self.pair_refs = pair_idx
        by_abs = sorted(pair_idx, key=flat_abs.__getitem__)
        self.pair_abs = [flat_abs[i] for i in by_abs]
        self.pair_abs_refs = by_abs

    def _signed(self, g: int) -> float:
        rs_i = bisect_right(self.set_start, g) - 1
        return self.ev.results[rs_i].values[g - self.set_start[rs_i]]

    def cell(self, g: int) -> Cell:
        rs_i = bisect_right(self.set_start, g) - 1
        return self.ev.results[rs_i].cell(g - self.set_start[rs_i])

    def lookup(self, value: float, policy: Policy, *, is_percent: bool = False) -> Match | None:
        v = abs(value)
        tol = max(v * policy.rel_tolerance, policy.abs_tolerance)

        def score(d: float) -> float | None:
            if not policy.close(v, d):
                return None
            return abs(v - d) / d if d else abs(v - d)

        return self._search(v - tol, v + tol, v, score, is_percent)

    def lookup_range(self, lo: float, hi: float, policy: Policy, *, is_percent: bool = False) -> Match | None:
        lo_t = min(lo * (1 - policy.rel_tolerance), lo - policy.abs_tolerance)
        hi_t = max(hi * (1 + policy.rel_tolerance), hi + policy.abs_tolerance)

        def score(d: float) -> float | None:
            if d < lo_t or d > hi_t:
                return None
            return 0.0 if lo <= d <= hi else min(abs(d - lo), abs(d - hi)) / max(d, 1e-12)

        return self._search(lo_t, hi_t, (lo + hi) / 2, score, is_percent)

    def _search(self, lo: float, hi: float, v: float, score: Scorer, is_percent: bool) -> Match | None:
        allowed = self.allowed
        if self.cell_keys:
            m = self._best_sorted(self.cell_keys, lo, hi, score, self._cell_candidate)
            if m:
                return m
        if self.agg_keys:
            m = self._best_sorted(self.agg_keys, lo, hi, score, self._agg_candidate)
            if m:
                return m
        if is_percent and self._share_index():
            m = self._best_sorted(self.share_keys, lo, hi, score, self._share_candidate)
            if m:
                return m
        if "row_sum" in allowed:
            sums = self._row_sum_index()
            if sums:
                m = self._best_sorted(self._row_sum_keys, lo, hi, score, self._row_candidate)
                if m:
                    return m
        if not self.pair_vals:
            return None
        if "difference" in allowed:
            m = self._differences(lo, hi, score)
            if m:
                return m
        if "sum" in allowed and not is_percent:
            m = self._sums(lo, hi, score)
            if m:
                return m
        kind = "percent" if is_percent else "ratio"
        if kind in allowed:
            m = self._ratios(lo, hi, kind, score)
            if m:
                return m
        if "percent_change" in allowed:
            m = self._percent_changes(lo, hi, score)
            if m:
                return m
        return None

    @staticmethod
    def _best_sorted(
        keys: list[float], lo: float, hi: float, score: Scorer, make: Callable[[int], Candidate]
    ) -> Match | None:
        best: tuple[float, int] | None = None
        for i in range(bisect_left(keys, lo), bisect_right(keys, hi)):
            err = score(keys[i])
            if err is not None and (best is None or err < best[0]):
                best = (err, i)
        return Match(make(best[1]), best[0]) if best else None

    def _cell_candidate(self, i: int) -> Candidate:
        c = self.cell(self.cell_order[i])
        return Candidate(c.value, "cell", f"{c.ref()} = {fmt(c.value)}")

    def _agg_candidate(self, i: int) -> Candidate:
        a, kind, text = self.agg_items[i]
        return Candidate(a, kind, text)

    def _row_candidate(self, i: int) -> Candidate:
        assert self._row_sums is not None
        _, rs_index, r, a, b = self._row_sums[i]
        rs = self.ev.results[rs_index]
        s = rs.row_start[r]
        signed = sum(rs.values[s + a : s + b + 1])
        head, tail = rs.columns[rs.col_of[s + a]], rs.columns[rs.col_of[s + b]]
        return Candidate(signed, "row_sum", f"{head}..{tail}[{rs.labels[r]}] summed = {fmt(signed)}")

    def _share_candidate(self, i: int) -> Candidate:
        pct, rs_index, k, r = self.share_items[i]
        rs = self.ev.results[rs_index]
        col = rs.columns[rs.col_of[k]]
        return Candidate(pct, "share", f"{col}[{rs.labels[r]}] ÷ sum of {col} = {fmt(pct)}%")

    def _column_means(self) -> list[tuple[float, str, str]]:
        out: list[tuple[float, str, str]] = []
        for rs in self.ev.results:
            for c, (total, n) in enumerate(zip(rs.col_totals, rs.col_counts, strict=True)):
                if n >= 2:
                    mean = total / n
                    out.append(
                        (abs(mean), "column_mean", f"mean of {rs.columns[c]} over {n} rows = {fmt(mean)}")
                    )
        return out

    def _share_index(self) -> list[float]:
        """Each cell of the first max_rows rows as a percentage of its column total over all rows, what
        "APAC was 19.5% of revenue" means. Built on the first percentage looked up, formatted on a match."""
        if not self._shares_built:
            out: list[tuple[float, int, int, int]] = []
            for rs in self.ev.results:
                vals, starts, col_of = rs.values, rs.row_start, rs.col_of
                for r in range(min(self.policy.max_rows, len(starts) - 1)):
                    for k in range(starts[r], starts[r + 1]):
                        c = col_of[k]
                        total, n = rs.col_totals[c], rs.col_counts[c]
                        if n >= 2 and total:
                            out.append((abs(vals[k] / total * 100), rs.index, k, r))
            out.sort(key=lambda t: t[0])
            self.share_items = out
            self.share_keys = [t[0] for t in out]
            self._shares_built = True
        return self.share_keys

    def _column_sums(self) -> list[tuple[float, str, str]]:
        out: list[tuple[float, str, str]] = []
        for rs in self.ev.results:
            for c, (total, n) in enumerate(zip(rs.col_totals, rs.col_counts, strict=True)):
                if n >= 2:
                    out.append(
                        (abs(total), "column_sum", f"sum of {rs.columns[c]} over {n} rows = {fmt(total)}")
                    )
        return out

    def _row_sum_index(self) -> list[tuple[float, int, int, int, int]]:
        """Adjacent-cell sums (2 to 6 cells) over the first max_rows rows, formatted only on a match."""
        if self._row_sums is None:
            out: list[tuple[float, int, int, int, int]] = []
            for rs in self.ev.results:
                vals, starts, idx = rs.values, rs.row_start, rs.index
                for r in range(min(self.policy.max_rows, len(starts) - 1)):
                    s, e = starts[r], starts[r + 1]
                    n = e - s
                    for i in range(n - 1):
                        total = vals[s + i]
                        for j in range(i + 1, min(n, i + 6)):
                            total += vals[s + j]
                            out.append((abs(total), idx, r, i, j))
            out.sort(key=lambda t: t[0])
            self._row_sums = out
            self._row_sum_keys = [t[0] for t in out]
        return self._row_sums

    def _differences(self, lo: float, hi: float, score: Scorer) -> Match | None:
        """Pairs with |a − b| in [lo, hi]. Both windows slide right as b grows, so two pointers suffice."""
        vals, n = self.pair_vals, len(self.pair_vals)
        best: tuple[float, int, int] | None = None
        s1 = e1 = s2 = e2 = 0
        for j in range(n):
            b = vals[j]
            a_lo, a_hi = b + lo, b + hi
            while s1 < n and vals[s1] < a_lo:
                s1 += 1
            while e1 < n and vals[e1] <= a_hi:
                e1 += 1
            for i in range(s1, e1):
                if i != j:
                    err = score(vals[i] - b)
                    if err is not None and (best is None or err < best[0]):
                        best = (err, i, j)
            a_lo, a_hi = b - hi, b - lo
            while s2 < n and vals[s2] < a_lo:
                s2 += 1
            while e2 < n and vals[e2] <= a_hi:
                e2 += 1
            for i in range(s2, e2):
                if i != j:
                    err = score(b - vals[i])
                    if err is not None and (best is None or err < best[0]):
                        best = (err, i, j)
        if best is None:
            return None
        _, i, j = best
        ca, cb = self.cell(self.pair_refs[i]), self.cell(self.pair_refs[j])
        d = ca.value - cb.value
        return Match(
            Candidate(
                d, "difference", f"{ca.ref()} − {cb.ref()} = {fmt(ca.value)} − {fmt(cb.value)} = {fmt(d)}"
            ),
            best[0],
        )

    def _sums(self, lo: float, hi: float, score: Scorer) -> Match | None:
        """Pairs with a + b in [lo, hi]. As b grows the window for a slides left: two pointers."""
        vals, n = self.pair_vals, len(self.pair_vals)
        best: tuple[float, int, int] | None = None
        s = e = n
        for j in range(n):
            b = vals[j]
            a_lo, a_hi = lo - b, hi - b
            while e > 0 and vals[e - 1] > a_hi:
                e -= 1
            while s > 0 and vals[s - 1] >= a_lo:
                s -= 1
            for i in range(max(s, j + 1), e):
                err = score(abs(vals[i] + b))
                if err is not None and (best is None or err < best[0]):
                    best = (err, i, j)
        if best is None:
            return None
        _, i, j = best
        ca, cb = self.cell(self.pair_refs[i]), self.cell(self.pair_refs[j])
        total = ca.value + cb.value
        return Match(
            Candidate(
                total, "sum", f"{ca.ref()} + {cb.ref()} = {fmt(ca.value)} + {fmt(cb.value)} = {fmt(total)}"
            ),
            best[0],
        )

    def _ratios(self, lo: float, hi: float, kind: str, score: Scorer) -> Match | None:
        """A percent figure is searched as a ÷ b × 100, a plain figure as a ÷ b."""
        scale = 100.0 if kind == "percent" else 1.0
        vals, n = self.pair_abs, len(self.pair_abs)
        best: tuple[float, int, int, str, float] | None = None
        r_lo, r_hi = lo / scale, hi / scale
        if r_hi <= 0:
            return None
        r_lo = max(r_lo, 1e-300)
        s1 = e1 = s2 = e2 = 0
        for j in range(n):
            b = vals[j]
            if b == 0:
                continue
            a_lo, a_hi = b * r_lo, b * r_hi
            while s1 < n and vals[s1] < a_lo:
                s1 += 1
            while e1 < n and vals[e1] <= a_hi:
                e1 += 1
            for i in range(s1, e1):
                if i != j:
                    err = score(vals[i] / b * scale)
                    if err is not None and (best is None or err < best[0]):
                        best = (err, i, j, kind, scale)
            a_lo, a_hi = b / r_hi, b / r_lo
            while s2 < n and vals[s2] < a_lo:
                s2 += 1
            while e2 < n and vals[e2] <= a_hi:
                e2 += 1
            for i in range(s2, e2):
                if i != j and vals[i]:
                    err = score(b / vals[i] * scale)
                    if err is not None and (best is None or err < best[0]):
                        best = (err, j, i, kind, scale)
        if best is None:
            return None
        err, num_i, den_i, kind, scale = best
        ca, cb = self.cell(self.pair_abs_refs[num_i]), self.cell(self.pair_abs_refs[den_i])
        value = abs(ca.value / cb.value) * scale
        text = f"{ca.ref()} ÷ {cb.ref()} = {fmt(value)}" + ("%" if scale == 100.0 else "")
        return Match(Candidate(value, kind, text), err)

    def _percent_changes(self, lo: float, hi: float, score: Scorer) -> Match | None:
        """(a − b) ÷ b × 100 in ±[lo, hi]. Positive b slides monotonically; negatives fall back to bisect."""
        vals, n = self.pair_vals, len(self.pair_vals)
        f_lo, f_hi = lo / 100, hi / 100
        best: tuple[float, int, int] | None = None
        s1 = e1 = s2 = e2 = 0
        for j in range(n):
            b = vals[j]
            if b == 0:
                continue
            if b > 0:
                a_lo, a_hi = b * (1 + f_lo), b * (1 + f_hi)
                while s1 < n and vals[s1] < a_lo:
                    s1 += 1
                while e1 < n and vals[e1] <= a_hi:
                    e1 += 1
                r1 = range(s1, e1)
                a_lo, a_hi = b * (1 - f_hi), b * (1 - f_lo)
                while s2 < n and vals[s2] < a_lo:
                    s2 += 1
                while e2 < n and vals[e2] <= a_hi:
                    e2 += 1
                r2 = range(s2, e2)
            else:
                w1 = sorted((b * (1 + f_lo), b * (1 + f_hi)))
                w2 = sorted((b * (1 - f_hi), b * (1 - f_lo)))
                r1 = range(bisect_left(vals, w1[0]), bisect_right(vals, w1[1]))
                r2 = range(bisect_left(vals, w2[0]), bisect_right(vals, w2[1]))
            for rng in (r1, r2):
                for i in rng:
                    if i != j:
                        err = score(abs((vals[i] - b) / b * 100))
                        if err is not None and (best is None or err < best[0]):
                            best = (err, i, j)
        if best is None:
            return None
        _, i, j = best
        ca, cb = self.cell(self.pair_refs[i]), self.cell(self.pair_refs[j])
        pc = (ca.value - cb.value) / cb.value * 100
        return Match(
            Candidate(pc, "percent_change", f"({ca.ref()} − {cb.ref()}) ÷ {cb.ref()} = {fmt(pc)}%"),
            best[0],
        )
