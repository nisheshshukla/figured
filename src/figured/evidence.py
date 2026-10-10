"""Normalize whatever the caller has (rows, dicts, a DataFrame, a cursor) into numeric cells.

Storage is columnar and flat: one list of values across all result sets, with offsets. Cell
objects are created only for the handful of cells that end up in an explanation.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

_NUMERIC_STRING = re.compile(r"^[-+]?[$€£¥]?\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?%?$")
_MONEY = str.maketrans("", "", ",$€£¥")


@dataclass(frozen=True, slots=True)
class Cell:
    value: float
    column: str
    row: int
    result: int
    label: str

    def ref(self) -> str:
        return f"{self.column}[{self.label}]"


@dataclass(slots=True)
class ResultSet:
    columns: list[str]
    index: int
    values: list[float] = field(default_factory=list)
    col_of: list[int] = field(default_factory=list)
    row_start: list[int] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    col_totals: list[float] = field(default_factory=list)
    col_counts: list[int] = field(default_factory=list)

    @property
    def rows(self) -> list[list[Cell]]:
        return [
            [self.cell(k) for k in range(self.row_start[r], self.row_start[r + 1])]
            for r in range(len(self.labels))
        ]

    def row_of(self, k: int) -> int:
        return bisect_right(self.row_start, k) - 1

    def cell(self, k: int) -> Cell:
        r = self.row_of(k)
        return Cell(self.values[k], self.columns[self.col_of[k]], r, self.index, self.labels[r])


@dataclass(slots=True)
class Evidence:
    results: list[ResultSet] = field(default_factory=list)

    @property
    def cells(self) -> list[Cell]:
        return [rs.cell(k) for rs in self.results for k in range(len(rs.values))]

    @property
    def size(self) -> int:
        return sum(len(rs.values) for rs in self.results)

    @property
    def empty(self) -> bool:
        return self.size == 0


def build_evidence(
    rows: Any = None,
    results: Iterable[Any] | None = None,
    *,
    parse_strings: bool = True,
) -> Evidence:
    """Accept one result set (`rows`) and/or several (`results`) in any common shape."""
    sets: list[Any] = []
    if rows is not None:
        sets.append(rows)
    if results is not None:
        sets.extend(results)
    ev = Evidence()
    for i, obj in enumerate(sets):
        columns, raw_rows = _to_table(obj)
        ev.results.append(_result_set(columns, raw_rows, i, parse_strings))
    return ev


def _to_table(obj: Any) -> tuple[list[str], list[Sequence[Any]]]:
    if obj is None:
        return [], []
    if isinstance(obj, Mapping) and "rows" in obj:
        cols = [str(c) for c in obj.get("columns") or []]
        return cols, [_dict_row(r, cols) if isinstance(r, Mapping) else r for r in obj["rows"]]
    if hasattr(obj, "to_dict") and hasattr(obj, "columns"):
        cols = [str(c) for c in obj.columns]
        return cols, [[rec.get(c) for c in cols] for rec in obj.to_dict("records")]
    if hasattr(obj, "fetchall") and hasattr(obj, "description"):
        return [str(d[0]) for d in obj.description or []], list(obj.fetchall())
    rows = obj if isinstance(obj, list) else list(obj)
    if not rows:
        return [], []
    first = rows[0]
    if isinstance(first, Mapping):
        keys: list[Any] = list(first.keys())
        seen = set(keys)
        for r in rows:
            if len(r) != len(keys) or r.keys() != seen:
                for k in r:
                    if k not in seen:
                        seen.add(k)
                        keys.append(k)
        return [str(k) for k in keys], [[r.get(c) for c in keys] for r in rows]
    if isinstance(first, str | bytes | int | float | Decimal):
        return [], [[r] for r in rows]
    return [], rows


def _dict_row(r: Mapping[str, Any], cols: list[str]) -> list[Any]:
    return [r.get(c) for c in cols] if cols else list(r.values())


def _result_set(
    columns: list[str], raw_rows: list[Sequence[Any]], index: int, parse_strings: bool
) -> ResultSet:
    width = max((len(r) for r in raw_rows), default=0)
    if len(columns) < width:
        columns = columns + [f"c{i}" for i in range(len(columns), width)]
    rs = ResultSet(columns, index)
    values, col_of, row_start, labels = rs.values, rs.col_of, rs.row_start, rs.labels
    totals, counts = [0.0] * width, [0] * width
    push_v, push_c, push_label, push_start = values.append, col_of.append, labels.append, row_start.append
    isfinite = math.isfinite
    for raw in raw_rows:
        push_start(len(values))
        label = ""
        for ci, v in enumerate(raw):
            t = type(v)
            if t is float:
                if not isfinite(v):
                    continue
            elif t is int:
                try:
                    v = float(v)
                except OverflowError:
                    continue
            elif t is str:
                s = v.strip()
                if _NUMERIC_STRING.match(s):
                    if not parse_strings:
                        continue
                    num = _parse_numeric_string(s)
                    if num is None:
                        continue
                    v = num
                else:
                    if not label and s:
                        label = s[:40]
                    continue
            else:
                num = to_number(v, parse_strings)
                if num is None:
                    continue
                v = num
            push_v(v)
            push_c(ci)
            totals[ci] += v
            counts[ci] += 1
        push_label(label or f"row {len(labels)}")
    push_start(len(values))
    rs.col_totals, rs.col_counts = totals, counts
    return rs


def _parse_numeric_string(s: str) -> float | None:
    try:
        return float(s.rstrip("%").translate(_MONEY))
    except ValueError:
        return None


def to_number(v: Any, parse_strings: bool = True) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        try:
            f = float(v)
        except OverflowError:
            return None
        return f if math.isfinite(f) else None
    if isinstance(v, Decimal):
        return float(v) if v.is_finite() else None
    if isinstance(v, str):
        s = v.strip()
        return _parse_numeric_string(s) if parse_strings and _NUMERIC_STRING.match(s) else None
    if hasattr(v, "__float__"):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None
    return None
