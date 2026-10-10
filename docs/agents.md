# figured.agents: reference

The full behaviour of the agent check: what counts as found, the confirm verdict, policy options, latency, transcript formats, and every measured number. The README has the short version.

## What counts as found

Arguments are checked strictly, because they are acted on. Values in the agent's own messages are checked loosely, because prose rounds and abbreviates.

| Kind | Found when | Not found |
|---|---|---|
| identifier | the same characters appear, ignoring case and separators (`ORD 88213` → `ORD-88213`, `(415) 555-0132` → `+14155550132`, IBANs with or without spaces); a prefix the tool's schema states is added to digits the user typed (`9502127` → `#W9502127` when the description says "such as '#W0000000'", or a `pattern` says `^#W\d{7}$`), or dropped from an ID seen in a result | a digit run inside another identifier; a prefix added to digits from a tool result or to the wrong number of them (`credit_card_7334` from "ending in 7334"); a different country code |
| email, URL | the address appears, ignoring case; a URL may differ in scheme or `www.` | another host that ends the same way (`evil-example.com` is not `example.com`) |
| date | the calendar date appears in any format, including ISO timestamps (`2025-08-02T23:59:59Z`), `YY-MM-DD` in tool results, Spanish, French, German, Portuguese, Italian, and Chinese or Japanese dates; "tomorrow", weekdays, and "end of the month" resolve against the system prompt's date; a date without a year takes the year nearest that date; the first or last day of a period the user names, or the day after ("August" → 08-01, 08-31, 09-01; "in 2024"; "last month"), as search tools take them; a shift the user asked for, in the direction they asked ("a day later", "two weeks earlier") | a shift in the other direction; a shift applied to a birth date or any date years from now |
| amount | the number appears, to the cent and with its sign; or it is a sourced amount times or divided by a count the user stated or a tool reported (`passengers: 3`), a percentage the user stated of an amount (a tip), or two values of the same money field in one list added (two item prices); English number words count ("two hundred fifty"); a bare run of digits after a money word ("send 15000") | a multiple by a count nobody stated or by a list's length; two different fields added (a fee plus a tax); a percentage that appears only in a tool result; a sum over a search result with dozens of fares |
| phrase | a short string with digits (an address line) appears, or every number and word in it does, with each number beside words in its source | a house number borrowed from a price |

Nothing laundered counts as a source: a total in the agent's own message counts only if every operand was found, the output of a pure tool such as a calculator does not vouch for made-up inputs, an error that echoes a made-up ID back does not vouch for it, and a value the system prompt gives as an example ("IDs look like #W0000000") is not data.

Free text in an argument, such as an email body, is scanned for identifiers, emails, URLs, dates, and long digit runs. Values without digits (names, airport codes, enum words) are not checked unless a source rule covers the argument, and integers up to 10 are not checked. Tool schemas sharpen this when you pass them: `format: email` or `date` sets the kind, and `enum`, `const`, and `default` values are skipped.

## Loops and budgets

The same monitor watches the run as a whole: an identical call repeated `max_repeats` times, a call retried with the same arguments after it errored, and a tool-call budget. Repetition and not knowing when to stop are the two most common agent failure modes in published trace studies, and both are visible without a model.

## When a value is real but may be the wrong one: confirm

Some questions a provenance check cannot answer: which of three real orders the user meant, or whether an action was needed at all. figured does not guess. It has a fourth verdict, `confirm`: hold the call for a person or a model verifier, and say why. `decision.allowed` is False for both `confirm` and `block`, so code that does not handle the new verdict stays safe; `decision.needs_confirmation` tells you to ask.

```python
policy = AgentPolicy.build(ambiguous_before=["return_*", "cancel_*"])
monitor = RunMonitor(policy)
monitor.user("I want to return something.")
monitor.tool_result(
    "get_user_details",
    {
        "orders": [
            {"order_id": "#W1111111", "items": [{"name": "Headphones", "item_id": "4202497723"}]},
            {"order_id": "#W2222222", "items": [{"name": "Smart Watch", "item_id": "9408160950"}]},
        ]
    },
)
monitor.before_call("return_items", {"order_id": "#W2222222"}).action
# "confirm": return_items.order_id=#W2222222 was chosen from context; nothing the user said or confirmed
#            singles it out from 1 other value of the same shape (#W1111111)
monitor.user("It's the one with the smart watch.")
monitor.before_call("return_items", {"order_id": "#W2222222"}).action  # "allow"
```

