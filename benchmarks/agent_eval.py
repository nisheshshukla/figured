"""Measure figured.agents on public agent transcripts, and compare it with a plain substring baseline.

    python benchmarks/agent_eval.py --download
    python benchmarks/agent_eval.py taubench          # tau-bench retail and airline (development data)
    python benchmarks/agent_eval.py tau2              # tau2-bench telecom (held out)
    python benchmarks/agent_eval.py agentdojo         # AgentDojo prompt injection (held out)

For tau-style data, four measures:

1. Flag rate on successful and failed runs: how often a run has any argument value not in context.
2. Real errors: on failed runs, argument values in write calls that differ from the task's
   ground-truth action for the same tool and argument, plus write calls to tools the task never
   required. Recall is the share of those the check flags.
3. Absent corruptions: one traced argument at a time is changed the way models mistype (two digits
   swapped, a digit changed, digits dropped from an email, a date moved a day, an amount off by 7%).
4. Substitutions: one traced argument is replaced by a different real value of the same kind that
   already appears in context (the wrong order, the wrong date). Provenance cannot catch these by
   design; the number is reported so nobody reads (3) as general recall.

The baseline marks a value as sourced when its lowercased text, or a number's integer form, occurs
as a substring of an earlier user, system, or tool message; it checks the same argument leaves.
"""

from __future__ import annotations

import argparse
import collections
import copy
import datetime as dt
import glob
import json
import random
import re
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from figured.agents import AgentPolicy, RunMonitor, events
from figured.agents.values import classify

DATA = Path(__file__).parent / "data"
TAU_FILES = ["gpt-4o-retail", "gpt-4o-airline", "sonnet-35-new-retail", "sonnet-35-new-airline"]
TAU_URL = "https://raw.githubusercontent.com/sierra-research/tau-bench/main/historical_trajectories/"
TAU2_FILES = [
    "gpt-4.1-2025-04-14_telecom_default_gpt-4.1-2025-04-14_4trials",
    "claude-3-7-sonnet-20250219_telecom_default_gpt-4.1-2025-04-14_4trials",
]
TAU2_URL = "https://raw.githubusercontent.com/sierra-research/tau2-bench/main/data/tau2/results/final/"
POLICY = AgentPolicy.build(ignore=["think.*"], pure_tools=["calculate"])
READ_PREFIX = ("get_", "find_", "list_", "search_", "check_", "calculate", "think", "transfer_", "read_")
ADOJO_RULES = {
    "send_money.recipient": {"user", "system"},
    "schedule_transaction.recipient": {"user", "system"},
    "update_scheduled_transaction.recipient": {"user", "system"},
    "update_password.password": {"user", "system"},
    "update_user_info.*": {"user", "system"},
    "send_direct_message.recipient": {"user", "system"},
    "invite_user_to_slack.*": {"user", "system"},
    "add_user_to_channel.user": {"user", "system"},
    "remove_user_from_slack.user": {"user", "system"},
    "post_webpage.url": {"user", "system"},
    "send_email.recipients[]": {
        "user",
        "system",
        "tool:search_contacts_by_name",
        "tool:search_contacts_by_email",
    },
    "share_file.email": {"user", "system", "tool:search_contacts_by_name", "tool:search_contacts_by_email"},
    "add_calendar_event_participants.participants[]": {"user", "system", "tool:search_contacts_by_name"},
    "create_calendar_event.participants[]": {"user", "system", "tool:search_contacts_by_name"},
}


@dataclass
class Run:
    id: str
    ok: bool
    messages: list[dict[str, Any]]
    expected: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


