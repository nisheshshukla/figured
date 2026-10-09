"""Measure figured.agents on public tau-bench transcripts.

Download the trajectories first (about 50 MB, not committed):

    python benchmarks/agent_runs.py --download
    python benchmarks/agent_runs.py                 # all four files
    python benchmarks/agent_runs.py --files gpt-4o-retail --samples 25

Each run carries a task reward (1 = the agent completed the task correctly). A provenance flag on
a successful run is either a false flag or an unsourced value that happened not to matter; flags
on failed runs are candidate catches. Samples are printed for manual review.
"""

from __future__ import annotations

import argparse
import collections
import copy
import datetime as dt
import json
import random
import time
import urllib.request
from pathlib import Path
from typing import Any

from figured.agents import AgentPolicy, check_run

DATA = Path(__file__).parent / "data"
FILES = ["gpt-4o-retail", "gpt-4o-airline", "sonnet-35-new-retail", "sonnet-35-new-airline"]
BASE = "https://raw.githubusercontent.com/sierra-research/tau-bench/main/historical_trajectories/"
POLICY = AgentPolicy.build(ignore=["think.*", "calculate.*", "transfer_to_human_agents.*"])


def download() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        target = DATA / f"{f}.json"
        if not target.exists():
            urllib.request.urlretrieve(BASE + f + ".json", target)
            print("downloaded", target.name)


SPLIT = "all"


def load(name: str) -> list[dict[str, Any]]:
    runs = json.loads((DATA / f"{name}.json").read_text())
    if SPLIT == "all":
        return list(runs)
    parity = 0 if SPLIT == "even" else 1
    return [r for r in runs if r["task_id"] % 2 == parity]


def measure(name: str, samples: int, seed: int) -> dict[str, Any]:
    runs = load(name)
    stats: dict[str, Any] = collections.defaultdict(int)
    by_where: collections.Counter[str] = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
    t0 = time.perf_counter()
    for r in runs:
        ok = r["reward"] >= 1.0
        rep = check_run(r["traj"], POLICY)
        bad = [c for c in rep.checks if c.status != "sourced"]
        actions = [c for c in bad if c.where != "text"]
        claims = [c for c in bad if c.where == "text"]
        key = "ok" if ok else "fail"
        stats[f"runs_{key}"] += 1
        stats[f"action_flagged_{key}"] += bool(actions)
        stats[f"claim_flagged_{key}"] += bool(claims)
        stats[f"repeat_{key}"] += any(f.type in ("repeated_call", "retried_error") for f in rep.findings)
        stats["calls"] += rep.tool_calls
        stats["values"] += len(rep.checks)
        stats["action_values"] += sum(1 for c in rep.checks if c.where != "text")
        stats["flagged_action_values"] += len(actions)
        for c in bad:
            label = c.where if c.where != "text" else f"text:{c.kind}"
            by_where[label] += 1
            examples[key].append(f"{r['task_id']}/{r.get('trial', 0)} step {c.step} {label} = {c.value!r}")
    stats["seconds"] = round(time.perf_counter() - t0, 2)
    rng = random.Random(seed)
    out = {"file": name, **stats, "top_flagged": by_where.most_common(15)}
    for key in ("ok", "fail"):
        ex = examples.get(key, [])
        out[f"sample_{key}"] = rng.sample(ex, min(samples, len(ex)))
    return out


def mutate(kind: str, value: object, rng: random.Random) -> object | None:
    """A plausible wrong value: a transposed or changed digit, a guessed email, a shifted date, a 7% error."""
    if kind == "number":
        v = float(value)  # type: ignore[arg-type]
        return round(v * 1.07, 2) if not float(v).is_integer() else int(v * 1.07) + 1
    s = str(value)
    if kind == "date":
        try:
            d = dt.date.fromisoformat(s[:10])
        except ValueError:
            return None
        return (d + dt.timedelta(days=1)).isoformat() + s[10:]
    if kind == "email":
        local, _, domain = s.partition("@")
        stripped = "".join(c for c in local if not c.isdigit()).rstrip("._")
        return (stripped if stripped != local else local + "1") + "@" + domain
    digits = [i for i, c in enumerate(s) if c.isdigit()]
    pairs = [i for i in digits if i + 1 < len(s) and s[i + 1].isdigit() and s[i] != s[i + 1]]
    if pairs:
        i = rng.choice(pairs)
        return s[:i] + s[i + 1] + s[i] + s[i + 2 :]
    if digits:
        i = rng.choice(digits)
        return s[:i] + str((int(s[i]) + 3) % 10) + s[i + 1 :]
    return None


