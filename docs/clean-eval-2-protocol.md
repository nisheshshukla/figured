# Second clean evaluation (figured 0.4.1): protocol

Written and pushed before figured sees this data. The library is frozen at tag `v0.4.1`. The loader and the scoring are fixed here; the loader may be fixed only to read the data correctly, and any such fix is reported. Every number below is reported, whichever way it comes out.

## Question

Does 0.4.1's false-flag rate hold on domains and tools figured has never seen? 0.4.1's fixes were built on ToolScale, tau-bench, tau2-bench and AgentDojo, so none of those count.

**Claim under test:** figured 0.4.1 flags at most 3% of good runs in unseen domains, while still catching at least 95% of corrupted values.

## Data

[Toucan-1.5M](https://huggingface.co/datasets/Agent-Ark/Toucan-1.5M) (Apache-2.0): agent runs against 495 real MCP servers and 2,000+ tools, with real tool responses.

- **Sample:** shard `train-00007` of each generator model's config (Kimi-K2, Qwen3, OSS). From each, 1,000 rows drawn at random with seed 7 from rows that contain at least one function call. The shard was chosen before any row was read; it holds only the `single-turn-original` subset (one user request, then tool calls).
- **Tools:** the row's `available_tools`, passed as `tools=`.
- **Label:** `response_quality_assessment.completeness.score`, an LLM judge's rating of whether the request was fulfilled.
  - 4 or 5: good runs.
  - 1 or 2: poor runs.
  - 3: reported as its own band.
- **Loader normalization:** `function_call` is stored as a Python-literal string and is parsed with `ast.literal_eval`. Rows whose call cannot be parsed are counted and reported.

## Policy

`AgentPolicy.build(ignore=["think.*"], pure_tools=["calculate"])`, defaults otherwise (including `search_bounds="skip"`), with `tools=`.

## Measures

Per model and pooled, figured and the substring baseline:

1. Runs with an argument flag, per band (good, middle, poor).
2. Absent corruptions caught: 0.4.0's procedure, 3 per good run, seed 7.
3. Substitutions caught.
4. Calls to tools not in `available_tools`.
5. Transcripts figured cannot read.
6. `before_call` p50 and p99.
7. A random 40 flags on good runs (seed 7), read in context and classified:
   - **made up, and it matters:** a value with no source sent to a tool
   - **invented legitimately:** a parameter the agent chose, a value from common knowledge such as a city's coordinates, or one the user asked it to choose
   - **false flag:** the value follows from the context in a way figured does not model

## Predictions

- Good runs flagged: at most 3%. A result above 3% fails the claim.
- Corruptions caught: at least 95%.
- Substitutions caught: about 0%.
- Poor runs flagged more often than good runs.
