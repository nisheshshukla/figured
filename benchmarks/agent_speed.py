"""Latency of figured.agents per tool call, as an online guardrail would see it.

python benchmarks/agent_speed.py               # tau-bench calls, p50/p99/max per before_call
python benchmarks/agent_speed.py --synthetic   # long sessions and large tool outputs
python benchmarks/agent_speed.py --golden out  # write a fingerprint of every result (regression check)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import agent_eval as ae

from figured.agents import RunMonitor, check_run, events


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def per_call() -> None:
    call_us: list[float] = []
    other_us: list[float] = []
    for name in ae.TAU_FILES:
        for r in ae.load_tau(name):
            m = RunMonitor(ae.POLICY)
            for kind, ev in events(r.messages):
                t = time.perf_counter()
                if kind == "system":
                    m.system(ev["text"])
                elif kind == "user":
                    m.user(ev["text"])
                elif kind == "assistant":
                    m.assistant(ev["text"])
                elif kind == "call":
                    m.before_call(ev["name"], ev["args"], ev.get("id"))
                elif kind == "result":
                    m.tool_result(ev["name"], ev["output"], ev.get("id"))
                dt = (time.perf_counter() - t) * 1e6
                (call_us if kind == "call" else other_us).append(dt)
    for label, xs in (("before_call", call_us), ("other events", other_us)):
        print(
            f"{label:<13} n={len(xs):>6}  p50 {pct(xs, 0.5):7.1f} µs  p90 {pct(xs, 0.9):7.1f} µs  "
            f"p99 {pct(xs, 0.99):8.1f} µs  max {max(xs):9.1f} µs  mean {statistics.fmean(xs):6.1f} µs"
        )


def synthetic() -> None:
    rng = random.Random(1)
    for n_results, size in ((10, 2_000), (50, 2_000), (200, 2_000), (20, 100_000)):
        m = RunMonitor()
        m.user("My user id is u_123456 and I want to refund order ORD-1000042.")
        ids = []
        for i in range(n_results):
            items = []
            while len(json.dumps(items)) < size:
                oid = f"ORD-{rng.randint(1_000_000, 9_999_999)}"
                ids.append(oid)
                items.append(
                    {
                        "id": oid,
                        "price": round(rng.uniform(5, 900), 2),
                        "date": "2026-10-0" + str(rng.randint(1, 9)),
                    }
                )
            m.tool_result(f"search_{i}", {"items": items})
        times = []
        for _ in range(200):
            args = {
                "order_id": rng.choice(ids),
                "amount": round(rng.uniform(5, 900), 2),
                "date": "2026-10-05",
            }
            t = time.perf_counter()
            m.before_call("refund", args)
            times.append((time.perf_counter() - t) * 1e6)
        kb = n_results * size / 1000
        p50, p99 = pct(times, 0.5), pct(times, 0.99)
        label = f"{n_results:>4} tool results x {size // 1000:>3} KB ({kb:>6.0f} KB seen)"
        print(f"{label}: before_call p50 {p50:8.1f} µs  p99 {p99:8.1f} µs")


def golden(path: Path) -> None:
    h = hashlib.sha256()
    for name in ae.TAU_FILES:
        for r in ae.load_tau(name):
            rep = check_run(r.messages, ae.POLICY)
            h.update(json.dumps(rep.to_dict(), sort_keys=True, default=str).encode())
    path.write_text(h.hexdigest() + "\n")
    print("golden", h.hexdigest())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--golden", type=Path)
    a = ap.parse_args()
    if a.golden:
        golden(a.golden)
    elif a.synthetic:
        synthetic()
    else:
        per_call()
