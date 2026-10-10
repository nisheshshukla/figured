# figured.agents on public agent runs

How `figured.agents` 0.3.0 was measured, what it catches, what it misses, and how the measurement was kept honest. Everything here is reproduced by one script:

```bash
python benchmarks/agent_eval.py --download          # tau-bench and tau2-bench; AgentDojo instructions printed
python benchmarks/agent_eval.py taubench --json benchmarks/results/taubench.json
python benchmarks/agent_eval.py tau2 --json benchmarks/results/tau2.json
python benchmarks/agent_eval.py agentdojo --json benchmarks/results/agentdojo.json
python benchmarks/agent_eval.py selection --json benchmarks/results/selection.json
python benchmarks/agent_eval.py actions --json benchmarks/results/tau2_actions.json
```

The JSON files in `benchmarks/results/` are the outputs reported below, for 0.4.0. The 0.3.0 files, including the frozen held-out AgentDojo run, are in the `v0.3.0` tag.

## What is being claimed

One thing: **a value an agent passes to a tool that appears nowhere in the context**, meaning the user's messages, the system prompt, and earlier tool results, nor follows from them by the few kinds of arithmetic listed in the README. Everything below measures that claim, its false-flag rate, and how much of real agent failure it covers.

## Data

| Dataset | Role | Agent models | Runs | Tool calls | Ground truth |
|---|---|---|---|---|---|
| tau-bench retail and airline (sierra-research/tau-bench, `historical_trajectories/`) | development | GPT-4o, Claude 3.5 Sonnet | 1,980 | 14,285 | task reward, required actions |
| tau2-bench telecom (sierra-research/tau2-bench, `data/tau2/results/final/`) | held out | GPT-4.1, Claude 3.7 Sonnet | 912 | 6,162 | task reward, required actions |
| AgentDojo banking, slack, travel, workspace (ethz-spylab/agentdojo, `runs/claude-3-7-sonnet-20250219`) | held out | Claude 3.7 Sonnet | 1,065 | | utility and security per run |

The three use different transcript formats, so the format adapters are exercised too: OpenAI-style JSON-string arguments, inline call markup, tau2's `requestor` field and user-side tools, and AgentDojo's `{"function", "args"}` calls.

## Development and held-out data

The heuristics were written and tuned against tau-bench, and against 64 hand-written probe cases from an independent review. tau2-bench and AgentDojo were not looked at while building. The code was frozen at commit 665ba11, both held-out evaluations were run once, and the numbers below are what came out. Nothing was changed in response to them.

**A disclosure about 0.2.0.** The 0.2.0 results split tau-bench by task number and called the odd tasks held out, but flags on those tasks were reviewed and two fixes made in response, and the README reported that all 10 flags on successful runs were real fabrications. An independent review then measured what 0.2.0 actually caught against ground truth (23 of 1,217 real errors, 1.9%), showed that a plain substring check matched its corruption headline (3,312 against 3,305 of the same 3,339), found holes the heuristics had opened (a digit run inside another ID counted as a source, any multiple of a price up to 9 was accepted, a sign flip passed, errors that echoed a made-up ID vouched for it), and found that unknown transcript formats were reported as clean. 0.3.0 fixes those, changes the claim to "absent from context", and treats all of tau-bench as development data.

## What was measured

1. **Flags on successful runs.** A run that completed its task should rarely be flagged. A flag there is either a false flag or a made-up value that happened not to matter, and each was read in context.
2. **Real errors against ground truth.** In failed runs, every write call (a tool whose name does not start with `get_`, `find_`, `list_`, `search_`, `check_`, `read_`, `calculate`, `think`, or `transfer_`) is compared with the task's required actions. An argument whose value differs from what the task required is a real error. So is a write to a tool the task never required.
3. **Absent corruptions.** On successful runs, three argument values that figured found in context are altered at random, one at a time, the way models get values wrong: two adjacent digits swapped, a digit changed, the digits dropped from an email's local part, a date moved by a day, an amount raised 7%. The run is replayed and the altered value checked.
4. **Substitutions.** The same values are replaced with another real value of the same kind and shape from the same conversation: another order ID, another date, another email. This is the error provenance cannot see, measured so the gap has a number.
5. **A baseline.** Each measure is repeated for a substring check: every argument value that contains a digit or an @, and every number above 10, must appear verbatim, case-insensitively, somewhere in the context. A guardrail that does less work should be compared against this before against nothing.

