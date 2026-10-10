"""Isolation and bounded state: monitors share nothing, threads do not interfere, and per-run caches
stay bounded."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from figured.agents import RunMonitor
from figured.agents.store import SourceStore
from figured.policy import Policy


def run(seed: int) -> str:
    m = RunMonitor()
    m.user(f"My order is ORD-{1000000 + seed} and my zip is {10000 + seed}.")
    m.tool_result("get", {"order": f"ORD-{1000000 + seed}", "total": 10 + seed})
    m.before_call("refund", {"order_id": f"ORD-{1000000 + seed}", "amount": 10 + seed})
    m.before_call("refund", {"order_id": f"ORD-{2000000 + seed}", "amount": 99})
    return json.dumps(m.report().to_dict(), sort_keys=True, default=str)


def test_threads_give_the_same_reports_as_sequential() -> None:
    sequential = [run(i) for i in range(40)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        threaded = list(pool.map(run, range(40)))
    assert threaded == sequential


def test_monitors_do_not_leak_into_each_other() -> None:
    a, b = RunMonitor(), RunMonitor()
    a.user("order ORD-1111111")
    assert b.before_call("get", {"order_id": "ORD-1111111"}).action == "warn"
    assert a.before_call("get", {"order_id": "ORD-1111111"}).action == "allow"


def test_lookup_memo_is_bounded() -> None:
    s = SourceStore(Policy(), None)
    s.add("user", "user", 1, "nothing")
    for i in range(20_050):
        s.find("identifier", f"ID-{i:07d}")
    assert len(s._memo) <= 20_001


def test_report_is_cumulative_and_decision_is_per_call() -> None:
    m = RunMonitor()
    m.user("order ORD-1111111")
    first = m.before_call("get", {"order_id": "ORD-1111111"})
    second = m.before_call("get", {"order_id": "ORD-9999999"})
    assert len(first.checks) == 1 and len(second.checks) == 1
    assert len(m.report().checks) == 2 and m.report().tool_calls == 2
