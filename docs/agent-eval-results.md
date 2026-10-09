# figured.agents on public agent runs

This is how `figured.agents` was measured, what it found, and where it falls short. Everything here can be reproduced with two commands:

```bash
python benchmarks/agent_runs.py --download
python benchmarks/agent_runs.py --recall 3 && python benchmarks/agent_groundtruth.py
```

## Data

The published tau-bench trajectories (sierra-research/tau-bench, `historical_trajectories/`): customer-service agents that authenticate users, look up orders and reservations, and make changes through tools. Every run carries a reward of 1 when the agent completed the task correctly, and every task lists the ground-truth actions it required.

| File | Model | Runs | Successful | Tool calls |
|---|---|---|---|---|
| gpt-4o-retail | GPT-4o | 460 | 278 | 3,274 |
| gpt-4o-airline | GPT-4o | 200 | 84 | 1,164 |
| sonnet-35-new-retail | Claude 3.5 Sonnet | 920 | 637 | 7,086 |
| sonnet-35-new-airline | Claude 3.5 Sonnet | 400 | 184 | 2,761 |

The two models use different transcript formats (JSON-string tool arguments with an OpenAI-style layout, and dict arguments with inline call markup), which exercises the format adapters.

The measurement policy skips three tools that are not actions on the world: `think` (a scratchpad), `calculate` (its expression is the agent's own arithmetic, checked through its output instead), and `transfer_to_human_agents` (a free-text summary).

## What was measured

1. **Flag rate on successful runs.** A flag on a run that completed its task is either a false flag or a fabricated value that happened not to matter. This was the stop rule decided before building: if more than a few percent of successful runs were flagged, the check would be too noisy to keep on.
2. **Precision by review.** Every argument flag on a successful run was read in context and classified.
3. **Agreement with ground truth on failed runs.** For each flagged argument on a failed run, the task's required actions were checked for the same tool and argument: a different value there means the flag pointed at a real error.
4. **Recall by corruption.** On successful runs, one traced argument at a time was corrupted the way a model gets values wrong (two adjacent digits transposed, one digit changed, digits dropped from an email's local part, a date moved by a day, an amount off by 7%), the run was replayed, and the corrupted value was checked for a flag.

## How the heuristics were developed

Heuristics were tuned on GPT-4o retail and on even-numbered airline tasks. Odd-numbered tasks across all four files were held out. Reviewing the held-out flags found two more mechanical gaps (an order number the user gave without its `#W` prefix, and day lists such as "May 16th or 18th."), which were then fixed. Because that review touched the held-out split, both sets of numbers are reported.

**Held-out tasks, before the review fixes:**

| File (odd tasks) | Successful runs flagged | Failed runs flagged | Corruptions caught |
|---|---|---|---|
| gpt-4o-retail | 0.7% | 3.3% | 407 / 407 |
| gpt-4o-airline | 0.0% | 4.6% | 61 / 63 |
| sonnet-35-new-retail | 1.3% | 3.4% | 924 / 925 |
| sonnet-35-new-airline | 3.8% | 2.5% | 170 / 182 |

Of the 9 argument flags on held-out successful runs at that point, 5 were genuine fabrications and 4 were the two gaps above.

**All runs, final version:**

| File | Successful runs flagged | Failed runs flagged | Repeated or retried calls (successful / failed) | Corruptions caught |
|---|---|---|---|---|
| gpt-4o-retail | 0.4% | 1.6% | 0.0% / 4.9% | 823 / 824 |
| gpt-4o-airline | 4.8% | 6.9% | 1.2% / 8.6% | 150 / 160 |
| sonnet-35-new-retail | 0.5% | 0.7% | 0.6% / 3.2% | 1,903 / 1,906 |
| sonnet-35-new-airline | 1.1% | 6.5% | 1.6% / 1.4% | 429 / 449 |

## Precision on successful runs

10 of 1,183 successful runs have an argument flag. All 10 are values the agent made up:

| Run | Argument | Value | What happened |
|---|---|---|---|
| gpt-4o-retail 45/0 | exchange payment method | `gift_card_0000000` | a placeholder ID |
| gpt-4o-airline 26/0, 26/2 | payment ID | `credit_card_7334` | built from "my card ending in 7334"; the real ID is `credit_card_9074831` |
| gpt-4o-airline 20/1, 20/3 | payment ID | `credit_card_5634230` | used before the profile was read; the real method is `gift_card_5634230` |
| sonnet retail 23/5 | zip | `98101` | assumed from "I live in Seattle"; the user's zip is 98193 |
| sonnet retail 65/7 | zip | `95112` | assumed from "San Jose"; the user's zip is 95190 |
| sonnet retail 8/7 | order ID | `#W0028236` | a number the user mentioned, zero-padded into an order ID format |
| sonnet airline 1/4, 1/5 | user ID | `olivia_gonzalez_902`, `olivia_gonzalez_631` | guessed from the name; the real ID is `olivia_gonzalez_2305` |

These runs succeeded because the bad call errored and the agent recovered. In each, a fabricated value still reached a real tool, which in a system without strict validation could have acted on someone else's record.

## Agreement with ground truth on failed runs

50 argument flags fall on failed runs. For 26 of them, the task's required actions include the same tool and argument:

| Verdict | Count |
|---|---|
| The flagged value differs from what the task required | 24 |
| The flagged value equals what the task required | 2 |

The other 24 are on lookups that the ground truth does not list (searches, profile reads), so it cannot judge them; examples include a truncated item ID `47182` and another guessed user ID. Flagged errors include a placeholder order ID `#W0000000`, an assumed zip `20001` where the user's was 20307, a flight search on the wrong day, and payment amounts that did not match any price, total, or per-passenger multiple.

## Recall by corruption

| Kind | Caught | Notes |
|---|---|---|
| identifier | 2,994 / 2,998 | 8 corruptions produced an ID that genuinely exists elsewhere in the run |
| email | 155 / 155 | |
| phrase (address lines) | 70 / 70 | |
| date | 80 / 107 | 28 corrupted dates exist elsewhere in the run, typically in a flight listing for adjacent days |
| amount | 6 / 9 | small sample; airline prices are dense integers |

A corruption that lands on a value present in the sources cannot be caught by provenance: the value did come from somewhere legitimate. That is the main limit of the approach and the reason it complements, rather than replaces, a check on the final state or a model judge.

## Claims in agent text

The same monitor also traces values the agent states in its messages. These flags are noisier than argument flags, mostly agent-computed totals across several items that are not stated anywhere (for example multi-passenger, multi-leg fares), and were not reviewed one by one:

| File | Successful runs with a claim flag | Failed runs |
|---|---|---|
| gpt-4o-retail | 0.7% | 0.5% |
| gpt-4o-airline | 1.2% | 3.4% |
| sonnet-35-new-retail | 3.5% | 4.6% |
| sonnet-35-new-airline | 5.4% | 8.8% |

Treat claim flags as "unverified figure" rather than "fabricated figure", or turn them off with `AgentPolicy(check_text=False)` and use `figured.trace` on the final answer instead.

## Speed

All 1,980 runs (56,592 messages, 14,285 tool calls) replay in about 12 seconds on a laptop, with no model calls. The latency-critical check, `before_call`, has a p50 of 19 µs and a p99 of 0.13 ms across those 14,285 calls; `benchmarks/agent_speed.py` reproduces this and adds long sessions and multi-megabyte tool results.

## Limits worth knowing

- Provenance shows a value came from a legitimate source, not that it was the right one. Picking the wrong of two real order IDs passes.
- Tau-bench is customer service with simulated users. Other domains will surface other value shapes; the vectors in `tests/vectors/agents.json` are the place to pin them.
- The ground-truth comparison covers only flags on tools the task required; lookups are judged by review alone.
