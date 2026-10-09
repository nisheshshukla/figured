# figured

**Show your work.** Verify that every number in an LLM-generated answer traces to the rows it was written from.

```python
from figured import trace

rows = [{"state": "California", "pop": 39_346_023}, {"state": "Texas", "pop": 28_635_442}]
answer = "California has 39.3 million people, about 10.7 million more than Texas, and 4.1 million of them moved last year."

report = trace(answer, rows)
report.ok  # False
report.ungrounded  # ['4.1 million']
print(report.explain())
```

```
UNGROUNDED · 3 checked · 1 untraceable
  ✓ 39.3 million     cell           pop[California] = 39,346,023
  ✓ 10.7 million     difference     pop[California] − pop[Texas] = 39,346,023 − 28,635,442 = 10,710,581
  ✗ 4.1 million      no cell, sum, difference, or ratio within tolerance
```

Zero dependencies. Deterministic. About 150 µs for a typical answer, 1 ms for 200 rows. Python 3.10+.

```bash
pip install figured
```

## Why

Text-to-SQL agents and RAG-over-tables pipelines validate the query and trust the prose. The model reads the rows and writes a paragraph, and nothing checks that the paragraph's numbers came from the rows. When it invents a figure, the SQL was fine, the rows were fine, and the user sees a confident wrong number.

The usual answer is an LLM judge, which is slow, costs money per answer, and is itself wrong sometimes: in one published test, a faithfulness metric scored a fabricated price as fully faithful five times in a row. `figured` is the deterministic check that runs on every answer before a judge is needed. It is the "grounding" step the authors of this library shipped inside a Census data agent, extracted so anyone can use it.

## What counts as grounded

Every substantive number in the text must be within a tolerance (default 1.5 percent) of something the rows could legitimately produce:

| Derivation | Example | Explanation you get back |
|---|---|---|
| cell | "39,346,023 people" | `pop[California] = 39,346,023` |
| column sum | "together, 1,000,000 residents" | `sum of pop over 3 rows = 1,000,000` |
| adjacent-cell sum | "the three youngest bands total 1,200" | `a..c[row 0] summed = 1,200` |
| difference | "10.7 million more than Texas" | `pop[California] − pop[Texas] = ... = 10,710,581` |
| ratio | "3.0 to one" | `a[row 0] ÷ b[row 0] = 3` |
| percent | "72.8% of California" | `pop[Texas] ÷ pop[California] = 72.8%` |
| percent change | "grew 2.3%" | `(y2020 − y2019) ÷ y2019 = 2.3%` |

Differences, ratios, and percentages are searched within a row and across rows. A stated range such as "between 39 and 40 million" is grounded when a candidate lies inside it. Numbers at or below 100 and bare four-digit years are ignored by default, because "top 5 counties in 2020" is not a claim about the data.

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

Numeric strings in the rows are parsed by default, so `"39,346,023"`, `"$1,200"`, and `"12%"` all count. Decimals from database drivers are handled. Booleans are not numbers.

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
| `ignore_below` | 100 | figures at or below this are counts of things, not claims |
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
figured "California has 39.3 million people." --rows rows.json
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

**With a model judge for the rest.** Arithmetic cannot see a wrong word around a right number: "Nevada is richer than Utah" with the two correct medians reversed passes. The optional `judge` extra sends the question, the rows, and the answer to a model and returns a strict verdict on faithfulness, responsiveness, and caveats:

```bash
pip install "figured[judge]"
```

```python
from figured.judge import judge

judge("Which state is richer?", answer, rows)  # {"verdict": "fail", "issues": ["comparison reversed"], ...}
```

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

## Origin

Built inside a Census data agent whose answers had to be traceable to the ACS rows behind them. The first version only derived values within a row, so a correct "about $10,900 higher" comparison across two state rows was flagged as suspect. That false flag is now a named test vector, and it is why the defaults lean toward trusting the model when the arithmetic works out.

## License

MIT.