def download() -> None:
    (DATA / "tau2").mkdir(parents=True, exist_ok=True)
    for f in TAU_FILES:
        if not (DATA / f"{f}.json").exists():
            urllib.request.urlretrieve(TAU_URL + f + ".json", DATA / f"{f}.json")
    for f in TAU2_FILES:
        if not (DATA / "tau2" / f"{f}.json").exists():
            urllib.request.urlretrieve(TAU2_URL + f + ".json", DATA / "tau2" / f"{f}.json")
    print("tau-bench and tau2 downloaded. For AgentDojo, sparse-clone github.com/ethz-spylab/agentdojo")
    print("and copy runs/claude-3-7-sonnet-20250219 to benchmarks/data/agentdojo.")


def load_tau(name: str) -> list[Run]:
    out = []
    for r in json.loads((DATA / f"{name}.json").read_text()):
        acts = [(a["name"], a.get("kwargs") or {}) for a in r["info"]["task"]["actions"]]
        out.append(Run(f"{name}:{r['task_id']}/{r.get('trial', 0)}", r["reward"] >= 1.0, r["traj"], acts))
    return out


def load_tau2(name: str) -> list[Run]:
    d = json.loads((DATA / "tau2" / f"{name}.json").read_text())
    tasks = {t["id"]: t for t in d["tasks"]}
    out = []
    for s in d["simulations"]:
        crit = tasks[s["task_id"]]["evaluation_criteria"] or {}
        acts = [
            (a["name"], a.get("arguments") or {})
            for a in crit.get("actions") or []
            if a.get("requestor", "assistant") == "assistant"
        ]
        system = (
            d["info"].get("environment_info", {}).get("policy") if isinstance(d.get("info"), dict) else None
        )
        msgs = ([{"role": "system", "content": system}] if system else []) + s["messages"]
        out.append(
            Run(f"{name}:{s['task_id']}/{s.get('trial', 0)}", s["reward_info"]["reward"] >= 1.0, msgs, acts)
        )
    return out


def is_write(name: str) -> bool:
    return not name.startswith(READ_PREFIX)


def leaves(obj: Any, path: str = "") -> list[tuple[str, Any]]:
    if isinstance(obj, dict):
        return [x for k, v in obj.items() for x in leaves(v, f"{path}.{k}" if path else str(k))]
    if isinstance(obj, list):
        return [x for v in obj for x in leaves(v, f"{path}[]")]
    return [(path, obj)]


def replay(
    messages: list[dict[str, Any]], policy: AgentPolicy = POLICY, override: tuple[int, str, Any] | None = None
) -> Iterator[tuple[int, dict[str, Any], Any]]:
    """Feed the run to a monitor; yield (call index, call, decision). `override` replaces one argument
    leaf (call index, path, new value) before that call is checked."""
    m = RunMonitor(policy)
    ci = 0
    for kind, ev in events(messages):
        if kind == "system":
            m.system(ev["text"])
        elif kind == "user":
            m.user(ev["text"])
        elif kind == "assistant":
            m.assistant(ev["text"])
        elif kind == "result":
            m.tool_result(str(ev["name"]), ev["output"], ev.get("id"))
        elif kind == "call":
            args = ev["args"]
            if override and override[0] == ci:
                args = set_leaf(copy.deepcopy(args), override[1], override[2])
            yield ci, {**ev, "args": args}, m.before_call(str(ev["name"]), args, ev.get("id"))
            ci += 1


def set_leaf(obj: Any, path: str, value: Any) -> Any:
    parts = re.findall(r"[^.\[\]]+|\[\]", path)

    def go(x: Any, i: int) -> Any:
        if i == len(parts):
            return value
        p = parts[i]
        if p == "[]" and isinstance(x, list) and x:
            x[0] = go(x[0], i + 1)
        elif isinstance(x, dict) and p in x:
            x[p] = go(x[p], i + 1)
        return x

    return go(obj, 0)


def naive_sourced(v: Any, sources: list[str]) -> bool | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        if float(v).is_integer() and abs(v) <= 10:
            return None
        forms = {str(v).lower()} | ({str(int(v))} if float(v).is_integer() else set())
        return any(f in s for f in forms for s in sources)
    if (
        not isinstance(v, str)
        or not (any(c.isdigit() for c in v) or "@" in v)
        or len(v) > 80
        or len(v.split()) > 6
    ):
        return None
    return any(v.lower() in s for s in sources)


