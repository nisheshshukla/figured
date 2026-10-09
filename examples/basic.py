"""Run: python examples/basic.py"""

from figured import trace

rows = [
    {"state": "California", "population": 39_346_023, "moe": 79_849},
    {"state": "Texas", "population": 28_635_442, "moe": 65_120},
]

answer = (
    "California has about 39.3 million people (±79,849), roughly 10.7 million more than Texas. "
    "Texas is 72.8% of California's size. Combined they hold 68 million people, "
    "and about 4.1 million of them moved last year."
)

report = trace(answer, rows)
print(report.explain())
print()
print(report.caveat() or "Every figure traced.")
