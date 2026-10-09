"""Normalize whatever the caller has (rows, dicts, a DataFrame, a cursor) into numeric cells."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

_NUMERIC_STRING = re.compile(r"^[-+]?[$€£¥]?\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?%?$")


@dataclass(frozen=True)
class Cell:
    value: float
    column: str
    row: int
    result: int
    label: str

    def ref(self) -> str:
        return f"{self.column}[{self.label}]"


@dataclass
class ResultSet:
    columns: list[str]
    rows: list[list[Cell]]
    index: int


@dataclass
class Evidence:
    results: list[ResultSet] = field(default_factory=list)

    @property
    def cells(self) -> list[Cell]:
        return [c for rs in self.results for row in rs.rows for c in row]

    @property
    def empty(self) -> bool:
        return not self.cells


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
        return cols, [list(r) if not isinstance(r, Mapping) else _dict_row(r, cols) for r in obj["rows"]]
    if hasattr(obj, "to_dict") and hasattr(obj, "columns"):
        records = obj.to_dict("records")
        cols = [str(c) for c in obj.columns]
        return cols, [[rec.get(c) for c in cols] for rec in records]
    if hasattr(obj, "fetchall") and hasattr(obj, "description"):
        cols = [str(d[0]) for d in obj.description or []]
        return cols, [list(r) for r in obj.fetchall()]
    rows = list(obj)
    if not rows:
        return [], []
    if isinstance(rows[0], Mapping):
        keys: list[str] = []
        for r in rows:
            for k in r:
                if str(k) not in keys:
                    keys.append(str(k))
        return keys, [[r.get(c) for c in keys] for r in rows]
    if isinstance(rows[0], str | bytes | int | float | Decimal):
        return [], [[r] for r in rows]
    return [], [list(r) for r in rows]


def _dict_row(r: Mapping[str, Any], cols: list[str]) -> list[Any]:
    return [r.get(c) for c in cols] if cols else list(r.values())


def _result_set(
    columns: list[str], raw_rows: list[Sequence[Any]], index: int, parse_strings: bool
) -> ResultSet:
    width = max((len(r) for r in raw_rows), default=0)
    if len(columns) < width:
        columns = columns + [f"c{i}" for i in range(len(columns), width)]
    rows: list[list[Cell]] = []
    for ri, raw in enumerate(raw_rows):
        label = _row_label(raw, ri)
        cells = []
        for ci, v in enumerate(raw):
            num = to_number(v, parse_strings)
            if num is not None:
                cells.append(Cell(num, columns[ci], ri, index, label))
        rows.append(cells)
    return ResultSet(columns, rows, index)


def _row_label(raw: Sequence[Any], ri: int) -> str:
    for v in raw:
        if isinstance(v, str) and v.strip() and not _NUMERIC_STRING.match(v.strip()):
            return v.strip()[:40]
    return f"row {ri}"


def to_number(v: Any, parse_strings: bool = True) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, Decimal):
        return float(v) if v.is_finite() else None
    if isinstance(v, str):
        s = v.strip()
        if parse_strings and _NUMERIC_STRING.match(s):
            s = (
                s.rstrip("%")
                .replace(",", "")
                .replace("$", "")
                .replace("€", "")
                .replace("£", "")
                .replace("¥", "")
            )
            try:
                return float(s)
            except ValueError:
                return None
        return None
    if hasattr(v, "__float__"):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None
    return None