def naive_calls(
    messages: list[dict[str, Any]], override: tuple[int, str, Any] | None = None
) -> Iterator[tuple[int, dict[str, Any], dict[str, bool]]]:
    sources: list[str] = []
    ci = 0
    for kind, ev in events(messages):
        if kind in ("system", "user"):
            sources.append(ev["text"].lower())
        elif kind == "result":
            sources.append(str(ev["output"]).lower())
        elif kind == "call":
            args = ev["args"]
            if override and override[0] == ci:
                args = set_leaf(copy.deepcopy(args), override[1], override[2])
            status = {}
            if not str(ev["name"]).startswith("think") and isinstance(args, dict):
                for p, v in leaves(args):
                    s = naive_sourced(v, sources)
                    if s is not None:
                        status[p] = s
            yield ci, {**ev, "args": args}, status
            ci += 1


def mutate(kind: str, value: Any, rng: random.Random) -> Any:
    if kind == "number":
        v = float(value)
        return round(v * 1.07, 2) if not v.is_integer() else int(v * 1.07) + 1
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


def shape(v: str) -> str:
    return re.sub(r"[a-z]+", "a", re.sub(r"\d", "9", v.lower()))


def substitute(kind: str, value: Any, context: str, rng: random.Random) -> Any:
    """Another real value of the same kind and shape already in context, or None."""
    if kind == "number":
        nums = {float(x) for x in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", context)}
        cands = [n for n in nums if n != float(value) and n > 10]
        if not cands:
            return None
        pick = rng.choice(sorted(cands))
        return int(pick) if isinstance(value, int) and pick.is_integer() else pick
    s = str(value)
    if kind == "date":
        cands = sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", context)) - {s[:10]})
        return rng.choice(cands) if cands else None
    if kind == "email":
        cands = sorted(set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", context)) - {s})
        return rng.choice(cands) if cands else None
    want = shape(s)
    tokens = set(re.findall(r"#?[A-Za-z0-9_\-]*\d[A-Za-z0-9_\-]*", context))
    cands = sorted(t for t in tokens if t != s and shape(t) == want)
    return rng.choice(cands) if cands else None


def context_before(messages: list[dict[str, Any]], call_index: int) -> str:
    parts: list[str] = []
    ci = 0
    for kind, ev in events(messages):
        if kind == "call":
            if ci == call_index:
                break
            ci += 1
        elif kind in ("system", "user"):
            parts.append(ev["text"])
        elif kind == "result":
            parts.append(str(ev["output"]))
    return "\n".join(parts)


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:5.1f}%" if b else "  n/a"


def evaluate_tau(runs: list[Run], label: str, samples: int, seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    res: dict[str, Any] = collections.defaultdict(int)
    wrong: collections.Counter[str] = collections.Counter()
    wrong_flag: collections.Counter[str] = collections.Counter()
    wrong_base: collections.Counter[str] = collections.Counter()
    corr: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0])
    subs: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0])
    examples: dict[str, list[str]] = collections.defaultdict(list)
    t0 = time.perf_counter()
    for run in runs:
        calls = list(replay(run.messages))
        base = {ci: st for ci, _, st in naive_calls(run.messages)}
        flagged = any(c.status != "sourced" for _, _, d in calls for c in d.checks)
        base_flagged = any(not s for st in base.values() for s in st.values())
        key = "ok" if run.ok else "fail"
        res[f"runs_{key}"] += 1
        res[f"flagged_{key}"] += flagged
        res[f"base_flagged_{key}"] += base_flagged
        res["calls"] += len(calls)
        if run.ok and flagged and len(examples["flag_ok"]) < 12:
            c = next(c for _, _, d in calls for c in d.checks if c.status != "sourced")
            examples["flag_ok"].append(f"{run.id} {c.where} = {c.value!r}")
        if not run.ok:
            gt: dict[str, set[str]] = collections.defaultdict(set)
            gt_tools = {n for n, _ in run.expected}
            for n, kw in run.expected:
                for p, v in leaves(kw):
                    gt[f"{n}.{p}"].add(json.dumps(v))
            for ci, call, d in calls:
                name = str(call["name"])
                if not is_write(name):
                    continue
                status = {c.where: c.status for c in d.checks}
                bstat = base.get(ci, {})
                if name not in gt_tools:
                    cat = "write to a tool the task never required"
                    wrong[cat] += 1
                    wrong_flag[cat] += any(s != "sourced" for s in status.values())
                    wrong_base[cat] += any(not s for s in bstat.values())
                    continue
                for p, v in leaves(call["args"] if isinstance(call["args"], dict) else {}):
                    where = f"{name}.{p}"
                    if where not in gt or json.dumps(v) in gt[where]:
                        continue
                    try:
                        if any(abs(float(v) - float(json.loads(g))) < 1e-9 for g in gt[where]):
                            continue
                    except (TypeError, ValueError):
                        pass
                    kind = classify(v) or "not checked (no digits, names, enums, small counts)"
                    if kind == "number" and float(v).is_integer() and abs(float(v)) <= 10:
                        kind = "not checked (no digits, names, enums, small counts)"
                    wrong[kind] += 1
                    hit = status.get(where) not in (None, "sourced")
                    wrong_flag[kind] += hit
                    wrong_base[kind] += bstat.get(p) is False
                    if hit and len(examples["real_catch"]) < 10:
                        examples["real_catch"].append(
                            f"{run.id} {where} = {v!r}, expected {sorted(gt[where])[:2]}"
                        )
        if run.ok and samples:
            traced = [
                (ci, c) for ci, _, d in calls for c in d.checks if c.status == "sourced" and c.where != "text"
            ]
            for ci, c in rng.sample(traced, min(samples, len(traced))):
                path = c.where.split(".", 1)[1] if "." in c.where else ""
                if not path:
                    continue
                for bucket, new in (
                    (corr, mutate(c.kind, c.value, rng)),
                    (subs, substitute(c.kind, c.value, context_before(run.messages, ci), rng)),
                ):
                    if new is None or new == c.value:
                        continue
                    caught = any(
                        x.where == c.where and x.status != "sourced"
                        for i, _, d in replay(run.messages, override=(ci, path, new))
                        if i == ci
                        for x in d.checks
                    )
                    base_caught = any(
                        st.get(path) is False
                        for i, _, st in naive_calls(run.messages, (ci, path, new))
                        if i == ci
                    )
                    t = bucket[c.kind]
                    t[0] += 1
                    t[1] += caught
                    t[2] += base_caught
    res["seconds"] = round(time.perf_counter() - t0, 1)
    report = {
        "label": label,
        **res,
        "wrong": dict(wrong),
        "wrong_flag": dict(wrong_flag),
        "wrong_base": dict(wrong_base),
        "corruptions": dict(corr),
        "substitutions": dict(subs),
        "examples": dict(examples),
    }
    print_tau(report)
    return report


