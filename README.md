# figured

**Show your work.** A deterministic online eval for numbers: every figure in an LLM-generated answer is traced to the rows it was written from, on every answer, in microseconds.

```python
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
report.ok  # False
report.ungrounded  # ['$71']
print(report.explain())
```

```
UNGROUNDED · 5 checked · 1 untraceable
  ✓ $4.82M           cell           revenue[North America] = 4,820,000
  ✓ 53%              percent_change (revenue[North America] − revenue[Europe]) ÷ revenue[Europe] = 53.02%
  ✓ $9.9M            column_sum     sum of revenue over 3 rows = 9,900,000
  ✓ 144,300          column_sum     sum of orders over 3 rows = 144,300
  ✗ $71              no cell, sum, difference, or ratio within tolerance
```

APAC's real average order value is $55. The model wrote a fluent sentence with a number that is not in the data and cannot be derived from it, and the other four figures are fine. That is the failure this library exists for.

Zero dependencies. Deterministic. About 150 µs for a typical answer, 1 ms for 200 rows. Python 3.10+.

```bash
pip install figured
```

## Why

Text-to-SQL agents validate the query and trust the prose. The model reads the rows and writes a paragraph, and nothing checks that the paragraph's numbers came from the rows. When it invents a figure, the SQL was fine, the rows were fine, and the user sees a confident wrong number.

The same thing happens wherever a model turns structured data into sentences: BI copilots, finance and KPI narratives, spreadsheet and CSV assistants, agents summarizing an API response. The usual answer is an LLM judge, which is slow, costs money per answer, and is itself wrong sometimes: in one published test, a faithfulness metric scored a fabricated price as fully faithful five times in a row. `figured` is the deterministic check that runs on every answer before a judge is needed.

## Where it sits in an eval stack

Most evaluation of generated text is offline: a fixed dataset, a judge model, a score before deployment. That catches regressions but says nothing about the answer a user is reading right now. `figured` is built to run online, on every production answer, because it is deterministic and costs microseconds. The same call feeds all three layers:

| Layer | What `figured` does there |
|---|---|
| Online eval | Runs on every live answer; the per-figure results go to logs and dashboards, so hallucinated numbers become a rate you can watch and alert on |
| Guardrail | The same result decides what the user sees: a Grounded badge, or a caveat naming the figure that did not trace |
| Offline eval and CI | As an assertion over a test set, it returns the same answer every run, so it can gate a merge |

It is a complement to LLM-as-judge, not a replacement. A judge reads meaning and catches a correct number attached to the wrong claim; it costs a request and seconds, so it runs offline or on a sample. `figured` reads numbers and runs on everything. Together they cover each other's blind spot, and the cheap check is the one that tells you which answers deserve the expensive one.

## What counts as grounded

Every substantive number in the text must be within a tolerance (default 1.5 percent) of something the rows could legitimately produce:

| Derivation | Example | Explanation you get back |
|---|---|---|
| cell | "$4,820,000 in revenue" | `revenue[North America] = 4,820,000` |
| column sum | "combined, $9.9M" | `sum of revenue over 3 rows = 9,900,000` |
| adjacent-cell sum | "the first three quarters total 1,200" | `q1..q3[Widgets] summed = 1,200` |
| difference | "$1.67M more than Europe" | `revenue[North America] − revenue[Europe] = 4,820,000 − 3,150,000 = 1,670,000` |
| ratio | "1.28 times Europe's orders" | `orders[North America] ÷ orders[Europe] = 1.28` |
| percent | "Europe is 65% of North America" | `revenue[Europe] ÷ revenue[North America] = 65.35%` |
| percent change | "grew 53%" | `(revenue[North America] − revenue[Europe]) ÷ revenue[Europe] = 53.02%` |

Differences, ratios, and percentages are searched within a row and across rows. A stated range such as "between $9 and $10 million" is grounded when a candidate lies inside it. Plain numbers at or below 100 and bare four-digit years are ignored by default, because "top 5 regions in 2024" is not a claim about the data; a figure with a currency symbol or a percent sign is always checked.

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
| `unmatched_percent` | "pass" | shares of totals outside the rows are common, so a lone percentage passes |
| `max_rows`, `max_cells` | 12, 40 | how much of the result feeds the pairwise and adjacent-sum search |
| `derivations` | all seven | which candidate kinds are generated |
| `parse_strings` | True | coerce numeric strings in the rows |

## Speed

Measured with `python benchmarks/bench.py` on a laptop, one answer with nine figures:

| Result set | Time per check |
|---|---|
| 2 rows × 3 columns | 150 µs |
| 12 rows × 5 columns | 360 µs |
| 200 rows × 10 columns | 1.1 ms |
| 2,000 rows × 10 columns | 9 ms |

Nothing is enumerated up front. Cells and column sums are indexed once; differences, ratios, percentages, and percent changes are found per figure by solving for the partner cell and bisecting for it. Explanations are formatted only for the figure that matched. For comparison, a model-based faithfulness judge takes seconds and costs a request.

## Command line

```bash
figured "Revenue reached $4.82M in North America." --rows rows.json
figured - --rows rows.json < answer.txt
figured "..." --rows rows.json --json --tolerance 0.01 --strict-percent
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

**In promptfoo**, as a Python assertion: see `examples/promptfoo_assert.py`.

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

## What it does not do

- It cannot catch a correct number attached to the wrong claim. That is what the judge extra is for.
- With large result sets the derived set is big, and a hallucinated figure can land within tolerance of some difference by coincidence. The defaults cap the pairwise search at 12 rows and 40 cells; tighten the tolerance or restrict `derivations` for sensitive uses. A flag on a correct figure is treated as the worse error, because people stop reading badges that cry wolf.
- Numbers written as words ("two million") are not extracted.
- It does not know what the rows mean. If the agent queried the wrong column and described it faithfully, every figure traces.

## How it compares

| | rows as evidence | derived arithmetic | deterministic | names each figure | packaged |
|---|---|---|---|---|---|
| **figured** | yes | sums, differences, ratios, percentages, ranges | yes | yes, with the derivation | pip, zero deps |
| llmground | no, a source string | no | yes | yes | pip |
| @demystify/grounding | no, cited facts | no | yes | yes | npm |
| pcn-core (Proof-Carrying Numbers) | claim values you supply | no | yes | yes, needs model-emitted tags | pip |
| NumProof | yes | yes | yes | yes | hosted API |
| DeepEval / Ragas faithfulness | text context | n/a | no, LLM or NLI | no | pip |

The Proof-Carrying Numbers policy vocabulary (exact, rounded, scale alias, tolerance, percent, range, year) is the clearest statement of the matching problem, and this library borrows its shape. The difference is the evidence contract: rows in, free text in, no cooperation from the model required.

## Ports

Behavior is pinned by the conformance vectors in `tests/vectors/`. A port in another language is correct when it passes them unchanged. A TypeScript port is the natural next one; open an issue if you want to take it.

## Why this exists

Models are being wired into dashboards, reports, and analytics assistants faster than the checks around them. We validate the SQL, cap the rows, and then trust a paragraph the model wrote about the numbers. I think that step deserves a deterministic check that runs on every answer, the way a type checker runs on every build: no model grading a model, no sampling, no cost argument for skipping it. `figured` is that check for numbers. It is small on purpose, so it can be a default rather than a project.

## License

MIT.