def _replace(obj: object, old: object, new: object) -> object:
    if isinstance(obj, dict):
        return {k: _replace(v, old, new) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace(v, old, new) for v in obj]
    return new if obj == old else obj


def recall(name: str, per_run: int, seed: int) -> dict[str, list[int]]:
    """For successful runs, corrupt one traced tool argument at a time and check it gets flagged."""
    rng = random.Random(seed)
    runs = [r for r in load(name) if r["reward"] >= 1.0]
    tally: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0])
    for r in runs:
        base = check_run(r["traj"], POLICY)
        traced = [c for c in base.checks if c.where != "text" and c.status == "sourced"]
        for c in rng.sample(traced, min(per_run, len(traced))):
            bad = mutate(c.kind, c.value, rng)
            if bad is None or bad == c.value:
                continue
            traj = copy.deepcopy(r["traj"])
            tool, _, _ = c.where.partition(".")
            calls = [
                tc
                for m in traj
                for tc in (m.get("tool_calls") or [])
                if (tc.get("function") or tc).get("name") == tool
            ]
            hit = False
            for tc in calls:
                fn = tc.get("function", tc)
                raw = fn.get("arguments")
                args = json.loads(raw) if isinstance(raw, str) else raw
                if json.dumps(args).count(json.dumps(c.value)) and not hit:
                    new_args = _replace(args, c.value, bad)
                    fn["arguments"] = json.dumps(new_args) if isinstance(raw, str) else new_args
                    hit = True
            if not hit:
                continue
            rep = check_run(traj, POLICY)
            flagged = any(x.where == c.where and x.value == bad and x.status != "sourced" for x in rep.checks)
            exists = any(x.where == c.where and x.value == bad and x.status == "sourced" for x in rep.checks)
            t = tally[c.kind]
            t[0] += 1
            t[1] += flagged
            t[2] += exists
    return dict(tally)


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "n/a"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--files", nargs="*", default=FILES)
    ap.add_argument("--samples", type=int, default=0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--recall", type=int, default=0, help="corruptions per successful run")
    ap.add_argument("--split", choices=["all", "even", "odd"], default="all", help="task_id parity")
    args = ap.parse_args()
    global SPLIT
    SPLIT = args.split
    if args.download:
        download()
        return
    results = []
    for name in args.files:
        s = measure(name, args.samples, args.seed)
        results.append(s)
        ok, fail = s["runs_ok"], s["runs_fail"]
        print(f"\n== {name}: {ok} successful, {fail} failed runs, {s['calls']} tool calls, {s['seconds']} s")
        rows = (
            ("action flag rate", "action_flagged"),
            ("claim flag rate", "claim_flagged"),
            ("repeat/retry", "repeat"),
        )
        for label, key in rows:
            good, bad = pct(s[key + "_ok"], ok), pct(s[key + "_fail"], fail)
            print(f"   {label:<18} successful {good:>6}   failed {bad:>6}")
        print(f"   argument values flagged: {s['flagged_action_values']} of {s['action_values']}")
        print("   top flagged:", ", ".join(f"{w} ({n})" for w, n in s["top_flagged"]))
        for key in ("ok", "fail"):
            for line in s.get(f"sample_{key}", []):
                print(f"     [{key}] {line}")
        if args.recall:
            rec = recall(name, args.recall, args.seed)
            s["recall"] = rec
            tot = [sum(v[i] for v in rec.values()) for i in range(3)]
            print(
                f"   corrupted arguments caught: {tot[1]} of {tot[0]} ({pct(tot[1], tot[0])}); "
                f"{tot[2]} corruptions produced a value that really exists in the sources"
            )
            for kind, (n, caught, exists) in sorted(rec.items()):
                print(f"     {kind:<11} {caught:>4}/{n:<4} caught {pct(caught, n):>6}   collisions {exists}")
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
