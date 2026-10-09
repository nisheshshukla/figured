from __future__ import annotations

import pytest

from figured import LENIENT, STRICT, Policy, trace

ROWS = [{"state": "Utah", "income": 79243.4}, {"state": "Nevada", "income": 68303.1}]


def test_report_shape_and_explanations() -> None:
    r = trace("Utah's median is $79,243, about $10,940 above Nevada.", ROWS)
    assert r.ok and r.checked == 2 and r.evidence_cells == 2
    kinds = {x.literal: x.match.kind for x in r.grounded if x.match}
    assert kinds == {"$79,243": "cell", "$10,940": "difference"}
    diff = next(x for x in r.grounded if x.literal == "$10,940")
    assert diff.match is not None and "income[Utah] − income[Nevada]" in diff.match.explanation
    text = r.explain()
    assert text.startswith("OK · 2 checked") and "difference" in text
    d = r.to_dict()
    assert d["ok"] is True and d["figures"][1]["match"]["kind"] == "difference"
    assert repr(r) == "Report(ok=True, checked=2, ungrounded=[])"


def test_caveat_names_figures_and_counts_overflow() -> None:
    r = trace("Figures: 111,111 and 222,222 and 333,333 and 444,444 and 555,555 and 666,666.", [{"x": 1.0}])
    assert len(r.ungrounded) == 6
    assert r.caveat().startswith("Note: these figures could not be traced to the data: 111,111, 222,222")
    assert "and 2 more" in r.caveat()
    assert trace("fine", [{"x": 1.0}]).caveat() == ""


def test_derivations_can_be_restricted() -> None:
    text = "Utah is about $10,940 above Nevada."
    assert trace(text, ROWS).ok
    r = trace(text, ROWS, derivations={"cell"})
    assert r.ungrounded == ["$10,940"]


def test_unknown_policy_option_is_an_error() -> None:
    with pytest.raises(TypeError, match="unknown policy option"):
        trace("x", ROWS, tolerance=0.1)


def test_policy_objects_and_overrides_compose() -> None:
    assert STRICT.rel_tolerance < Policy().rel_tolerance < LENIENT.rel_tolerance
    text = "About 12% of households, roughly $79,900 median."
    assert trace(text, ROWS, policy=LENIENT).ok
    r = trace(text, ROWS, policy=STRICT)
    assert r.ungrounded == ["12%", "$79,900"]
    assert trace(text, ROWS, policy=STRICT, rel_tolerance=0.01).ungrounded == ["12%"]


def test_abs_tolerance_helps_small_values() -> None:
    rows = [{"share": 0.31}]
    assert trace("share of 0.3", rows, ignore_below=0.0).ungrounded == ["0.3"]
    assert trace("share of 0.3", rows, ignore_below=0.0, abs_tolerance=0.02).ok


def test_ignore_below_and_years_are_configurable() -> None:
    rows = [{"n": 5.0}]
    assert trace("5 of 2020", rows).checked == 0
    r = trace("5 of 2020", rows, ignore_below=0.0, ignore_years=False)
    assert r.checked == 2 and r.ungrounded == ["2020"]


def test_pairwise_cap_limits_work_not_correctness_of_cells() -> None:
    rows = [{"v": float(i * 1000)} for i in range(1, 200)]
    r = trace("The row holds 150,000.", rows, max_rows=5, max_cells=5)
    assert r.ok and r.grounded[0].match is not None and r.grounded[0].match.kind == "cell"


def test_best_match_prefers_simplest_derivation() -> None:
    rows = [{"a": 1000000.0, "b": 2000000.0, "c": 1000000.0}]
    r = trace("The value is 1,000,000.", rows)
    assert r.grounded[0].match is not None and r.grounded[0].match.kind == "cell"


def test_ratio_kind_for_plain_fraction() -> None:
    rows = [{"a": 300.0, "b": 100.0}]
    r = trace("The ratio is 3.0 to one, which is 300 units.", rows, ignore_below=0.0)
    kinds = [x.match.kind for x in r.grounded if x.match]
    assert kinds[0] == "ratio" and kinds[1] == "cell"
