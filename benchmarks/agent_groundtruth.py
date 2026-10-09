"""Check argument flags on failed tau-bench runs against the task's ground-truth actions.

For each flagged tool argument, find the same tool and argument path in the actions the task
required. If the ground truth has a different value there, the flag pointed at a real error.
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
import agent_runs as ar

from figured.agents import check_run


def leaves(obj: Any, path: str = "") -> list[tuple[str, Any]]:
    if isinstance(obj, dict):
        return [x for k, v in obj.items() for x in leaves(v, f"{path}.{k}" if path else k)]
    if isinstance(obj, list):
        return [x for v in obj for x in leaves(v, f"{path}[]")]
    return [(path, obj)]


def main() -> None:
    verdicts: collections.Counter[str] = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
    for name in ar.FILES:
        for r in ar.load(name):
            if r["reward"] >= 1.0:
                continue
            expected: dict[str, set[str]] = collections.defaultdict(set)
            for act in r["info"]["task"]["actions"]:
                for path, v in leaves(act.get("kwargs") or act.get("arguments") or {}):
                    expected[f"{act['name']}.{path}"].add(str(v))
            rep = check_run(r["traj"], ar.POLICY)
            for c in rep.checks:
                if c.where == "text" or c.status == "sourced":
                    continue
                want = expected.get(c.where)
                if not want:
                    v = "tool or argument not in the ground-truth actions"
                elif str(c.value) in want:
                    v = "same value as the ground truth"
                else:
                    v = "differs from the ground truth"
                verdicts[v] += 1
                examples[v].append(
                    f"{name} {r['task_id']}/{r.get('trial', 0)} {c.where} = {c.value!r} "
                    f"(expected {sorted(want or [])[:2]})"
                )
    total = sum(verdicts.values())
    print(f"argument flags on failed runs: {total}")
    for v, n in verdicts.most_common():
        print(f"  {n:4}  {v}")
        for line in examples[v][:6]:
            print("        ", line)


if __name__ == "__main__":
    main()
