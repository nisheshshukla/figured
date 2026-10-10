# Testing

`pytest` runs everything; `pytest -m "not perf"` skips the latency guards. CI runs the full suite on Python 3.10 to 3.13 with lint, formatting, strict typing, and a 95% coverage gate, then builds the wheel and installs it into a clean environment in a separate smoke job.

| Kind | Where | What it guards |
|---|---|---|
| Unit | `test_extract`, `test_evidence`, `test_trace`, `test_edges`, `test_agent_*`, `test_cli` | each function and each documented behaviour |
| Conformance vectors | `test_vectors` with `vectors/core.json`, `vectors/agents.json` | the pinned behaviour a port in another language must reproduce |
| Adversarial regression | `test_probes` with `vectors/probes.json` (148 agent probes), `vectors/trace_probes.json` (40) | every probe two independent reviewers wrote; the ones figured gets right are pinned, the ones it gets wrong are `xfail(strict)` so a fix must update the record, and the published tallies are asserted |
| Golden drift | `test_golden` with `data/taubench_sample.json` (16 real runs), `data/agentdojo_sample/` (11), `vectors/golden.json` | the sha256 of every report on real transcripts; any change in output names the run. Refresh with `python -m tests.regolden` after an intended change |
| Metric gates | `test_golden` | at sample scale: no successful run flagged, ≥90% of corrupted identifiers caught, ≥5 of 7 successful injections flagged with rules, ≤1 of 4 benign runs flagged, median call under 1 ms |
| Property-based and fuzz | `test_properties`, `test_fuzz` (Hypothesis) | arithmetic invariants of `trace`; the monitor never raises on any JSON-like input and is deterministic; every input class a review found crashing |
| Isolation | `test_isolation` | monitors share no state, threads match sequential runs, caches stay bounded |
| Performance | `test_perf` (`perf` marker) | a lookup must not grow with session length (the token index); loose absolute budgets |
| Smoke | `test_smoke`, CI `smoke` job | the CLI as a subprocess, the examples, the public API names, version in one place, the README's examples, and a clean-environment install of the built wheel |
| Formats | `test_agent_formats` | every transcript adapter and the refusal to read unknown shapes |

The benchmarks in `benchmarks/` are not tests: they run on 1.7 GB of public agent runs (gitignored) and produce the numbers in the docs. The clean-evaluation protocols in `docs/clean-eval-*.md` are the process for measuring on data figured has never seen.
