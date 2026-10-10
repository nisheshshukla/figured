"""Performance regression guards. Absolute budgets are loose, so slow CI machines pass; the ratio
test is the real guard: a lookup must not get slower as the session grows, which is what the
token index is for."""

from __future__ import annotations

import json
import random
import statistics
import time

import pytest

from figured import trace
from figured.agents import RunMonitor

pytestmark = pytest.mark.perf


def session(n_results: int, size: int = 2_000) -> tuple[RunMonitor, list[str]]:
    rng = random.Random(1)
    m = RunMonitor()
    m.user("My user id is u_123456 and I want to refund order ORD-1000042.")
    ids: list[str] = []
    for i in range(n_results):
        items = []
        while len(json.dumps(items)) < size:
            oid = f"ORD-{rng.randint(1_000_000, 9_999_999)}"
            ids.append(oid)
            items.append(
                {"id": oid, "price": round(rng.uniform(5, 900), 2), "date": f"2026-10-0{rng.randint(1, 9)}"}
            )
        m.tool_result(f"search_{i}", {"items": items})
    return m, ids


def median_call_us(m: RunMonitor, ids: list[str], n: int = 300) -> float:
    rng = random.Random(2)
    times = []
    for _ in range(n):
        args = {"order_id": rng.choice(ids), "amount": round(rng.uniform(5, 900), 2), "date": "2026-10-05"}
        t = time.perf_counter()
        m.before_call("refund", args)
        times.append((time.perf_counter() - t) * 1e6)
    return statistics.median(times)


def test_lookup_cost_does_not_grow_with_the_session() -> None:
    small = median_call_us(*session(10))
    large = median_call_us(*session(200))
    assert large < 4 * small + 50, f"10 sources: {small:.0f} µs, 200 sources: {large:.0f} µs"
    assert large < 2_000, f"200 sources: {large:.0f} µs median"


def test_before_call_budget_on_a_short_session() -> None:
    m, ids = session(5)
    assert median_call_us(m, ids) < 1_000


def test_trace_budget() -> None:
    rows = [{"r": f"row{i}", "revenue": 1000 + 37 * i, "orders": 10 + i} for i in range(12)]
    text = "Revenue was 1,444 in one row and 13,700 overall, about 8.5% from the top row."
    times = []
    for _ in range(200):
        t = time.perf_counter()
        trace(text, rows)
        times.append((time.perf_counter() - t) * 1e6)
    assert statistics.median(times) < 5_000


def test_repeated_values_are_memoized() -> None:
    m, ids = session(100)
    first = median_call_us(m, ids[:1], n=50)  # the same value every time after the first
    assert first < 1_000
