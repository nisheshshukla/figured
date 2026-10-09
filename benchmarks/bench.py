"""Run: python benchmarks/bench.py  — microseconds per trace() across result-set sizes."""

from __future__ import annotations

import random
import statistics
import time

from figured import trace

random.seed(7)

TEXT = (
    "California has 39.3 million people (39,346,023), about 10.7 million more than Texas, "
    "which is 72.8% of its size. Median income is $79,243, roughly $10,940 above Nevada, "
    "and the region grew 2.3% while 12% of households moved. Together they hold 68 million."
)


def rows(n: int, cols: int) -> list[dict[str, float | str]]:
    out = []
    for i in range(n):
        r: dict[str, float | str] = {"name": f"row{i}"}
        for c in range(cols):
            r[f"c{c}"] = float(random.randint(1_000, 50_000_000))
        out.append(r)
    out[0].update({"c0": 39346023.0, "c1": 28635442.0, "c2": 79243.4})
    if n > 1:
        out[1].update({"c0": 68303.1, "c1": 220000.0, "c2": 225060.0})
    return out


def bench(label: str, data: object, reps: int) -> None:
    times = []
    for _ in range(reps):
        t = time.perf_counter()
        trace(TEXT, data)
        times.append(time.perf_counter() - t)
    med = statistics.median(times) * 1e6
    print(f"{label:<28} {med:9.0f} µs   (min {min(times) * 1e6:7.0f} µs)")


if __name__ == "__main__":
    bench("2 rows × 3 cols", rows(2, 3), 300)
    bench("12 rows × 5 cols", rows(12, 5), 200)
    bench("200 rows × 10 cols", rows(200, 10), 50)
    bench("2,000 rows × 10 cols", rows(2000, 10), 10)
    bench("2,000 rows, max_cells=400", rows(2000, 10), 5) if False else None
    print()
    t = time.perf_counter()
    r = trace(TEXT, rows(2000, 10), max_cells=400)
    print(f"{'2,000 rows, max_cells=400':<28} {(time.perf_counter() - t) * 1e6:9.0f} µs   ok={r.ok}")