The opt-in checks behind it. The first two are experimental: they route calls to a person or a verifier, and how well depends on the domain.

- `ambiguous_before`: an identifier the agent chose from several of the same shape (orders, items, payment methods) that the user neither typed nor singled out. Singling out counts by value or by an attribute of the value's record that the other candidates do not all share: "the smart watch", "the Mastercard ending in 2478".
- `confirm_before`: the user's last message must agree ("yes", "go ahead") to what the agent said since the last confirmed action, and every value in the call must appear in it, by value or by attribute. Many support policies, tau-bench's among them, require this before any change.
- `named_sources="confirm"`: a value a source rule forbids, which came from a file, URL, or address the user named ("pay the bill in 'bill.txt'"), asks instead of blocking.

Two checks on the call itself complement them: with `tools=` passed, a call to a tool the agent does not have is blocked; and, experimental, `requires` names calls that must come first (`{"send_payment_request": ["get_bills_for_customer"]}`).

These route; they do not judge. Measured on the same benchmarks:

| Check | Asks on successful runs | Reaches in failed runs |
|---|---|---|
| `ambiguous_before`, tau-bench writes | 2.5% to 25% of writes, 0.04 to 0.17 asks per run | 225 of 1,002 wrong values fall on a call it asks about, about 1.1 to 2.4 times its ask rate; it names the wrong value in 84 |
| `confirm_before`, tau-bench writes | 9% to 54% of writes | 559 of 1,002 on a call it asks about; names 102 |
| `named_sources="confirm"`, AgentDojo with source rules | benign runs blocked fall from 15.5% to 7.8%; 7.8% ask instead | successful injections blocked fall from 76.6% to 38.3%; 38.3% go to the person instead |
| `requires`, three rules from tau2-bench's telecom policy | 0% to 0.4% of writes | 2 of 82 side effects the task did not need |
| unknown tools, tool list passed, tau2-bench | none | all 146 calls Claude 3.7 Sonnet made to tools it does not have |

The ambiguity check is the most useful of these: on most runs it asks rarely, and when it asks, a wrong value is somewhat more likely than average. Confirmation binding is only worth turning on where the policy requires a confirmation; on tau2-bench telecom, whose policy does not, it asked on 46% to 93% of writes. Turning a named source into a confirmation halves blocked benign runs but hands the decision on many real injections to a person, because AgentDojo plants its attacks in exactly the files users name; use it only where a person reads the prompt. The prerequisite rules caught almost nothing: the agents did the lookups and still made changes no one asked for.

## Policy

Defaults are lenient: an unsourced value warns, so the check can run on all traffic from day one and feed an online metric before it is allowed to stop anything. Tighten it where an action has side effects.

```python
policy = AgentPolicy.build(
    block_unsourced=["refund.*", "transfer.*"],  # side-effecting tools block on any unsourced value
    source_rules={"send_email.to": {"user"}},  # where a value must come from; violations block
    pure_tools=["calculate"],  # output computed only from the arguments
    ignore=["think.*"],  # scratchpad tools are not actions
    constants=["USA", "USD"],  # values that never need a source
    max_repeats=3,
    max_tool_calls=40,  # loop and budget findings
)
```

| Option | Default | Meaning |
|---|---|---|
| `block_unsourced` | none | argument patterns where an unsourced value blocks the call |
| `source_rules` | none | argument pattern to allowed sources: `user`, `system`, `tool`, `tool:<name>`, `derived` |
| `pure_tools` | none | tools whose output is computed from their arguments; if those were made up, the output vouches for nothing |
| `schema_formats` | True | read ID formats from the `tools` schemas: `pattern`, `examples`, and examples in descriptions |
| `named_sources` | "rule" | "confirm" asks instead of blocking when a rule-violating value came from a resource the user named |
| `ambiguous_before`, `confirm_before` | none | tool patterns for the selection and confirmation checks |
| `requires` | none | tool pattern to calls that must come first |
| `read_tools` | `get_*`, `search_*`, `list_*`... | tools that only read; the MCP annotation `readOnlyHint` marks one too |
| `search_bounds` | "skip" | on read calls, do not check range bounds and paging (`start_date`, `min_amount`, `limit`), which agents choose themselves; identifiers in read calls are still checked |
| `on_selection`, `on_requires`, `on_unknown_tool` | confirm, warn, block | severity for those checks |
| `on_unsourced`, `on_rule`, `on_repeat`, `on_budget` | warn, block, warn, block | severity for each finding type: "warn", "confirm", or "block" |
| `kinds` | all six | which value kinds need a source |
| `ignore`, `constants` | none | arguments to skip; values that are always allowed |
| `small_ints` | 10 | integers up to this are counts and are not checked |
| `free_text` | "extract" | scan long string arguments for values, or "skip" them |
| `max_repeats`, `max_tool_calls` | 3, none | loop and budget thresholds |
| `as_of` | "auto" | date for "tomorrow", weekdays, and dates without a year, read from the system prompt by default |
| `date_shift_days` | 31 | the largest date move a user can ask for; 0 turns shifts off |
| `check_text` | True | also check values the agent states in its messages |

