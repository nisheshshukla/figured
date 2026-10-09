"""Edge cases that keep the less-travelled branches honest."""

from __future__ import annotations

from decimal import Decimal

from figured import Policy, build_evidence, extract_numbers, trace
from figured.derive import Index, fmt


class Floaty:
    def __init__(self, v: float, raise_: bool = False) -> None:
        self.v, self.raise_ = v, raise_

    def __float__(self) -> float:
        if self.raise_:
            raise ValueError("no")
        return self.v


def test_evidence_odd_inputs() -> None:
    ev = build_evidence(results=[None, [{"a": 1.0}]])
    assert [c.value for c in ev.cells] == [1.0]
    ev = build_evidence([{"a": 1.0}, {"b": 2.0}])
    assert ev.results[0].columns == ["a", "b"] and len(ev.cells) == 2
    ev = build_evidence((1.0, 2.0) for _ in range(1))
    assert [c.value for c in ev.cells] == [1.0, 2.0]
    assert build_evidence([{"x": float("inf")}]).empty
    assert build_evidence([{"x": "1,234"}], parse_strings=False).empty
    ev = build_evidence([{"d": Decimal("2.5"), "f": Floaty(3.0), "bad": Floaty(1.0, raise_=True), "n": None}])
    assert [c.value for c in ev.cells] == [2.5, 3.0]
    assert build_evidence([{"x": Floaty(float("nan"))}]).empty


def test_fmt_and_rank() -> None:
    assert fmt(1e16) == "1e+16" and fmt(1234.5) == "1,234.5" and fmt(0.5) == "0.5"
    r = trace("Exactly 39,346,023.", [{"pop": 39346023}])
    assert r.grounded[0].match is not None and r.grounded[0].match.candidate.rank == 0


def test_derivations_without_cells() -> None:
    rows = [{"a": 1000.0}, {"a": 1200.0}]
    r = trace("A gap of 200.", rows, derivations={"difference"}, ignore_below=0.0)
    assert r.grounded[0].match is not None and r.grounded[0].match.kind == "difference"
    assert trace(
        "A gap of 200.", rows, derivations={"difference"}, ignore_below=0.0, max_rows=0
    ).ungrounded == ["200"]
    assert trace("Exactly 1,200.", rows, derivations=set()).ungrounded == ["1,200"]


def test_zero_and_negative_cells_in_pairwise_search() -> None:
    rows = [{"a": 0.0, "b": 5000.0, "c": 10000.0}]
    r = trace("Ratio of 2.0 to one.", rows, ignore_below=0.0)
    assert r.grounded[0].match is not None and r.grounded[0].match.kind == "ratio"
    rows = [{"start": -200000.0, "end": -210000.0}]
    r = trace("It fell 5% over the period.", rows)
    assert r.grounded[0].match is not None and r.grounded[0].match.kind == "percent_change"
    rows = [{"a": 0.0, "b": 0.0}]
    assert trace("Grew 5% this year.", rows).ok


def test_range_against_derived_values() -> None:
    rows = [{"state": "CA", "pop": 39346023}, {"state": "TX", "pop": 28635442}]
    r = trace("Between 10 and 11 million more people.", rows)
    assert r.ok and all(x.match is not None and x.match.kind == "difference" for x in r.grounded)
    r = trace("Between 40% and 50% larger.", rows)
    assert r.ok


def test_range_scale_without_space() -> None:
    a, b = extract_numbers("40 to 50M units")
    assert a.value == 40e6 and b.value == 50e6


def test_no_evidence_ignores_years_and_percents() -> None:
    r = trace("In 2020 about 12% changed.", None)
    assert r.checked == 0 and [x.reason for x in r.ignored] == ["year", "percent without evidence"]
    assert r.ignored[0].to_dict()["reason"] == "year"


def test_policy_close_with_zero() -> None:
    p = Policy()
    assert p.close(0.0, 0.0) and not p.close(1.0, 0.0)
    assert Policy(abs_tolerance=2.0).close(1.0, 0.0)


def test_index_can_be_reused_across_texts() -> None:
    ev = build_evidence([{"pop": 39346023}, {"pop": 28635442}])
    ix = Index(ev, Policy())
    assert ix.lookup(10.7e6, Policy()) is not None
    assert ix.lookup(99e6, Policy()) is None
    assert ix.lookup_range(10e6, 11e6, Policy()) is not None