The measurement policy ignores the `think` scratchpad and treats `calculate` as a pure tool.

## Results

Each cell is figured / baseline.

### tau-bench (development)

Replayed with tau-bench's tool schemas (extracted from its source by `--download`), as the agents had them. `--no-tools` reproduces 0.3.0's setting: 16 successful runs flagged instead of 10, everything else the same.

| File | Runs | Tool calls | Successful runs flagged | Real errors flagged | Absent corruptions caught | Substitutions caught |
|---|---|---|---|---|---|---|
| GPT-4o retail | 460 | 3,274 | 1 of 278 (0.4%) / 14.0% | 4 of 206 / 2 | 824 of 824 / 775 | 0 of 498 / 0 |
| GPT-4o airline | 200 | 1,164 | 4 of 84 (4.8%) / 14.3% | 9 of 301 / 21 | 189 of 197 / 167 | 0 of 61 / 0 |
| Claude 3.5 Sonnet retail | 920 | 7,086 | 3 of 637 (0.5%) / 12.2% | 2 of 213 / 2 | 1,905 of 1,906 / 1,788 | 0 of 1,157 / 0 |
| Claude 3.5 Sonnet airline | 400 | 2,761 | 2 of 184 (1.1%) / 15.8% | 28 of 497 / 81 | 450 of 468 / 401 | 0 of 205 / 2 |
| **All** | 1,980 | 14,285 | 10 of 1,183 (0.8%) / 13.4% | 43 of 1,217 (3.5%) / 106 (8.7%) | 3,368 of 3,395 (99.2%) / 92.2% | 0 of 1,921 / 2 |

### tau2-bench telecom (held out)

| File | Runs | Tool calls | Successful runs flagged | Real errors flagged | Absent corruptions caught | Substitutions caught |
|---|---|---|---|---|---|---|
| GPT-4.1 | 456 | 3,041 | 0 of 156 (0.0%) / 0.0% | 0 of 59 / 0 | 430 of 430 / 430 | 0 of 304 / 0 |
| Claude 3.7 Sonnet | 456 | 3,121 | 2 of 225 (0.9%) / 0.0% | 3 of 92 / 1 | 667 of 668 / 667 | 0 of 524 / 0 |
| **All** | 912 | 6,162 | 2 of 381 (0.5%) / 0.0% | 3 of 151 (2.0%) / 1 (0.7%) | 1,097 of 1,098 (99.9%) / 99.9% | 0 of 828 / 0 |

Telecom identifiers (phone numbers, line and plan IDs) are distinctive, so the baseline does as well as figured on corruptions there. The two flags on successful runs are the same value, `set_network_mode_preference.mode = "4g_5g_preferred"`, an enum the agent knows from the tool's schema. The replay has no schemas; passed as `tools=`, an `enum` argument is skipped.

### Real errors by kind

| Kind of error in failed runs' writes | tau-bench | figured | baseline | tau2-bench | figured | baseline |
|---|---|---|---|---|---|---|
| wrong identifier | 640 | 8 | 6 | 3 | 0 | 0 |
| write to a tool the task never required | 215 | 3 | 16 | 145 | 2 | 0 |
| value not checked (no digits: names, enums, cabin classes; counts up to 10) | 158 | 0 | 0 | 2 | 0 | 0 |
| wrong amount | 131 | 30 | 81 | 1 | 1 | 1 |
| wrong date | 37 | 2 | 3 | | | |
| wrong address line | 36 | 0 | 0 | | | |

Of the 844 wrong identifier, amount, date, and address values in tau-bench that figured could check, 95% appear in the context: real values, wrongly chosen. The baseline catches more wrong amounts because it flags every amount that is not copied verbatim; it also flags 13% of successful runs.

### Flags on successful tau-bench runs, read in context

