from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from figured import trace

big = st.floats(min_value=1_000, max_value=1e12, allow_nan=False, allow_infinity=False)


@settings(max_examples=200)
@given(big)
def test_a_cell_rendered_with_commas_is_always_grounded(v: float) -> None:
    v = float(round(v))
    assert trace(f"The total is {v:,.0f}.", [{"x": v}]).ok


@settings(max_examples=200)
@given(big, st.floats(min_value=1.2, max_value=50.0))
def test_a_number_far_from_the_only_cell_is_flagged(v: float, factor: float) -> None:
    v = float(round(v))
    w = float(round(v * factor))
    assert trace(f"The total is {w:,.0f}.", [{"x": v}]).ungrounded == [f"{w:,.0f}"]


@settings(max_examples=100)
@given(big, big)
def test_difference_and_sum_of_two_cells_are_grounded(a: float, b: float) -> None:
    a, b = float(round(a)), float(round(b))
    text = f"Gap of {abs(a - b):,.0f}; combined {a + b:,.0f}."
    assert trace(text, [{"a": a}, {"a": b}]).ok


@settings(max_examples=100)
@given(st.text(max_size=200))
def test_never_raises_on_arbitrary_text(text: str) -> None:
    trace(text, [{"x": 1.0}])
    trace(text, None)
