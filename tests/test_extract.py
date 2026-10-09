from __future__ import annotations

import pytest

from figured import extract_numbers


def values(text: str) -> list[float]:
    return [f.value for f in extract_numbers(text)]


def test_scales_commas_decimals_and_percent() -> None:
    figs = extract_numbers("About 39.3 million people (39,346,023), 12.5% of them, $78,350 income, in 2020.")
    assert [round(f.value) for f in figs] == [39300000, 39346023, 12, 78350, 2020]
    assert figs[2].is_percent and figs[3].is_currency and figs[0].has_scale
    assert figs[4].looks_like_year and not figs[1].looks_like_year


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1.2e6 dollars", [1200000.0]),
        ("2.5bn and 3k and 4M", [2.5e9, 3000.0, 4e6]),
        ("grew 12 percent", [12.0]),
        ("fell 3 percentage points", [3.0]),
        ("−4.5 million", [-4.5e6]),
        ("-1,200", [-1200.0]),
        ("2019-2020", [2019.0, 2020.0]),
        ("B01003e1 and 9bb930dc4b6b", []),
        ("ranks 1st and 22nd", []),
        ("$1,234.56", [1234.56]),
    ],
)
def test_forms(text: str, expected: list[float]) -> None:
    assert values(text) == expected


def test_spans_point_at_the_literal() -> None:
    text = "Roughly 39.3 million people."
    fig = extract_numbers(text)[0]
    assert text[fig.start : fig.end] == fig.literal == "39.3 million"


def test_range_shares_scale_and_links_partners() -> None:
    a, b = extract_numbers("between 39 and 40 million people")
    assert a.value == 39e6 and a.has_scale and a.range_partner == 40e6 and b.range_partner == 39e6
    assert a.bounds == (39e6, 40e6)


def test_range_shares_percent() -> None:
    a, b = extract_numbers("10 to 12 percent of households")
    assert a.is_percent and b.is_percent and a.is_range


def test_years_are_not_ranges() -> None:
    a, b = extract_numbers("from 2019 to 2020")
    assert not a.is_range and not b.is_range


def test_percent_sign_without_space_and_with_space() -> None:
    assert [f.is_percent for f in extract_numbers("12% and 13 %")] == [True, True]