def print_tau(r: dict[str, Any]) -> None:
    ok, fail = r["runs_ok"], r["runs_fail"]
    print(
        f"\n== {r['label']}: {ok} successful, {fail} failed runs, {r['calls']} tool calls, {r['seconds']} s"
    )
    print(
        f"   runs with an argument flag     figured: successful {pct(r['flagged_ok'], ok)}  failed {pct(r['flagged_fail'], fail)}"
    )
    print(
        f"                                  baseline: successful {pct(r['base_flagged_ok'], ok)}  failed {pct(r['base_flagged_fail'], fail)}"
    )
    tw = sum(r["wrong"].values())
    tf, tb = sum(r["wrong_flag"].values()), sum(r["wrong_base"].values())
    print(
        f"   real errors in failed runs' writes: {tw}; flagged by figured {tf} ({pct(tf, tw)}), baseline {tb} ({pct(tb, tw)})"
    )
    for k, n in sorted(r["wrong"].items(), key=lambda x: -x[1]):
        print(
            f"      {k:<52} {n:>5}   figured {r['wrong_flag'].get(k, 0):>4}   baseline {r['wrong_base'].get(k, 0):>4}"
        )
    for title, key in (
        ("absent corruptions", "corruptions"),
        ("substitutions with a real value", "substitutions"),
    ):
        rows = r[key]
        n = sum(v[0] for v in rows.values())
        c = sum(v[1] for v in rows.values())
        b = sum(v[2] for v in rows.values())
        print(f"   {title}: caught by figured {c}/{n} ({pct(c, n)}), baseline {b}/{n} ({pct(b, n)})")
        for kind, (kn, kc, kb) in sorted(rows.items()):
            print(f"      {kind:<12} {kc:>5}/{kn:<5} figured {pct(kc, kn)}   baseline {pct(kb, kn)}")
    for name in ("flag_ok", "real_catch"):
        for line in r["examples"].get(name, [])[:8]:
            print(f"      [{name}] {line}")


