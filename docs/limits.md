# Limits and comparison

## What it does not do

- It cannot catch a correct number attached to the wrong claim. That is what the judge extra is for.
- Tolerance matching over many cells accepts many wrong figures by coincidence: about 70% of random figures on a 12×5 table at the default 1.5%, 99% with pairwise arithmetic on, 10% on the three-row example. `Report.coincidence()` gives the number for your table; read it before trusting a green result, and tighten `rel_tolerance` or the derivations for larger tables.
- Sign is ignored: "-$97,150" traces to a cell of 97,150.
- In answers, numbers written as words ("two million") are not extracted. Agent checks read English number words in what the user says.
- It does not know what the rows mean. If the agent queried the wrong column and described it faithfully, every figure traces.
- For agents, a wrong value that also exists in the context passes: the wrong one of two real order IDs, a flight date put in a birth date field, a recipient copied from an injected email when no source rule covers that argument. Provenance proves a value came from somewhere in the context, not that it was the right one. On the benchmarks above this is most real agent failure.
- A call to a real tool that the task did not need, with correct values, is invisible to it. Calls to tools the agent does not have are blocked when you pass `tools=`; prerequisite rules from tau2-bench's policy caught 2 of 82 unneeded changes.
- Values without digits (names, airport codes, product options) and integers up to 10 are not checked, so a wrong passenger name or a quantity of 9 instead of 1 passes.
- An error that echoes a made-up value back does not vouch for it only when the monitor saw the call that caused it. Feed `before_call` and `tool_result` the same call id, or replay the whole transcript.
- Values the model knows rather than read (a model name, a currency conversion at a rate it remembers, a well-known code) are flagged; list them in `constants` or ignore the argument.
- Amounts in European (`1.234,56`) or Indian (`1,23,456`) grouping, and number words in languages other than English, are not read.
- Agent amounts are strict: a figure the agent computed in a way not listed above, such as a fare difference times the passengers, is flagged. That is usually worth seeing.
- More context means more coincidences: in a session with hundreds of tool results, a made-up amount can match a real one to the cent.
- Source rules flag legitimate runs that take a value from a document the user pointed to; see the AgentDojo numbers.

## How it compares

| | rows as evidence | derived arithmetic | deterministic | names each figure | packaged |
|---|---|---|---|---|---|
| **figured** | yes | sums, means, shares, ranges; differences, ratios and percentages opt-in; reports its own coincidence rate | yes | yes, with the derivation | pip, zero deps |
| llmground | no, a source string | no | yes | yes | pip |
| @demystify/grounding | no, cited facts | no | yes | yes | npm |
| pcn-core (Proof-Carrying Numbers) | claim values you supply | no | yes | yes, needs model-emitted tags | pip |
| NumProof | no: verifies a self-contained claim, or audits a spreadsheet for internal consistency | yes, within the claim or sheet | yes | per claim | hosted API, open client |
| DeepEval / Ragas faithfulness | text context | n/a | no, LLM or NLI | no | pip |

For agent runs:

| | needs a reference trajectory | per-value provenance | runs before the call | deterministic |
|---|---|---|---|---|
| **figured.agents** | no | yes, every argument value with its source | yes | yes |
| agentevals, Ragas ToolCallAccuracy, DeepEval ToolCorrectness | yes | no | no | yes |
| DeepEval ArgumentCorrectness | no | no | no | no, LLM judge |
| DeepEval AgentLoopDetection | no | no | no, scores a finished trace | yes |
| Invariant Guardrails | no | for the data-flow rules you write | yes | yes, rule language |
| CaMeL, FIDES (information-flow control) | no | yes, as labels on every value | yes | yes, but the agent is rebuilt around a planner and a quarantined model |

The Proof-Carrying Numbers policy vocabulary (exact, rounded, scale alias, tolerance, percent, range, year) is the clearest statement of the matching problem, and this library borrows its shape. The difference is the evidence contract: rows in, free text in, no cooperation from the model required.

## Ports

Behavior is pinned by the conformance vectors in `tests/vectors/` (`core.json` for answers, `agents.json` for agent runs). A port in another language is correct when it passes them unchanged. A TypeScript port is the natural next one; open an issue if you want to take it.
