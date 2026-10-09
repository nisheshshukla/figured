# Changelog

## 0.1.0

First release.

- `trace(text, rows)` checks every number in a text against the rows it was written from.
- Derived values: column sums, adjacent-cell sums, differences, ratios, percentages, and percent change, within and across rows.
- Number extraction with thousands separators, decimals, scientific notation, currency, scale words, percent markers, and ranges.
- Evidence from lists of dicts, lists of sequences, `{columns, rows}` mappings, pandas DataFrames, and DB-API cursors, with numeric-string parsing.
- Per-figure report with the matching derivation spelled out, a one-line caveat, JSON output, and a CLI that exits non-zero on untraceable figures.
- Optional `[judge]` extra for a model-based second opinion on comparative words.
- Language-neutral conformance vectors in `tests/vectors/`.
