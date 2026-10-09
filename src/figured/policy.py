"""Tunable rules for what counts as grounded."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

DERIVATIONS = ("cell", "column_sum", "row_sum", "difference", "ratio", "percent", "percent_change")


@dataclass(frozen=True)
class Policy:
    """How strictly to match, what to ignore, and which derivations to allow.

    rel_tolerance: relative error allowed between a figure and a candidate (0.015 is 1.5%).
    abs_tolerance: absolute error allowed in addition to the relative one.
    ignore_below: plain figures at or below this value are not checked (counts of items, rankings);
        currency and percent figures are always checked.
    ignore_years: treat bare four-digit integers between year_range as years and skip them.
    unmatched_percent: "pass" lets a percentage through when nothing matches, since shares of a
        total outside the rows are common; "flag" treats it like any other figure.
    max_rows / max_cells: how many rows and flat cells feed the pairwise derivations.
    derivations: which candidate kinds are generated.
    parse_strings: coerce numeric strings in the rows ("39,346,023", "$1,200", "12%").
    flag_without_evidence_above: with no rows at all, figures above this are flagged.
    """

    rel_tolerance: float = 0.015
    abs_tolerance: float = 0.0
    ignore_below: float = 100.0
    ignore_years: bool = True
    year_range: tuple[int, int] = (1900, 2100)
    unmatched_percent: Literal["pass", "flag"] = "pass"
    max_rows: int = 12
    max_cells: int = 40
    derivations: frozenset[str] = frozenset(DERIVATIONS)
    parse_strings: bool = True
    flag_without_evidence_above: float = 1000.0

    def with_overrides(self, **overrides: Any) -> Policy:
        if not overrides:
            return self
        if "derivations" in overrides and not isinstance(overrides["derivations"], frozenset):
            overrides["derivations"] = frozenset(overrides["derivations"])
        unknown = set(overrides) - set(self.__dataclass_fields__)
        if unknown:
            raise TypeError(f"unknown policy option(s): {', '.join(sorted(unknown))}")
        return replace(self, **overrides)

    def close(self, a: float, b: float) -> bool:
        if a == b:
            return True
        if abs(a - b) <= self.abs_tolerance:
            return True
        if b == 0:
            return abs(a) <= self.abs_tolerance
        return abs(a - b) / abs(b) <= self.rel_tolerance


STRICT = Policy(rel_tolerance=0.005, unmatched_percent="flag", ignore_below=10.0)
LENIENT = Policy(rel_tolerance=0.05)
