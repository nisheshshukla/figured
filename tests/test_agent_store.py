from __future__ import annotations

import datetime as dt
import json

import pytest

from figured.agents.store import SourceStore, _numbers_in
from figured.extract import extract_numbers, scan_values
from figured.policy import Policy

TRICKY = [
    "40 to 50 million",
    "between 39 and 40 million people",
    "10 to 12 percent of households",
    "$5M-$10M raised",
    "-4.5 million and −3 bn",
    "12 T-shirts, 3k users, 1.2e6 rows",
    "from 2019 to 2020",
    "1,234.56 and 7 or 8k",
    "x-$5, - $5, a-5",
    "ORD-1234567 on 2026-10-15",
    "3 percentage points, 4 pct",
    "2nd place, 5 m tall",
]


@pytest.mark.parametrize("text", TRICKY)
def test_scan_values_matches_extract_numbers(text: str) -> None:
    assert scan_values(text) == [f.value for f in extract_numbers(text)]


def store() -> SourceStore:
    return SourceStore(Policy(max_rows=40, max_cells=60), dt.date(2024, 5, 15))


def test_phrases_exact_composed_and_missing() -> None:
    s = store()
    s.add("user", "user", 1, "Ship to 123   Elm\nStreet please, and keep unit 4B")
    hit = s.find("phrase", "123 Elm Street")
    assert hit is not None and hit.how == "exact"
    s.add("tool", "tool:profile", 2, '{"line": "Suite 491"}')
    composed = s.find("phrase", "Suite 491, 123 Main")
    assert composed is not None and composed.how == "composed"
    assert s.find("phrase", "999 Nowhere Road") is None
    assert s.find("phrase", "Main Street") is None


def test_dates_year_inferred_filters_and_shifts() -> None:
    s = store()
    s.add("user", "user", 1, "I fly on May 19 and want to move it a day later.")
    s.add("tool", "tool:res", 2, '{"date": "2024-06-01"}')
    hit = s.find("date", "2024-05-19")
    assert hit is not None and hit.how == "date (year inferred)"
    assert s.find("date", "2024-06-01", {"user"}) is None
    shifted = s.find("date", "2024-06-02", {"tool"})
    assert shifted is not None and shifted.how == "derived:date shift (+1 days)"
    assert s.find("date", "2024-06-02", {"system"}) is None
    assert s.find("date", "not a date") is None
    s.add("tool", "tool:odd", 3, "on 02/30/2024")
    assert s.find("date", "2024-03-30") is None


def test_shift_needs_a_year_somewhere() -> None:
    s = SourceStore(Policy(), None)
    s.add("user", "user", 1, "Move my May 3 trip a day later.")
    assert s.find("date", "May 4") is None


def test_numbers_zero_filters_count_and_digits() -> None:
    s = store()
    s.add("tool", "tool:quote", 1, '{"fee": 0, "price": 174, "ref": 1656367028}')
    s.add("user", "user", 2, "my zip is 98101")
    assert s.find("number", 0.0) is not None
    assert s.find("number", 174.0, {"user"}) is None
    exact = s.find("number", 174.0, {"tool"})
    assert exact is not None and exact.how == "exact"
    count = s.find("number", 522.0, strict=True)
    assert count is not None and count.how == "derived:count" and "× 3" in count.explanation
    assert s.find("number", 98101.0, {"user"}) is not None
    assert s.find("number", 1656367028.0, {"tool:quote"}) is not None
    assert s.find("number", 177.0, strict=True) is None


def test_numbers_derived_with_and_without_filters() -> None:
    s = store()
    s.add("tool", "tool:a", 1, '{"x": 1000, "y": 250}')
    s.add("tool", "tool:b", 2, '{"z": 40}')
    total = s.find("number", 1290.0)
    assert total is not None and total.how == "derived:column_sum"
    filtered_total = s.find("number", 1250.0, {"tool:a"})
    assert filtered_total is not None and filtered_total.how == "derived:column_sum"
    diff = s.find("number", 960.0)
    assert diff is not None and diff.how == "derived:difference"
    again = s.find("number", 960.0)
    assert again is not None and again.how == "derived:difference"
    filtered = s.find("number", 750.0, {"tool:a"})
    assert filtered is not None and filtered.how == "derived:difference"
    assert s.find("number", 960.0, {"tool:a"}) is None
    assert s.find("number", 5.0, {"system"}) is None


def test_pair_window_and_index_cap() -> None:
    s = store()
    s.add("tool", "tool:big", 1, json.dumps({"v": list(range(1000, 1100))}))
    hit = s.find("number", 2075.0)
    assert hit is not None and hit.how == "derived:sum"
    capped = SourceStore(Policy(), None, max_index_chars=20)
    capped.add("tool", "tool:t", 1, '{"a": 1, "padding": "xxxxxxxxxxxxxxxx", "b": 777777}')
    assert capped.sources[0].numbers == [1.0] and capped.find("number", 777777.0, strict=True) is not None


def test_numbers_in_handles_bad_and_deep_json() -> None:
    assert _numbers_in('{"a": 1, "b": [2, "3.5 million", "1234567890"], "c": true}') == [3.5e6, 2.0, 1.0]
    assert _numbers_in("[not json 42") == [42.0]
    deep = "[" * 100_000 + "]" * 100_000
    assert _numbers_in(deep) == []
    s = store()
    s.add("tool", "tool:x", 1, "")
    s.add("tool", "tool:x", 1, "", numbers=[3.0])
    assert len(s.sources) == 1
