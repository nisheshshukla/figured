"""Run: python examples/basic.py"""

from figured import trace

rows = [
    {"region": "North America", "revenue": 4_820_000, "orders": 61_300},
    {"region": "Europe", "revenue": 3_150_000, "orders": 47_900},
    {"region": "APAC", "revenue": 1_930_000, "orders": 35_100},
]

answer = (
    "North America brought in $4.82M, about 53% more than Europe, and the three regions "
    "combined reached $9.9M on 144,300 orders. Average order value in APAC was $71."
)

report = trace(answer, rows)
print(report.explain())
print()
print(report.caveat() or "Every figure traced.")
