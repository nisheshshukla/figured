"""The second clean evaluation, specified in docs/clean-eval-2-protocol.md.

python benchmarks/clean_eval2_sample.py    # once, with pyarrow
python benchmarks/clean_eval2.py --json benchmarks/results/clean2/toucan.json
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
import agent_eval as ae
import clean_eval as ce

DIR = ae.DATA / "clean2" / "toucan"
MODELS = ["Kimi-K2", "Qwen3", "OSS"]


def message(m: dict[str, Any], problems: list[str]) -> dict[str, Any]:
    m = {k: v for k, v in m.items() if v not in (None, "")} | {"content": m.get("content") or ""}
    fc = m.get("function_call")
    if isinstance(fc, str):
        try:
            m["function_call"] = ast.literal_eval(fc)
        except (ValueError, SyntaxError):
            problems.append(fc[:80])
            m.pop("function_call")
    return m


def load(name: str) -> tuple[list[tuple[str, ae.Run]], int]:
    rows = json.loads((DIR / f"{name}.sample.json").read_text())
    out: list[tuple[str, ae.Run]] = []
    problems: list[str] = []
    for r in rows:
        score = json.loads(r["response_quality_assessment"])["completeness"]["score"]
        band = "good" if score >= 4 else "poor" if score <= 2 else "middle"
        msgs = [message(m, problems) for m in json.loads(r["messages"])]
        tools = json.loads(r["available_tools"])
        out.append((band, ae.Run(f"{name}:{r['uuid']}", band == "good", msgs, [], tools)))
    return out, len(problems)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    results: list[dict[str, Any]] = []
    everything: list[ae.Run] = []
    flags: list[str] = []
    for name in MODELS:
        runs, unparsed = load(name)
        print(f"\n######## {name}: {len(runs)} runs, unparsable calls {unparsed}")
        for band in ("good", "middle", "poor"):
            subset = [r for b, r in runs if b == band]
            res = ce.evaluate(subset, f"Toucan {name} {band}", None, ground_truth=False, selection=False)
            res["unparsable_calls"] = unparsed
            results.append(res)
            if band == "good":
                flags += res.pop("all_flags")
            else:
                res.pop("all_flags")
        everything += [r for _, r in runs]
    review = random.Random(7).sample(sorted(set(flags)), min(40, len(set(flags))))
    results.append({"review": review, "speed": ce.speed(everything)})
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
