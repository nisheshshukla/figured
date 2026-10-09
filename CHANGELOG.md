# Changelog

## 0.3.0

Agent checks, made honest and fast. An independent review found that 0.2.0's headline overstated what it caught, that several heuristics opened holes, and that unknown transcript formats were reported as clean. This release fixes those and re-measures on held-out data. See `docs/agent-eval-results.md`.

What it claims.

- The claim is now "values absent from context": a value an agent passes to a tool that appears nowhere in what it saw and does not follow from it. Messages say "is not in context".
- Published alongside: real-error recall against ground truth (3.5% on tau-bench, 2.0% on held-out tau2-bench), a substring baseline, and the rate at which a real value in the wrong place passes (100%).

Stricter where it was loose.

- Argument amounts must match to the cent and in sign. A multiple counts only for a count the user stated or a list's length; integers in tool results no longer count as counts.
- An identifier must match as a whole token. A digit run inside another ID no longer vouches for an argument. A `#W`-style prefix is added only to digits the user typed, and only when IDs of that exact shape were seen.
- A date without a year takes the year nearest the reference date. Date shifts follow the direction the user asked for, are not applied to birth dates, and apply only to dates within two years of now.
- A phrase built from parts needs its words too, and its numbers must sit beside words in their source.
- Nothing laundered counts as a source:
  - A total in the agent's own message counts only if its operands were found.
  - `pure_tools` (a calculator) do not vouch for output built from made-up inputs.
  - A tool result does not vouch for a made-up value it echoes back.
  - Examples in the system prompt ("IDs look like #W0000000") are not data.
- A source rule checks any string, with or without digits, such as a password.

Looser where it was wrong.

- Identifiers match regardless of case and separators: `ORD 88213` and `ORD-88213`, IBANs with spaces, `(415) 555-0132` and `+14155550132`.
- URLs match across scheme and `www.`, but not across hosts.
- Allowed amounts now include a stated percentage of an amount (a tip, a tax) and two money fields of one small source added together (two item prices, a price and its tax).
- English number words in what the user says ("two hundred fifty").
- Dates in Spanish, French, German, Portuguese, Italian, and Chinese or Japanese forms, "end of the month", and a reference date written in prose ("Today is Friday, October 9, 2026").
- Values matching a tool schema's `default` or `const` are skipped.
- Numeric strings ("$49.90") are checked as amounts. Integer IDs passed as numbers match their quoted forms.

Formats and robustness.

- `check_run` reads OpenAI Chat Completions (including legacy `function_call` and the `developer` role), the OpenAI Responses API, Anthropic, Gemini, Bedrock Converse, LangChain (objects, dicts, and serialized messages), tau2-bench, and AgentDojo.
- A message in any other shape raises `UnrecognizedMessage`, so a transcript is never reported clean because nothing in it was read. Pass `on_unknown="warn"` or `"ignore"` to relax this.
- Arguments nested deeper than 64 levels produce an `unchecked` finding instead of passing.
- NaN and infinite amounts are unsourced instead of crashing.
- Free-text arguments are scanned for long digit runs, such as an account number in a memo.
- An `"error": null` field no longer counts as an error.

Speed.

- Everything is parsed when a message or tool result arrives, off the critical path. Numbers are indexed in log-scale buckets and dates by month and day.
- JSON string values are scanned in one pass, and per-source indexing is capped at 1 MB.
- `before_call` takes 23 µs at the median and 0.16 ms at p99 on tau-bench. With 400 KB of session text, p99 went from 78 ms in 0.2.0 to 0.42 ms.
- A malformed or deeply nested JSON tool result no longer raises `RecursionError` during indexing.
- `figured.extract.scan_values`: the values `extract_numbers` returns, without building `Figure` objects.

Benchmarks.

- `benchmarks/agent_eval.py` replaces `agent_runs.py` and `agent_groundtruth.py`. It covers tau-bench (development), tau2-bench telecom and AgentDojo (held out), real-error recall, a substring baseline, corruptions, and substitutions. Results are in `benchmarks/results/`.

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
