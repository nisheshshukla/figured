# Changelog

## Unreleased

- Agent checks are faster on long sessions and large tool results, with identical results on all 1,980 benchmark runs (verified by a fingerprint of every report). `before_call` p99 for 400 KB of session text went from 78 ms to 0.41 ms, and the first call after a 3.5 MB tool result from 1.5 s to under 2 ms. Numbers are indexed in log-scale buckets and dates by month and day, all at ingest; JSON string values are scanned in one pass; per-source indexing is capped at 1 MB.
- A malformed or deeply nested JSON tool result no longer raises `RecursionError` during number indexing.
- `figured.extract.scan_values`: the values `extract_numbers` returns, without building `Figure` objects.

## 0.2.0

Agent runs.

- `figured.agents.RunMonitor`: an online, pre-execution check for agent tool calls. Every identifier, email, URL, date, amount, and short address-like phrase in a call's arguments must come from the user, the system prompt, an earlier tool result, or arithmetic over those. Returns allow, warn, or block before the call runs.
- Source rules: per-argument limits on where a value may come from, so a recipient that arrived in a tool result instead of from the user is blocked (taint tracking for indirect prompt injection).
- Loop and budget findings: identical calls repeated, calls retried with the same arguments after an error, and a tool-call budget.
- `figured.agents.check_run`: replays a finished transcript in OpenAI or Anthropic message format through the same monitor, for offline evals and trace review.
- Dates in many forms, relative words against the system prompt's date, lists such as "May 16th or 18th", and shifts the user asked for ("a day later").
- Measured on 1,980 public tau-bench runs: 0.8% of successful runs flagged, all of them real fabrications on review; 2,994 of 2,998 corrupted identifiers caught. See `docs/agent-eval-results.md`.

Answers.

- New derivation: the sum of two cells, within or across rows.
- Fix: a one-letter scale word followed by a hyphenated word is not a scale ("12 T-shirts" is 12, not 12 trillion).

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
