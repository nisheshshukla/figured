"""The one function: trace(text, rows) -> Report."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from figured.derive import Index
from figured.evidence import build_evidence
from figured.extract import Figure, extract_numbers
from figured.policy import Policy
from figured.report import Report, Result


def trace(
    text: str,
    rows: Any = None,
    *,
    results: Iterable[Any] | None = None,
    policy: Policy | None = None,
    **overrides: Any,
) -> Report:
    """Check that every substantive number in `text` traces to `rows`.

    `rows` is one result set in any common shape: a list of dicts, a list of sequences, a
    pandas DataFrame, a DB-API cursor, or a {"columns": [...], "rows": [...]} mapping.
    `results` is several of those, for answers written from more than one query.
    Policy options can be passed as keywords: trace(text, rows, rel_tolerance=0.01).
    """
    pol = (policy or Policy()).with_overrides(**overrides)
    ev = build_evidence(rows, results, parse_strings=pol.parse_strings)
    figures = extract_numbers(text)
    if ev.empty:
        return Report(text, [_without_evidence(f, pol) for f in figures], 0)
    index = Index(ev, pol)
    return Report(text, [_check(f, index, pol) for f in figures], ev.size)


def _without_evidence(fig: Figure, pol: Policy) -> Result:
    if _is_year(fig, pol):
        return Result(fig, "ignored", reason="year")
    if fig.is_percent:
        return Result(fig, "ignored", reason="percent without evidence")
    if abs(fig.value) > pol.flag_without_evidence_above:
        return Result(fig, "ungrounded", reason="no evidence")
    return Result(fig, "ignored", reason="small")


def _check(fig: Figure, index: Index, pol: Policy) -> Result:
    if _is_year(fig, pol):
        return Result(fig, "ignored", reason="year")
    if not fig.is_percent and abs(fig.value) <= pol.ignore_below:
        return Result(fig, "ignored", reason="small")
    match = index.lookup(fig.value, pol, is_percent=fig.is_percent)
    if match is None and fig.is_range:
        match = index.lookup_range(*fig.bounds, pol, is_percent=fig.is_percent)
    if match is not None:
        return Result(fig, "grounded", match)
    if fig.is_percent and pol.unmatched_percent == "pass":
        return Result(fig, "ignored", reason="unmatched percent allowed by policy")
    return Result(fig, "ungrounded")


def _is_year(fig: Figure, pol: Policy) -> bool:
    lo, hi = pol.year_range
    return pol.ignore_years and fig.looks_like_year and lo <= fig.value <= hi