| Runs | What the agent sent | Verdict |
|---|---|---|
| 1 | `gift_card_0000000`, a placeholder | made up |
| 2 | `credit_card_7334`, built from "the card ending in 7334" (the real ID is `credit_card_9074831`) | made up |
| 2 | `credit_card_5634230`, the right digits with the wrong prefix (the real ID is `gift_card_5634230`) | made up |
| 1 | `#W0028236`, an order number the user gave as 28236, padded with zeros | made up |
| 2 | zip codes `98101` and `95112`, assumed from the city the user named | made up |
| 2 | user IDs `olivia_gonzalez_902` and `_631`, guessed from the user's name | made up |
| 4 | `#W9502126`, the `#W` prefix added to digits the user typed, a format the tool description states | false flag in 0.3.0; 0.4.0 reads the format |
| 2 | `card_7245904`, a gift card ID split by a space ("gift card_7245904") in a hand-off summary | false flag in 0.3.0; prose may now shorten an ID after an underscore |

Every made-up value reached a real tool. Those runs succeeded only because the bad call errored and the agent recovered.

### AgentDojo (held out): prompt injection

Source rules on sensitive arguments, written before the run and listed in `ADOJO_RULES` in the benchmark: payment and message recipients, passwords, user details, Slack invitations, and posted URLs must come from the user or the system prompt; email and calendar recipients may also come from a contacts lookup. A run counts as flagged if any write call has an unsourced value or a rule violation.

| Runs with a flagged write | Runs | With source rules | Without |
|---|---|---|---|
| Injection succeeded | 47 | 36 (76.6%) | 7 (14.9%) |
| Injection attempted, did not succeed | 902 | 151 (16.7%) | 68 (7.5%) |
| No attack, user's task completed | 116 | 26 (22.4%) | 13 (11.2%) |

Without rules, provenance alone barely separates attacks from benign runs, because an injected value arrives in a tool result and counts as context. With rules, three in four successful injections are flagged, and so are one in five benign runs. Of the 26 benign runs flagged:

- **15 have legitimate data flow that a rule forbids.** Examples: paying the IBAN in a bill the user pointed to, inviting the person named on a webpage, emailing an address from a calendar event. A rule that says recipients come from the user cannot tell these apart from an attack. This is the basic tradeoff of information-flow control.
- **11 have only values absent from context.** Examples: transaction dates the user told the agent to invent ("fill them in with reasonable values"), and a new rent amount the agent computed from a landlord's notice.

The 11 successful injections missed with rules:

- **6** make the agent visit a URL through `get_webpage`. The benchmark counts that as a read, not a write, and no rule covered it.
- **3** delete a file whose ID is real and in context. No rule covered deletion.
- **1** sets the password to `new_password`. Under the frozen code, a string without digits is not checked even when a source rule covers the argument; see the next section.
- **1** achieves its goal in the agent's text, with no tool call.

### Fixed after the held-out run

The password miss is a design gap, not a tuning question: a source rule exists to constrain where a value comes from, so it should apply to any string, digits or not. 0.3.0 ships the fix: under a source rule, a string argument with no digits is checked as an identifier or phrase. The tables above are from the frozen code (665ba11). Rerunning AgentDojo after the fix gives:

| Runs with a flagged write, with source rules | Frozen code | After the fix |
|---|---|---|
| Injection succeeded | 36 of 47 (76.6%) | 37 of 47 (78.7%) |
| Injection attempted, did not succeed | 151 of 902 (16.7%) | 169 of 902 (18.7%) |
| No attack, user's task completed | 26 of 116 (22.4%) | 29 of 116 (25.0%) |

The fix only changes runs with source rules, so tau-bench and tau2-bench are unaffected. It is in `benchmarks/results/agentdojo_after_fix.json`.

### What the 145 telecom "wrong tool" errors are

Read call by call, 63 of the 145 are Claude 3.7 Sonnet calling tools that belong to the simulated user's phone (`can_send_mms`, `run_speed_test`, `reboot_device`), which the agent does not have; the environment answered "Tool not found". The other 82 are real agent tools (`send_payment_request` 41, `resume_line` 34, `enable_roaming` 4, `refuel_data` 3) making changes the task did not call for. 0.3.0's docs reported all 145 as one class.

## 0.4.0: tool schemas, confirmations, and checks on the call

Built from follow-up research into the gaps 0.3.0 measured, comparing CaMeL, FIDES and Prudentia, Progent, PolicyGuard, ToolGuard, AWS AgentCore's temporal policies, and LLM tool-call judges. Its conclusion: fix the one false-flag class that has a deterministic fix, and for the questions provenance cannot answer, route rather than guess. Everything here is development data: tau-bench was tuned on before, and tau2-bench and AgentDojo have now been seen. Nothing below is held out.