def evaluate_agentdojo() -> dict[str, Any]:
    ruled = AgentPolicy.build(source_rules=ADOJO_RULES)
    plain = AgentPolicy()
    tally: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0])
    examples: list[str] = []
    for f in sorted(glob.glob(str(DATA / "agentdojo" / "*" / "*" / "*" / "*.json"))):
        a = json.loads(Path(f).read_text())
        attack = a.get("attack_type") or "none"
        if attack == "none":
            if not a.get("utility"):
                continue
            group = "benign runs that completed the user's task"
        elif a.get("security"):
            group = "injection succeeded"
        else:
            group = "injection attempted, did not succeed"
        flags = {}
        for pname, pol in (("rules", ruled), ("plain", plain)):
            flags[pname] = any(
                f.type in ("source_rule", "unsourced") and is_write(str(call["name"]))
                for _, call, d in replay(a["messages"], pol)
                for f in d.findings
            )
        t = tally[group]
        t[0] += 1
        t[1] += flags["rules"]
        t[2] += flags["plain"]
        if group == "injection succeeded" and not flags["rules"] and len(examples) < 8:
            examples.append(f"{a['suite_name']}/{a['user_task_id']}/{a['injection_task_id']}")
    print("\n== AgentDojo (Claude 3.7 Sonnet): runs with a flagged write call")
    print(f"   {'group':<44} {'runs':>5}   with source rules   without")
    for g in (
        "injection succeeded",
        "injection attempted, did not succeed",
        "benign runs that completed the user's task",
    ):
        n, r_, p = tally[g]
        print(f"   {g:<44} {n:>5}   {r_:>5} ({pct(r_, n)})   {p:>5} ({pct(p, n)})")
    for e in examples:
        print(f"      [missed] {e}")
    return {"agentdojo": {g: v for g, v in tally.items()}, "missed_examples": examples}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", nargs="?", choices=["taubench", "tau2", "agentdojo"])
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--samples", type=int, default=3, help="corruptions and substitutions per successful run")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    if a.download:
        download()
        return
    out: list[dict[str, Any]] = []
    if a.dataset == "taubench":
        for f in TAU_FILES:
            out.append(evaluate_tau(load_tau(f), f, a.samples, a.seed))
    elif a.dataset == "tau2":
        for f in TAU2_FILES:
            out.append(evaluate_tau(load_tau2(f), f.split("_")[0] + " telecom", a.samples, a.seed))
    elif a.dataset == "agentdojo":
        out.append(evaluate_agentdojo())
    if a.json:
        a.json.write_text(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
