# Changelog

## 0.4.3

Speed, with identical results: a fingerprint of every report over all 1,980 tau-bench runs is unchanged.

- Identifiers, emails, URLs, and phrases are found through an inverted index of every token in every source, built at ingest, instead of a scan of each source's text per lookup. A lookup is a dict hit plus a check of the few sources that hold the needle's rarest token, so cost no longer grows with the session: `before_call` 45 µs at 400 KB of context (was 246 µs) and 76 µs at 2 MB (was 616 µs).
- A looked-up value is memoized per run and only the sources added since are scanned again.
- Pair sums of money fields are indexed at ingest; the same-source sum check is a bucket lookup.
- The date lookup stops at the newest match; `classify` is cached for strings whose schema hint does not affect the kind; policy pattern matches are cached per tool and argument; the repeat-detection key is built from the leaves already walked instead of a JSON encoding.
- On tau-bench: `before_call` 27 µs median, 0.19 ms p99 (0.4.2: 33 µs, 0.22 ms). Ingesting a tool result costs about 15% more for the index.

## 0.4.2

Two independent adversarial reviews of 0.4.1 (`docs/review-0.4.1.md`, with code access; `docs/review-0.4.2-fresh.md`, without) and a version-by-version benchmark comparison. The agent check now beats a 15-line substring baseline on every probe set; the numeric check reports how much a green result means. Plan and status in `docs/plan-0.4.2.md`; all benchmark outputs in `benchmarks/results/v0.4.2/`.

Crashes.

- Text containing `550e8400` (a UUID fragment) or `1e999` was read as scientific notation and crashed indexing with `OverflowError` (since 0.3.0). Exponents are capped and non-finite values skipped.
- `tool_result` with non-string dict keys or a 10,000-deep object, `user`/`system`/`assistant` given `None` or bytes, `trace` given bytes or ints beyond float range, and malformed entries in `tools` no longer raise.

Security (source rules).

- A ruled argument is always checked, whatever it looks like: a seven-word password or a path with spaces from an injected result is blocked.
- A tool result never vouches for the arguments of the call that produced it, so an injected address cannot be laundered through an allowed contacts lookup that echoes its query.
- Composed phrases, date shifts, and server-supplied `readOnlyHint` annotations no longer satisfy a rule.
- Lookalike-character emails (a Cyrillic letter in place of a Latin one) are checked as emails instead of skipped.

Heuristics tightened.

- Schema formats add a prefix only to digits the user typed; dropping a prefix from an ID seen in a result still works.
- Counts come from the user's words and from `quantity`-like fields (`passengers: 3`), not from list lengths. An amount may also be divided by a stated count.
- Percent factors come from the user and system text only. Same-source sums add the same money field across one list's items (two prices), not two different fields (a fee plus a tax).
- A pure tool's output is tainted by any unsourced operand, small ints included. An enum argument is checked when its value is not one of the enum. Period bounds ("August" → 08-31) apply only to bound-named arguments. "The day after tomorrow" no longer arms a one-day shift. A country code is accepted only when the source number had none or the same one. A digit run that is part of a decimal (`12345` in `12,345.00`) is not a match for an ID.
- Removed: the digits-as-amount fallback. Instead a bare run of digits after a money word in the user's text ("send 15000") is an amount, and a digit ID matches its comma-grouped form.

Fewer false flags.

- Dates `YYYY/MM/DD` and `D.M.YYYY`; a day-month without a year also accepts the reference year; numbers with units ("250ml"); underscores as separators; "both", "a pair", "a couple", "a dozen" as counts; read tools matched by the last segment of a server-prefixed name and by camelCase.

Numeric check.

- Default derivations are cells, column sums and means, a cell's share of its column total, and row sums. Pairwise arithmetic is opt-in with `derivations="all"`; on a 12×5 table it accepts 99% of random figures.
- `unmatched_percent` defaults to "flag". `LENIENT` keeps "pass".
- `Report.coincidence()`: the fraction of random figures in the table's range that would be called grounded, printed by `explain()` and the CLI. 10% on the README table, about 70% on a 12×5 table at the default tolerance.
- "X and Y" is a range only after "between".
- CLI: `--derivations all`, `--allow-unmatched-percent` (replaces `--strict-percent`, now the default).

