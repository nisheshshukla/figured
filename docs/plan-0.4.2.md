# 0.4.2 plan

Status: done; results in `docs/agent-eval-results.md` (section 0.4.2) and `benchmarks/results/v0.4.2/`.

From the independent review of 0.4.1 (adversarial probes against 0.2.0, 0.3.0, 0.4.1 and a 15-line substring baseline) and the version-by-version benchmark comparison. Goal: beat the substring baseline on all three probe sets while keeping the benchmark numbers (tau2 ≤1% of good runs flagged, AgentDojo ≥86% of successful injections flagged), and make the numeric check honest about coincidence. Local commits only; no push or release without the owner's approval.

## 1. Crashes
- `extract._NUMBER` reads `550e8400` (UUID hex) and `1e999` as scientific notation → infinity → `OverflowError` in `store.add`. Cap the exponent, skip non-finite values, and guard `math.isfinite` where numbers are indexed.
- `tool_result` with non-string dict keys (`json.dumps` TypeError), `user(None)`, rows with ints ≥ 1e308 in `trace`.

## 2. Security holes (source rules)
- A ruled argument is never free text: check the whole string (S14, S16).
- A tool result never vouches for its own arguments: every argument value of a call is an echo for its result (S19 laundering).
- No composed phrases for ruled arguments (S20, H30).
- Remove `readOnlyHint` trust (H36).
- Schema formats wrap only digits the user typed (H10).
- Country code: strip a `+CC` only when the source number had none or the same (H15).

## 3. Heuristic holes
- Remove list-length counts (H18); add counts from `quantity`-like fields beside a price (H24).
- Remove the digits→amount fallback (H07); read a bare 5+ digit run in user text as an amount only after a money word (H08).
- Percent factors only from user and system text (H20, S17).
- Same-source sums only within one JSON object (H21, H23).
- Pure tools: any unsourced operand taints, small ints included (H34).
- Enum: skip only when the value is in the enum (H03).
- Period bounds only on bound-named arguments (`start_date`, `until`...) (H27).
- "the day after tomorrow" no longer arms a +1 shift (H28).

## 4. False flags on correct behaviour
- Dates `YYYY/MM/DD`, `D.M.YYYY` (F08, F09); a day-month without a year also accepts the reference year (F10).
- Read tools: match the last segment of server-prefixed names and camelCase (F22, F23).

## 5. Numeric check `trace`
- Default `derivations` = cell, column_sum, row_sum; pairwise arithmetic opt-in (`derivations="all"`), and documented as unusable above ~20 cells.
- Default `unmatched_percent="flag"`.
- `Report.coincidence()`: the fraction of random values in the table's range that would be called grounded, so a green result says how much it means; printed by the CLI.
- Conformance vectors that rely on pairwise derivations pass `derivations="all"` explicitly.

## 6. Measure
- The reviewer's three probe sets and `trace` probes, per version, plus the substring baseline.
- tau-bench, tau2 (airline, retail, telecom), ToolScale, AgentDojo, Toucan: the same harnesses as before.
- A new probe set written by a reviewer who has not seen the code.
