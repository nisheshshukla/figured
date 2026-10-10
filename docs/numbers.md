# figured.trace: reference


## What counts as grounded

Every substantive number in the text must be within a tolerance (default 1.5 percent) of something the rows could legitimately produce:

Default derivations are cells, column sums and means, a cell's share of its column total (for percentages), and adjacent-cell row sums. The pairwise kinds below need `derivations="all"`.

| Derivation | Example | Explanation you get back |
|---|---|---|
| cell | "$4,820,000 in revenue" | `revenue[North America] = 4,820,000` |
| column sum | "combined, $9.9M" | `sum of revenue over 3 rows = 9,900,000` |
| adjacent-cell sum | "the first three quarters total 1,200" | `q1..q3[Widgets] summed = 1,200` |
| sum | "North America and Europe together, $7.97M" | `revenue[North America] + revenue[Europe] = 4,820,000 + 3,150,000 = 7,970,000` |
| difference | "$1.67M more than Europe" | `revenue[North America] − revenue[Europe] = 4,820,000 − 3,150,000 = 1,670,000` |
| ratio | "1.28 times Europe's orders" | `orders[North America] ÷ orders[Europe] = 1.28` |
| percent | "Europe is 65% of North America" | `revenue[Europe] ÷ revenue[North America] = 65.35%` |
| percent change | "grew 53%" | `(revenue[North America] − revenue[Europe]) ÷ revenue[Europe] = 53.02%` |

With `derivations="all"`, sums, differences, ratios, and percentages are searched within a row and across rows. A stated range such as "between $9 and $10 million" is grounded when a candidate lies inside it. Plain numbers at or below 100 and bare four-digit years are ignored by default, because "top 5 regions in 2024" is not a claim about the data; a figure with a currency symbol or a percent sign is always checked.

Two rules keep the search honest. A figure written as a percentage is searched as `a ÷ b × 100`, and a plain figure as `a ÷ b`, never both, so "150" cannot pass by coincidentally matching a 150% share. And the pairwise and adjacent-cell derivations cover the first `max_rows` rows (12 by default), which is the part of a result a model has usually read; cells and column sums cover every row. Raise `max_rows` if your prompt includes more.

Each grounded figure carries the derivation that matched, so a reviewer can check it by hand. Each ungrounded figure is named. Nothing blocks: you decide whether to append the caveat, change a badge, or fail a test.

## What it reads

`trace(text, rows)` accepts the rows in whatever shape you already have:

- a list of dicts, as most drivers and ORMs return
- a list of lists or tuples, with or without column names
- a `{"columns": [...], "rows": [...]}` mapping
- a pandas DataFrame
- a DB-API cursor after `execute`
- several result sets at once: `trace(text, results=[rows_a, rows_b])`
- an API or tool response, since a list of JSON objects is a list of dicts

Numeric strings in the rows are parsed by default, so `"4,820,000"`, `"$1,200"`, and `"12%"` all count. Decimals from database drivers are handled. Booleans are not numbers.

## Text it understands

Thousands separators, decimals, scientific notation (`1.2e6`), currency symbols, scale words (`39.3 million`, `2.5bn`, `3k`), percent markers (`12%`, `12 percent`, `3 percentage points`), negatives, and ranges with a shared unit (`40 to 50 million`). Identifiers such as `B01003e1` or request ids are not mistaken for numbers, and ordinals are skipped.

## Tuning

```python
from figured import trace, Policy, STRICT, LENIENT

trace(answer, rows, rel_tolerance=0.005)  # tighter rounding
trace(answer, rows, unmatched_percent="flag")  # a percentage must match something
trace(answer, rows, derivations={"cell", "column_sum"})  # no pairwise arithmetic
trace(answer, rows, policy=STRICT)  # 0.5%, percentages must match, checks down to 10
trace(answer, rows, ignore_below=0, ignore_years=False)
```

| Option | Default | Meaning |
|---|---|---|
| `rel_tolerance` | 0.015 | relative error allowed, covers rounding to three significant figures |
| `abs_tolerance` | 0 | absolute error allowed in addition |
| `ignore_below` | 100 | plain figures at or below this are counts of things, not claims; currency and percent figures are always checked |
| `ignore_years` | True | bare four-digit integers in `year_range` are skipped |
| `unmatched_percent` | "flag" | "pass" lets a percentage that matches nothing through, for answers quoting shares of totals outside the rows |
| `max_rows`, `max_cells` | 12, 40 | how much of the result feeds the pairwise and adjacent-sum search |
| `derivations` | cell, column_sum, column_mean, share, row_sum | which candidate kinds are generated; `"all"` adds difference, sum, ratio, percent, percent_change |
| `parse_strings` | True | coerce numeric strings in the rows |

## Speed

Measured with `python benchmarks/bench.py` on a laptop, one answer with nine figures:

| Result set | Time per check |
|---|---|
| 2 rows × 3 columns | 93 µs |
| 12 rows × 5 columns | 170 µs |
| 200 rows × 10 columns | 1.0 ms |
| 2,000 rows × 10 columns | 9.3 ms |

Nothing is enumerated up front. Cells and column sums are indexed once; differences, ratios, percentages, and percent changes are found per figure by solving for the partner cell and bisecting for it. Explanations are formatted only for the figure that matched. For comparison, a model-based faithfulness judge takes seconds and costs a request.

## Command line

```bash
figured "Revenue reached $4.82M in North America." --rows rows.json
figured - --rows rows.json < answer.txt
figured "..." --rows rows.json --json --tolerance 0.01 --derivations all --allow-unmatched-percent
```

Exit code 1 when any figure is untraceable, so it can gate a pipeline step.

## Using it in a pipeline

**After every answer**, append the caveat and flip a badge:

```python
report = trace(answer, rows)
if not report.ok:
    answer += "\n\n" + report.caveat()
    badge = "check figures"
```

**In promptfoo**, as a Python assertion: see `../examples/promptfoo_assert.py`.

**In DeepEval or any custom metric**, wrap `trace` and return `1 - len(report.ungrounded) / report.checked`.

**As an online metric**, log `report.to_dict()` with the request id. The ungrounded rate per day, per model version, or per prompt version is the number that tells you when something regressed in production.

**With a model judge for the rest.** Arithmetic cannot see a wrong word around a right number: "Europe outperformed North America" with the two correct revenue figures reversed passes. The optional `judge` extra sends the question, the rows, and the answer to a model and returns a strict verdict on faithfulness, responsiveness, and caveats:

```bash
pip install "figured[judge]"
```

The judge calls Claude through the Anthropic SDK, so it needs an API key. Create one at console.anthropic.com under API Keys, then put it in the environment:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

```python
from figured.judge import judge

judge("Which region performed best?", answer, rows)
# {"verdict": "fail", "faithful": False, "responsive": True, "caveats_ok": True,
#  "issues": ["comparison reversed"], "model": "claude-opus-5"}
```

It reads the key from the environment by default. To use a different model, a different key, or a client you already have, pass them in:

```python
import anthropic

judge(question, answer, rows, model="claude-sonnet-5", client=anthropic.Anthropic(api_key="..."))
```

Each call is one model request over the question, up to 30 rows per result set, and the answer. Run `trace` on everything and the judge on a sample; the core library never calls a model.
