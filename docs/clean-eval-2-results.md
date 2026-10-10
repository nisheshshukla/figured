# Second clean evaluation (figured 0.4.1): results

Protocol: [clean-eval-2-protocol.md](clean-eval-2-protocol.md), pushed before the run (4a4ea64). The scripts were pushed before their first run (9c3d296), and the library was frozen at `v0.4.1`. The scoring harness crashed while timing, after printing every measure, because of a figured bug listed below. It was made to skip that run and rerun; the numbers were identical. Output files are in `benchmarks/results/clean2/`.

## Result: the claim fails

| Measure | Predicted | Toucan, 3,000 runs on 495 real MCP servers |
|---|---|---|
| Good runs flagged, figured / baseline | at most 3% | **41.9%** (673 of 1,605) / 40.2% |
| Middle runs flagged | | 53.2% / 51.4% |
| Poor runs flagged | more than good runs | 46.1% / 43.1% |
| Corruptions caught | at least 95% | 95.3% / 87.2% |
| Substitutions caught | about 0% | 24.4% / 1.8% |
| Calls to tools not in the tool list | | 137 in good runs, 198 in the others |
| Transcripts figured could not read | | 19, all one crash |
| `before_call` p50 / p99 | | 39 µs / 0.99 ms |

## Why: the 40 sampled flags on good runs

| Class | Count | Examples |
|---|---|---|
| Made up, and it matters | 0 | |
| Invented legitimately | 38 | the agent's drawing coordinates and colors (18); a named city's latitude and longitude (8); image sizes (3); its own confidence and temperature settings (3); result limits and a date window for "recent studies" (3); a filename, an argument ID, a workflow node type (3) |
| False flag | 2 | "250ml" not read as 250; an expression computed from a timestamp |

## What it means

**figured's premise, that the values an agent acts on should come from the conversation, holds for transactional agents and not for generative ones.** On tau-bench, tau2-bench, AgentDojo and ToolScale, arguments are IDs, accounts, recipients, amounts and dates, which the user or a lookup supplies. Most of Toucan's tools draw, render, think, convert, and look up weather. There the agent is meant to choose the values, so "not in context" is normal, and a flag carries no information: good runs were flagged about as often as poor runs, and as often as by a substring check.

The scope figured can claim is agents that act on records: orders, accounts, payments, bookings, messages, and their IDs, amounts, and dates. That is now stated in the README.

## Bugs found, not fixed here

- **A tool result with an infinite number** (`Infinity`) crashes indexing (`OverflowError`); 19 transcripts.
- **A number written with its unit** ("250ml", "175g") is not read as a number.
- **Read tools named with a server prefix** (`pubmed-mcp-server-search_pubmed_advanced`) do not match the `search_*` pattern, so their bounds are checked.
- **Substitutions were "caught"** 24% of the time, because the substitute was drawn from tool declarations in the system prompt, which figured does not treat as data. This says more about the procedure than about figured.

## After 0.4.2

0.4.2 fixed the three bugs above and changed nothing about the result: 41.2% of good Toucan runs flagged, against 40.6% for the baseline. The flags are values the agent is meant to choose. The scope statement stands.