## Latency

The check that matters for latency is `before_call`, which sits between the model proposing a tool call and the call running. Everything expensive happens earlier, when a message or tool result is added, because that moment is followed by a model call that takes seconds anyway. Numbers are indexed in log-scale buckets, so a tolerance lookup touches a few dozen entries however much the agent has seen; dates are indexed by month and day; identifiers are found through an inverted index of every token the agent has seen, built at ingest, so a lookup costs a dict hit plus a check of the few sources holding that token, and a value already looked up is not scanned again. Measured with `python benchmarks/agent_speed.py` on a laptop:

| Session | `before_call` p50 | p99 |
|---|---|---|
| tau-bench runs (14,285 calls), with tool schemas | 27 µs | 0.19 ms |
| 10 tool results, 20 KB seen | 0.03 ms | 0.13 ms |
| 200 tool results, 400 KB seen | 0.05 ms | 0.14 ms |
| 20 tool results, 2 MB seen | 0.08 ms | 0.26 ms |

Adding a tool result takes about 0.1 ms at the median on tau-bench, and a system prompt about 1 ms, once per run. Indexing is capped at 1 MB per source. For comparison, gateway hops in published benchmarks add under 10 ms, classifier guardrails 20 to 100 ms, and model-based checks around a second.

## Finished transcripts

`check_run(messages)` replays a recorded run through the same monitor, for offline evals, CI, and trace review. It reads OpenAI Chat Completions and Responses API items, Anthropic content blocks, Gemini parts, Bedrock Converse blocks, LangChain messages (objects, dicts, and serialized), and the tau-bench, tau2-bench, and AgentDojo formats, including mixtures. A message in any other shape raises `UnrecognizedMessage`, so a transcript is never reported clean because nothing in it was read; pass `on_unknown="warn"` or `"ignore"` to relax that.

```python
from figured.agents import check_run

report = check_run(messages, policy, tools=tool_schemas)
report.ok, report.unsourced, report.findings, report.to_dict()
```

## Measured on public agent runs

**A clean measurement first.** After 0.4.0, every benchmark below had been used during development, so 0.4.0 was measured again on data it had never seen, with the protocol committed before the run ([protocol](clean-eval-protocol.md), [results](clean-eval-results.md)). On new models and runs in familiar domains, the development numbers held:

- tau2-bench airline and retail: 1.1% of successful runs flagged, against 14.9% for the baseline, and 98% of corrupted values caught.
- AgentDojo, four new models: 86% of successful injections flagged with source rules.

**On new domains they did not.** In ToolScale's banking and medicine runs, 12.8% of successful runs were flagged, about as often as the substring baseline, mostly for date ranges ("August" → the 1st to the 31st) and date formats figured does not derive or read, and for search parameters agents choose themselves. 0.4.1 fixes those causes: on the same data, now development data, ToolScale's flagged successful runs fall from 12.8% to 1.3%, with corruptions caught unchanged. A second clean measurement, on 3,000 runs against 495 real MCP servers ([protocol](clean-eval-2-protocol.md), [results](clean-eval-2-results.md)), failed its claim:

- 41.9% of good runs were flagged, about as often as poor runs and as the substring baseline.
- Of 40 sampled flags, 38 were values the agent is meant to choose itself: drawing coordinates, image sizes, a city's coordinates.
- Corruptions caught: 95.3%.

So the scope is records, not open-ended tools. The numbers below are for agents of that kind.