Measured (`docs/agent-eval-results.md`): tau2 airline 2.1% and retail 0.7% of good runs flagged against the baseline's 23.7% and 12.1%; ToolScale 1.3%; AgentDojo 85.8% of successful injections flagged with rules across five models; Toucan unchanged at 41%, the generative-tool limit. Reviewer probes: 25/30, 25/37, 19/21 (baseline 13, 20, 17) and, from a reviewer without code access, 30/30 and 29/30 (baseline 10 and 22). `before_call` 33 µs median, 0.22 ms p99.

## 0.4.1

Fixes for what the clean evaluation of 0.4.0 found (`docs/clean-eval-results.md`).

- ISO timestamps with seconds and a zone (`2025-08-02T23:59:59Z`) are dates, not identifiers.
- Dates written `YY-MM-DD` in tool results (`26-01-01`) are read.
- Periods the user names give range bounds: "August" → 08-01, 08-31, and 09-01; "in 2024"; "this month" and "last month" against the reference date. "The 8th of this month" is a date.
- A string of digits that is not found as an identifier may match an amount written with separators ("50000" for "50,000").
- Read calls (`read_tools`, or the MCP annotation `readOnlyHint`) do not have their range bounds and paging checked (`search_bounds="skip"`): start and end dates, min and max, limit. Identifiers in read calls are still checked.
- AgentDojo's newer transcript format (content blocks keyed `content`) is read.
- `ambiguous_before`, `confirm_before`, and `requires` are documented as experimental.
- On the clean evaluation's data, now development data: ToolScale successful runs flagged 12.8% → 1.3%; tau-bench and tau2 unchanged or lower; AgentDojo Llama 3.3 runs readable.

## 0.4.0

Tool schemas, a confirm verdict, and checks on the call. Built from research into 0.3.0's measured gaps: fix the false flags that have a deterministic fix, and for questions provenance cannot answer, ask instead of guessing. See `docs/agent-eval-results.md`.

- Tool schemas are read for ID formats: a `pattern`, `examples`, or examples in a parameter's description ("such as '#W0000000'"). An agent that adds the stated prefix to digits found in context is not flagged. Only literal affixes are added; the digits must be a whole token in context and their count must match. Off with `schema_formats=False`.
- A call to a tool that is not in `tools` is blocked (`unknown_tool`, `on_unknown_tool`).
- A fourth verdict, `confirm`: hold the call for a person or a verifier. `Decision.needs_confirmation` says to ask; `Decision.allowed` is False for both confirm and block. `RunReport.confirmations` counts them.
- `ambiguous_before`: an identifier chosen from several of the same shape, which the user neither typed nor singled out by value or by an attribute of its record, asks for confirmation.
- `confirm_before`: a call needs the user's yes to what the agent said since the last confirmed action, with every value in it, by value or by attribute.
- `named_sources="confirm"`: a source-rule violation whose value came from a file, URL, or address the user named asks instead of blocking. The default stays "rule".
- `requires`: calls that must come first.
- In prose, an identifier may be shortened after an underscore ("gift card_7245904").
- Measured: successful tau-bench runs flagged 16 → 10 of 1,183, all ten genuine fabrications; the selection and confirmation checks, named sources, and prerequisites with their costs and reach on tau-bench, tau2-bench, and AgentDojo.
- Docs: 0.3.0 reported all 145 telecom wrong-tool errors as one class. 63 were calls to tools the agent does not have, and 82 were unneeded changes.
- `benchmarks/agent_eval.py`: `selection` and `actions` evaluations, tau-bench tool schemas, `--no-tools`; AgentDojo reports blocked, confirm, and warn separately.

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
