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
    composed = s.find("phrase", "Suite 491, 123 Elm")
    assert composed is not None and composed.how == "composed"
    assert s.find("phrase", "Suite 491, 123 Main") is None
    assert s.find("phrase", "999 Nowhere Road") is None
    assert s.find("phrase", "Main Street") is None


def test_dates_year_inferred_filters_and_shifts() -> None:
    s = store()
    s.add("user", "user", 1, "I fly on May 19 and want to move it a day later.")
    s.add("tool", "tool:res", 2, '{"date": "2024-06-01"}')
    hit = s.find("date", "2024-05-19")
    assert hit is not None and hit.how == "date (year inferred from the reference date)"
    assert s.find("date", "2025-05-19") is None
    assert s.find("date", "2024-06-01", {"user"}) is None
    shifted = s.find("date", "2024-06-02", {"tool"})
    assert shifted is not None and shifted.how == "derived:date shift (+1 days)"
    assert s.find("date", "2024-05-31", {"tool"}) is None
    assert s.find("date", "2024-06-02", {"tool"}, shift_ok=False) is None
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
    assert s.find("number", 522.0, strict=True) is None
    assert s.find("number", -174.0, strict=True) is None
    s.add("user", "user", 3, "it is for three people")
    assert s.find("number", 0.0) is not None
    assert s.find("number", 174.0, {"user"}) is None
    exact = s.find("number", 174.0, {"tool"})
    assert exact is not None and exact.how == "exact"
    count = s.find("number", 522.0, strict=True)
    assert count is not None and count.how == "derived:count" and "× 3" in count.explanation
    assert s.find("number", 98101.0, {"user"}) is not None
    assert s.find("number", 1656367028.0, {"tool:quote"}) is not None
    assert s.find("number", 98101.0, strict=True) is None
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
    hit = s.find("number", 2150.0)
    assert hit is not None and hit.how == "derived:sum"
    capped = SourceStore(Policy(), None, max_index_chars=20)
    capped.add("tool", "tool:t", 1, '{"a": 1, "padding": "xxxxxxxxxxxxxxxx", "b": 777777}')
    assert capped.sources[0].numbers == [1.0] and capped.find("number", 777777.0) is not None


def test_numbers_in_handles_bad_and_deep_json() -> None:
    assert _numbers_in('{"a": 1, "b": [2, "3.5 million", "1234567890"], "c": true}') == (
        [3.5e6, 1.0, 2.0],
        [],
        [],
    )
    nums, counts, groups = _numbers_in('{"prices": {"economy": 90}, "seats": 4, "note": "fee $1,250.50"}')
    assert set(nums) == {90.0, 4.0, 1250.5} and counts == [4] and groups == []
    assert _numbers_in('{"items": [{"price": 1, "qty": 2}, {"price": 2.5}], "tax": 3}')[2] == [[1.0, 2.5]]
    assert _numbers_in('{"note": "$5 now, $7 later"}')[2] == [[5.0, 7.0]]
    assert _numbers_in("[not json 42, $7") == ([42.0, 7.0], [], [])
    deep = "[" * 100_000 + "]" * 100_000
    assert _numbers_in(deep) == ([], [], [])
    s = store()
    s.add("tool", "tool:x", 1, "")
    s.add("tool", "tool:x", 1, "", numbers=[3.0])
    assert len(s.sources) == 1


def test_taint_echoes_and_pure_tools() -> None:
    s = store()
    s.add("tool", "tool:get", 1, "Error: order #W9999999 not found, try #W1111111", echoes={"#W9999999"})
    assert s.find("identifier", "#W9999999", strict=True) is None
    assert s.find("identifier", "#W1111111", strict=True) is not None
    mention = s.tainted_mention("identifier", "#W9999999")
    assert mention is not None and mention.label == "tool:get"
    s.add("tool", "tool:calculate", 2, "413", tainted=True)
    assert s.find("number", 413.0, strict=True) is None
    assert s.tainted_mention("number", 413.0) is not None
    s.add("tool", "tool:get", 3, '{"price": 12.5, "on": "2024-06-01"}', echoes={"12.5", "2024-06-01"})
    assert s.find("number", 12.5, strict=True) is None and s.find("date", "2024-06-01") is None


def test_prefix_needs_user_digits_and_a_seen_shape() -> None:
    s = store()
    s.add("user", "user", 1, "Order 9502127 please")
    assert s.find("identifier", "#W9502127", strict=True) is None
    s.add("tool", "tool:list", 2, '{"orders": ["#W1234567"]}')
    hit = s.find("identifier", "#W9502127", strict=True)
    assert hit is not None and hit.how == "composed"
    assert s.find("identifier", "#W95021", strict=True) is None
    assert s.find("identifier", "#W9502127", {"tool"}, strict=True) is None


def test_counts_from_arrays_and_words() -> None:
    s = store()
    s.add("tool", "tool:res", 1, '{"passengers": [{"n": "a"}, {"n": "b"}, {"n": "c"}], "price": 174}')
    assert s.find("number", 522.0, strict=True) is None and not s.has_count(3.0)
    s.add("tool", "tool:res", 2, '{"passengers": 3, "price": 174}')
    hit = s.find("number", 522.0, strict=True)
    assert hit is not None and hit.how == "derived:count" and s.has_count(3.0) and not s.has_count(2.5)
    t = store()
    t.add("system", "system", 1, "Each passenger pays $50. Up to 9 passengers.")
    assert t.find("number", 450.0, strict=True) is None