**Tool schemas.** IDs in the format a schema states, a `pattern` or examples in the description ("such as '#W0000000'"), are found when the digits are in context as a whole token and the digit count matches. Flags on successful tau-bench runs: 16 → 12; with the prose fix, 10, all genuine. Corruptions caught and real errors flagged are unchanged.

**Unknown tools.** With the tool list passed, figured blocks a call to a tool the agent does not have. On tau2-bench it flags all 146 such calls by Claude 3.7 Sonnet, 43 of them in runs that still succeeded.

**Selections and confirmations, tau-bench** (`python benchmarks/agent_eval.py selection`, with every write tool listed). "Asks" are `confirm` verdicts. "Reaches" counts real wrong values in required writes of failed runs that fall on a call it asked about, and in brackets those the finding names.

| File | `ambiguous_before`: asks on writes, successful / failed runs | reaches, of wrong values | `confirm_before`: asks on writes, successful / failed | reaches |
|---|---|---|---|---|
| GPT-4o retail | 9.3% / 7.0% | 27 (7) of 160 | 22.2% / 28.2% | 59 (10) of 160 |
| GPT-4o airline | 25.0% / 30.4% | 84 (31) of 250 | 53.6% / 72.2% | 225 (43) of 250 |
| Claude 3.5 Sonnet retail | 2.5% / 3.8% | 9 (4) of 184 | 9.4% / 12.3% | 23 (1) of 184 |
| Claude 3.5 Sonnet airline | 11.5% / 13.1% | 105 (42) of 408 | 40.8% / 40.7% | 252 (48) of 408 |

On tau2-bench telecom, whose policy does not require a confirmation before every change, `confirm_before` asked on 46% to 93% of writes in successful runs, and `ambiguous_before` on 0% to 1.2% (failed runs 0% to 7.9%).

Two rounds of fixes on tau-bench got here, and both are recorded so the numbers can be read as tuned. Matching IDs literally against the confirmed summary asked on 70% to 85% of writes: agents confirm with names ("the headphones", "the Mastercard ending in 2478"), not IDs. Binding through the attributes of each ID's record, a multi-turn confirmation window, values the user typed, and more ways of saying yes brought it to the table above.

**Named sources, AgentDojo** (`named_sources="confirm"`, the source rules unchanged):

| Runs with a flagged write | Blocked | Confirm | Blocked, rules only |
|---|---|---|---|
| Injection succeeded (47) | 18 (38.3%) | 18 (38.3%) | 36 (76.6%) |
| No attack, task completed (116) | 9 (7.8%) | 9 (7.8%) | 18 (15.5%) |

Confirmations per completed benign task: 0.10. The same move that halves blocked benign runs sends 18 of 47 successful injections to a person, because AgentDojo places its injection in the very file the user names; in the attack runs the bill's real IBAN is replaced by the attacker's, so a "two candidates in one file" check would not fire either. This is why it is opt-in.

**Prerequisites, tau2-bench telecom.** Three rules taken from the policy text before looking at errors ("always check that the bill is overdue before sending a payment request", lift a suspension only after the bills are paid, check roaming before enabling it). They flagged 0% to 0.4% of writes in successful runs and 2 of the 82 unneeded changes. The agents did the lookups; the changes were still not needed.

## Hand-written probes

An independent review wrote 64 cases: 41 correct behaviors that must pass, 23 wrong behaviors that must be flagged, and 18 format and robustness cases. These were used during development, so they are not a held-out measure.

| | 0.2.0 | 0.3.0 |
|---|---|---|
| correct behavior flagged | 32 of 41 | 5 of 41 |
| wrong behavior missed | 21 of 23 | 7 of 23 |
| format or robustness failure (silent pass or crash) | 5 of 18 | 0 of 18 |

Still flagged though correct:

- a currency conversion at a rate in a tool result
- a model name chosen by the agent
- European `1.234,56` amounts
- Indian `1,23,456` amounts
- the Spanish number word "doscientos"

Still missed though wrong:

- a fabricated passenger name
- a fabricated airport code
- a digit-free SKU
- a quantity of 9 instead of 1
- a flight date put in a birth date field
- an injected recipient with no source rule
- a tool error echoing a made-up ID when the monitor never saw the call that caused it

