# Clean evaluation of figured 0.4.0: protocol

Written and committed before any of the data below was run through figured. The library under test is frozen at tag `v0.4.0` (commit `3c05736`); nothing in `src/` changes between this protocol and the results. The evaluation code is `benchmarks/clean_eval.py`; it may be fixed only to read the data correctly (a loader or format bug), and any such fix is reported. Every number specified here is reported, whichever way it comes out.

## Data none of figured's development has seen

| Set | What is new | Source | Labels |
|---|---|---|---|
| **A. ToolScale** | Domains and tools: movie booking, banking, e-commerce, medicine, basketball. Models: DeepSeek V4 Pro, Qwen 3.6 Plus | `zake7749/deepseek-v4-pro-agent-tool-calling-trajectory` and `zake7749/Qwen-3.6-plus-agent-tool-calling-trajectory` on Hugging Face (from NVIDIA's ToolScale). The longest row of each conversation is the full run: 284 and 582 runs | Qwen: `reward` (1 = success). DeepSeek: `score`, action match against a reference; 1.0 counts as success, below 1.0 as failure. No per-call ground truth |
| **B. tau2-bench airline and retail** | Runs and models: Claude 3.7 Sonnet, GPT-4.1, o4-mini, GPT-4.1-mini, 4 trials each. The domains descend from tau-bench's, which figured was developed on, so this set is new runs in familiar domains, not new domains | `sierra-research/tau2-bench`, `data/tau2/results/final/` | task reward, required actions |
| **B'. tau2-bench telecom** | Models not measured before: o4-mini, GPT-4.1-mini. The domain was used in 0.3.0 and 0.4.0 with GPT-4.1 and Claude 3.7 | as above | as above |
| **C. AgentDojo** | Models: GPT-4o, GPT-4o-mini, Claude 3.5 Sonnet (2024-10-22), Gemini 2.0 Flash, Llama 3.3 70B. The benchmark was used with Claude 3.7 Sonnet | `ethz-spylab/agentdojo`, `runs/`; attack types `important_instructions` and `none` only | utility, security |

Tool schemas: ToolScale ships them per run. For tau2, they are built from the domain's `tools.py` (functions marked `@is_tool`, argument descriptions from the docstrings; a tool marked `ToolType.WRITE` is a write). Telecom uses the agent tool list from 0.4.0's benchmark. AgentDojo runs carry no schemas and are replayed without them.

## Policies, fixed in advance

- Provenance: `AgentPolicy.build(ignore=["think.*"], pure_tools=["calculate"])` with the tool schemas, as in 0.4.0's benchmark.
- Selection checks (sets A and B): the same, plus `ambiguous_before` and `confirm_before` on the write tools. Write tools are tau2's `WRITE` tools; for ToolScale, tools whose names do not start with `get_`, `find_`, `list_`, `search_`, `check_`, `read_`, `calculate`, `think`, or `transfer_` (0.4.0's rule).
- AgentDojo: `ADOJO_RULES` from 0.4.0's benchmark unchanged, default `named_sources="rule"`, and no rules.
- Baseline: 0.4.0's substring baseline.
- Corruptions and substitutions: 0.4.0's procedures, 3 per successful run, seed 7.

## Measures

For A, B and B', per file and pooled, figured and baseline:

1. Successful runs with an argument flag.
2. Failed runs with an argument flag.
3. Absent corruptions caught.
4. Substitutions caught.
5. Calls to tools not in the tool list (where the list is exact: A, B, B').
6. For B and B': real errors in failed runs' writes against required actions, flagged.
7. Selection checks: asks on writes in successful and failed runs; for B, real wrong values on a call asked about.
8. Every flag on a successful run in A and B is read in context and classified as genuine (a value with no source that reached a tool) or false, with the reason. If there are more than 40 per set, a random 40 (seed 7) are read.

For C, per model and pooled: runs with a flagged write, by strongest verdict, for injection succeeded, injection attempted but not succeeded, and benign runs that completed the task.

Speed: `before_call` p50 and p99 over all calls in A.

## What 0.4.0's development data predicts

Written down so the result can be compared with it:

- Successful runs flagged: about 1% (tau-bench 0.8%, tau2 telecom 0.5%).
- Absent corruptions caught: about 99%.
- Substitutions caught: about 0%.
- Real errors flagged: 2–4%.
- AgentDojo with rules: about 80% of successful injections and about 25% of benign runs flagged.
- `ambiguous_before`: asks on 2–25% of writes in successful runs.

A number far from these on new data means the development numbers do not generalize, and that will be the headline.
