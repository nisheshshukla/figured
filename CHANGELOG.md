# Changelog

## 0.1.2

- Documentation only: the derivation table, CLI example, and judge example use the sales dataset.

## 0.1.1

- Currency figures are always checked, regardless of size. A "$71" average order value is a claim about the data, not a count of things, and the small-number rule no longer skips it.
- Derived values are found per figure by solving for the partner cell and bisecting, instead of enumerating every pair up front. Typical answers check in about 150 µs; 2,000-row results in about 9 ms.
- A percentage is searched only as a percentage and a plain figure only as a ratio, removing a class of coincidental matches.
- Adjacent-cell sums cover the first `max_rows` rows, like the pairwise search.
- README and examples use a generic sales dataset; the benchmark script is in `benchmarks/`.

## 0.1.0

First release.

- `trace(text, rows)` checks every number in a text against the rows it was written from.
- Derived values: column sums, adjacent-cell sums, differences, ratios, percentages, and percent change, within and across rows.
- Number extraction with thousands separators, decimals, scientific notation, currency, scale words, percent markers, and ranges.
- Evidence from lists of dicts, lists of sequences, `{columns, rows}` mappings, pandas DataFrames, and DB-API cursors, with numeric-string parsing.
- Per-figure report with the matching derivation spelled out, a one-line caveat, JSON output, and a CLI that exits non-zero on untraceable figures.
- Optional `[judge]` extra for a model-based second opinion on comparative words.
- Language-neutral conformance vectors in `tests/vectors/`.