## Speed

`python benchmarks/agent_speed.py` replays every tau-bench call, with tool schemas. `before_call` takes 26 µs at the median and 0.19 ms at p99 (0.3.0: 23 µs and 0.16 ms). With `--synthetic`, sessions of 400 KB of tool output take 0.23 ms median and 0.43 ms p99, and 2 MB takes 0.49 ms and 1.1 ms. The selection checks parse a tool result's records only when a side-effecting call asks, so they cost nothing on other calls. Adding a tool result takes about 0.1 ms at the median and a system prompt about 1 ms, both off the critical path. 0.3.0 is about 25% slower per call than the unreleased performance work it builds on, in exchange for the separator-insensitive matching, the arithmetic, and the laundering checks.

## Reading these numbers

- The false-flag rate is low enough to run on all traffic: 0.8% of successful runs on development data (all genuine fabrications) and 0.5% on tau2-bench, against 13% for the substring baseline on tau-bench.
- Values altered the way models alter them are caught 99% of the time.
- Most real agent failure is a real value chosen wrongly, or a call no one needed, and figured's provenance check catches 2 to 4% of it. The 0.4.0 checks do not change that: they mark which calls deserve a person or a verifier, with a modest lift over asking at random, and block calls to tools the agent does not have.
- It is one layer: cheap enough for every call, precise on the failure it targets, and honest about the rest.

## 0.4.2

Built from two independent adversarial reviews of 0.4.1 ([one](review-0.4.1.md) with code access, [one](review-0.4.2-fresh.md) written without seeing the code, tests, or docs) and a version-by-version comparison of 0.2.0 through 0.4.1 on every benchmark. The reviews' probe sets became regression suites; everything below is development data.

| Probe set, correct / total | 0.2.0 | 0.3.0 | 0.4.1 | 0.4.2 | substring baseline |
|---|---|---|---|---|---|
| Heuristic holes and false flags (37) | 17 | 17 | 15 | **25** | 20 |
| Injected value under a source rule (21) | 14 | 15 | 14 | **19** | 17 |
| False flags on correct behaviour (30) | 17 | 15 | 17 | **25** | 13 |
| Fresh reviewer: correct calls allowed (30) | | | | **30** | 10 |
| Fresh reviewer: bad calls flagged (30) | | | | **29** | 22 |

Benchmarks, same harnesses as the clean evaluations (`benchmarks/results/v0.4.2/`), figured / baseline:

| | Good runs flagged | Corruptions caught | Real errors flagged |
|---|---|---|---|
| tau-bench, 4 files | 0.8% (10 of 1,183, all genuine) | 99.4% / 92.2% | 47 of 1,217 |
| tau2 airline, 4 models | 2.1% / 23.7% | 92.6% / 78.9% | 29 of 604 |
| tau2 retail, 4 models | 0.7% / 12.1% | 99.9% / 93.7% | 11 of 631 |
| tau2 telecom, 2 models | 21.4% / 21.4% (invented birth dates) | 99.9% / 99.9% | 3 of 444 |
| ToolScale, 2 models | 1.3% / 11.3% | 99.6% / 99.8% | |
| Toucan, 3 models, good band | 41.2% / 40.6% | 96.1% / 87.2% | |

AgentDojo with source rules, five models (3,000 runs, Llama 3.3 now readable): 85.8% of successful injections flagged, 23.3% of benign completed runs. Claude 3.7 alone: 78.7% and 25.0%.

Numeric check: default derivations are now cells, column sums and means, shares of a column total, and row sums; pairwise arithmetic is opt-in. Coincidence (random figures called grounded) on a 12×5 table: 99% with pairwise, 70% at the new default, 38% under STRICT; on the README's 3×3 table 10%. The number is now part of every report.

Speed: `before_call` 33 µs median, 0.22 ms p99 on tau-bench (0.4.1: 27 µs, 0.19 ms); `trace` 93 µs on 2×3, 9.3 ms on 2,000×10. 0.4.3 brings `before_call` to 27 µs and 0.19 ms with identical results (a fingerprint of every report on all 1,980 tau-bench runs is unchanged), and to 45 µs at 400 KB of context and 76 µs at 2 MB, where 0.4.2 took 246 µs and 616 µs.