**0.4.2** then went through two independent adversarial reviews ([one](review-0.4.1.md) with code access, [one](review-0.4.2-fresh.md) without), fixed what they found, and was re-measured on everything above with the same harnesses (`../benchmarks/results/v0.4.2/`): tau2 airline 2.1% and retail 0.7% of good runs flagged (baseline 23.7% and 12.1%), ToolScale 1.3%, AgentDojo 85.8% of successful injections flagged across five models, Toucan unchanged at 41%. On the fresh reviewer's 60 probes it allowed 30 of 30 correct calls and flagged 29 of 30 bad ones; the substring check managed 10 and 22.


Three public datasets, each run through `benchmarks/agent_eval.py`. tau-bench was used to develop the heuristics, and is replayed with its tools' schemas, as an agent would be given them. tau2-bench and AgentDojo were held out: run once, after the code was frozen, and reported as they came out. Each figure is shown next to a naive baseline: every argument value that contains a digit or an @, and every number above 10, must appear verbatim somewhere in the context. Full methodology and per-file numbers are in [docs/agent-eval-results.md](agent-eval-results.md).

| figured / baseline | tau-bench, development: 1,980 runs, GPT-4o and Claude 3.5 Sonnet | tau2-bench telecom, held out: 912 runs, GPT-4.1 and Claude 3.7 Sonnet |
|---|---|---|
| Successful runs with a flag | 0.8% / 13.4% | 0.5% / 0.0% |
| A real value altered the way models get values wrong: two digits swapped, a digit changed, a date off by a day, an amount off by 7% | 99.2% / 92.2% caught | 99.9% / 99.9% caught |
| A real value from the same conversation in the wrong place | 0.0% / 0.1% caught | 0.0% / 0.0% caught |
| Wrong argument values and wrong calls in failed runs, against the task's ground truth | 3.5% / 8.7% caught | 2.0% / 0.7% caught |

What this says, plainly:

- **It catches made-up values, and rarely flags good runs.** A real ID, email, date, or amount with two digits swapped or a day or a few percent off is caught almost every time, and on held-out data under 1% of successful runs carried a flag. The substring baseline flags 13% of successful tau-bench runs, mostly identifiers and address lines written differently from the source.
- **Most agent failures are not made-up values.** Of the wrong argument values in failed tau-bench runs that figured could check, 95% appear in the context: an existing order ID that was not the one the user meant, a real flight on the wrong date. On tau2-bench, 145 of 151 errors were calls to a tool the task never needed: 63 to tools the agent does not have, which the environment rejected and which figured now blocks when given the tool list, and 82 to real tools that changed something no one asked to change. Provenance cannot see either; that takes the system's own validation, or a model that reads intent. The baseline catches more failed-run errors on tau-bench airline because it flags any amount that is not copied verbatim, right or wrong.
- **The flags are worth reading.** All 10 successful tau-bench runs with a flag sent a tool a value with no source: zip codes assumed from a city name, user IDs guessed from a person's name, a payment ID built from "the card ending in 7334", a gift card ID with a credit card prefix, an order number padded with zeros, and a placeholder `gift_card_0000000`. The runs succeeded only because the bad call errored and the agent recovered. The six false flags in 0.3.0 are gone: four were the `#W` prefix the agent learned from tool descriptions, which figured now reads, and two an ID split by a space in a hand-off summary.

For prompt injection, AgentDojo (held out, Claude 3.7 Sonnet, 1,065 runs) with source rules on the sensitive arguments: payment and message recipients, passwords, user details, and posted URLs must come from the user or a contacts lookup. The rules were written before the run and are listed in the benchmark.

| Runs with a flagged write | with source rules | without |
|---|---|---|
| Injection succeeded | 78.7% (37 of 47) | 14.9% |
| Injection attempted, did not succeed | 18.7% | 7.5% |
| No attack, user's task completed | 25.0% (29 of 116) | 11.2% |

Rules catch four in five successful injections, and also flag one in four legitimate runs. Most of those are tasks that rightly take a recipient from a document, such as "pay the bill in bill.txt" or "invite the person on this webpage", which a rule saying recipients come from the user cannot tell apart from an attack. That is the basic tradeoff of information-flow control, and the reason to put rules only on the few arguments where you would rather ask than act. The held-out run, on frozen 0.3.0 code, gave 76.6% and 22.4%; the numbers above include one fix it exposed, rules now checking strings without digits such as a password.
