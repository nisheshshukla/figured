"""Refresh tests/vectors/golden.json after an intended behaviour change: python -m tests.regolden"""

from __future__ import annotations

import json
from pathlib import Path

from figured.agents import check_run

from .test_golden import ADOJO_POLICY, DATA, POLICY, SAMPLE, adojo_runs, fingerprint

golden = {}
for r in SAMPLE["runs"]:
    golden[r["id"]] = fingerprint(check_run(r["messages"], POLICY, tools=SAMPLE["tools"][r["domain"]]))
for name, run in adojo_runs():
    golden["agentdojo/" + name] = fingerprint(check_run(run["messages"], ADOJO_POLICY))
(Path(__file__).parent / "vectors" / "golden.json").write_text(json.dumps(golden, indent=1))
print(f"wrote {len(golden)} fingerprints; sample at {DATA}")
