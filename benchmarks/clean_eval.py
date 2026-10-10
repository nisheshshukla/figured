"""The clean evaluation of figured 0.4.0 specified in docs/clean-eval-protocol.md.

python benchmarks/clean_eval.py toolscale|tau2|telecom|agentdojo [--json path]

Data goes in benchmarks/data/clean/ (see the protocol for sources). The measurement procedures are
0.4.0's, imported from agent_eval.py.
"""

from __future__ import annotations

import argparse
import ast
import collections
import glob
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
import agent_eval as ae

from figured.agents import AgentPolicy

CLEAN = ae.DATA / "clean"
TOOLSCALE = ["deepseek-v4-pro", "Qwen-3.6-plus"]
TAU2_MODELS = {
    "airline": [
        "claude-3-7-sonnet-20250219_airline_default",
        "gpt-4.1-2025-04-14_airline_default",
        "o4-mini-2025-04-16_airline_default",
        "gpt-4.1-mini-2025-04-14_airline_base",
    ],
    "retail": [
        "claude-3-7-sonnet-20250219_retail_default",
        "gpt-4.1-2025-04-14_retail_default",
        "o4-mini-2025-04-16_retail_default",
        "gpt-4.1-mini-2025-04-14_retail_base",
    ],
    "telecom": ["o4-mini-2025-04-16_telecom_default", "gpt-4.1-mini-2025-04-14_telecom_base"],
}
ADOJO_MODELS = [
    "gpt-4o-2024-05-13",
    "gpt-4o-mini-2024-07-18",
    "claude-3-5-sonnet-20241022",
    "gemini-2.0-flash-001",
    "meta-llama_Llama-3.3-70B-Instruct",
]
REVIEW = 40


# loading


def load_toolscale(name: str) -> list[ae.Run]:
    rows = json.loads((CLEAN / "toolscale" / f"{name}.json").read_text())
    convs: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = r["id"].rsplit("_t", 1)[0] if "reward" in r else r["id"]
        if key not in convs or len(r["messages"]) > len(convs[key]["messages"]):
            convs[key] = r
    out = []
    for key, r in sorted(convs.items()):
        ok = r["reward"] >= 1.0 if "reward" in r else r["score"] >= 1.0
        msgs = [_clean_message(m) for m in r["messages"]]
        tools = r["tools"] if isinstance(r["tools"], list) else json.loads(r["tools"])
        out.append(ae.Run(f"{name}:{r['domain']}:{key}", ok, msgs, [], tools))
    return out


def _clean_message(m: dict[str, Any]) -> dict[str, Any]:
    """Parquet gives every message every column; drop the empty ones so formats read them as written."""
    return {k: v for k, v in m.items() if v not in (None, "", [], {}) or k == "content"}


def tau2_schemas(domain: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Tool schemas from tau2's tools.py: functions marked @is_tool, argument descriptions from the
    docstring's Args section. Returns the schemas and the names of WRITE tools."""
    tree = ast.parse((CLEAN / "tau2" / f"{domain}_tools.py").read_text())
    tools, writes = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        kinds = [
            ast.unparse(d.args[0])
            for d in node.decorator_list
            if isinstance(d, ast.Call) and ast.unparse(d.func) == "is_tool"
        ]
        if not kinds:
            continue
        if kinds[0].endswith("WRITE"):
            writes.append(node.name)
        doc = ast.get_docstring(node) or ""
        descs: dict[str, str] = {}
        current = None
        in_args = False
        for line in doc.splitlines():
            stripped = line.strip()
            if stripped in ("Args:", "Arguments:"):
                in_args = True
                continue
            if in_args and stripped.endswith(":") and " " not in stripped:
                in_args = False
            if not in_args or not stripped:
                continue
            head, sep, rest = stripped.partition(":")
            if sep and head.isidentifier() and line.startswith(" " * 4) and not line.startswith(" " * 8):
                current = head
                descs[current] = rest.strip()
            elif current:
                descs[current] += " " + stripped
        props: dict[str, Any] = {}
        for a in node.args.args[1:]:
            ann = ast.unparse(a.annotation) if a.annotation else ""
            desc = descs.get(a.arg, "")
            if ann.startswith(("List", "list")):
                props[a.arg] = {
                    "type": "array",
                    "description": desc,
                    "items": {"type": "string", "description": desc},
                }
            else:
                props[a.arg] = {"type": "string", "description": desc}
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": node.name,
                    "description": doc.split("\n")[0],
                    "parameters": {"type": "object", "properties": props},
                },
            }
        )
    return tools, writes


def load_tau2(name: str, tools: list[dict[str, Any]]) -> list[ae.Run]:
    d = json.loads((CLEAN / "tau2" / f"{name}_gpt-4.1-2025-04-14_4trials.json").read_text())
    tasks = {t["id"]: t for t in d["tasks"]}
    system = d["info"].get("environment_info", {}).get("policy") if isinstance(d.get("info"), dict) else None
    out = []
    for s in d["simulations"]:
        crit = tasks[s["task_id"]]["evaluation_criteria"] or {}
        acts = [
            (a["name"], a.get("arguments") or {})
            for a in crit.get("actions") or []
            if a.get("requestor", "assistant") == "assistant"
        ]
        msgs = ([{"role": "system", "content": system}] if system else []) + s["messages"]
        out.append(
            ae.Run(
                f"{name}:{s['task_id']}/{s.get('trial', 0)}",
                s["reward_info"]["reward"] >= 1.0,
                msgs,
                acts,
                tools,
            )
        )
    return out


# measuring


def evaluate(
    runs: list[ae.Run], label: str, writes: set[str] | None, ground_truth: bool, selection: bool | None = None
) -> dict[str, Any]:
    rng = random.Random(7)
    t: dict[str, Any] = collections.defaultdict(int)
    flags_ok: list[str] = []
    if selection is None:
        selection = writes is not None or not ground_truth
    t0 = time.perf_counter()
    for run in runs:
        key = "ok" if run.ok else "fail"
        t[f"runs_{key}"] += 1
        try:
            calls = []
            for item in ae.replay(run.messages, tools=run.tools):
                calls.append(item)
            base = {ci: st for ci, _, st in ae.naive_calls(run.messages)}
        except Exception as e:  # a transcript figured cannot read is reported, not skipped
            t["unreadable"] += 1
            t.setdefault("unreadable_examples", []).append(f"{run.id}: {type(e).__name__}: {str(e)[:120]}")
            continue
        t[f"calls_{key}"] += len(calls)
        run_writes = (
            writes
            if writes is not None
            else {
                str(x.get("function", x).get("name"))
                for x in run.tools or []
                if ae.is_write(str(x.get("function", x).get("name")))
            }
        )

        def is_write(n: str, run_writes: set[str] = run_writes) -> bool:
            return n in run_writes if run_writes else ae.is_write(n)

        flagged = [(c, ch) for _, c, d in calls for ch in d.checks if ch.status != "sourced"]
        t[f"flagged_{key}"] += bool(flagged)
        t[f"base_flagged_{key}"] += any(not s for st in base.values() for s in st.values())
        t[f"unknown_tool_{key}"] += sum(
            1 for _, _, d in calls for f in d.findings if f.type == "unknown_tool"
        )
        if run.ok:
            for _, ch in flagged:
                flags_ok.append(f"{run.id} {ch.where} = {ch.value!r}")
        # selection checks
        if selection:
            sel_pol = AgentPolicy.build(
                ignore=["think.*"],
                pure_tools=["calculate"],
                ambiguous_before=sorted(run_writes),
                confirm_before=sorted(run_writes),
            )
            for _, c, d in ae.replay(run.messages, sel_pol, tools=run.tools):
                if is_write(str(c["name"])):
                    t[f"writes_{key}"] += 1
                    types = {f.type for f in d.findings}
                    t[f"ambiguous_{key}"] += "ambiguous_selection" in types
                    t[f"unconfirmed_{key}"] += "unconfirmed" in types
        # real errors against required actions
        if ground_truth and not run.ok:
            gt: dict[str, set[str]] = collections.defaultdict(set)
            gt_tools = {n for n, _ in run.expected}
            for n, kw in run.expected:
                for p, v in ae.leaves(kw):
                    gt[f"{n}.{p}"].add(json.dumps(v))
            for ci, call, d in calls:
                name = str(call["name"])
                if not is_write(name):
                    continue
                status = {c.where: c.status for c in d.checks}
                bstat = base.get(ci, {})
                if name not in gt_tools:
                    t["wrong_tool"] += 1
                    t["wrong_tool_flagged"] += any(s != "sourced" for s in status.values())
                    continue
                for p, v in ae.leaves(call["args"] if isinstance(call["args"], dict) else {}):
                    where = f"{name}.{p}"
                    if where not in gt or json.dumps(v) in gt[where]:
                        continue
                    try:
                        if any(abs(float(v) - float(json.loads(g))) < 1e-9 for g in gt[where]):
                            continue
                    except (TypeError, ValueError):
                        pass
                    t["wrong_value"] += 1
                    t["wrong_value_flagged"] += status.get(where) not in (None, "sourced")
                    t["wrong_value_base"] += bstat.get(p) is False
        # corruptions and substitutions on successful runs
        if run.ok:
            traced = [
                (ci, c) for ci, _, d in calls for c in d.checks if c.status == "sourced" and c.where != "text"
            ]
            for ci, c in rng.sample(traced, min(3, len(traced))):
                path = c.where.split(".", 1)[1] if "." in c.where else ""
                if not path:
                    continue
                for bucket, new in (
                    ("corr", ae.mutate(c.kind, c.value, rng)),
                    ("subs", ae.substitute(c.kind, c.value, ae.context_before(run.messages, ci), rng)),
                ):
                    if new is None or new == c.value:
                        continue
                    caught = any(
                        x.where == c.where and x.status != "sourced"
                        for i, _, d in ae.replay(run.messages, override=(ci, path, new), tools=run.tools)
                        if i == ci
                        for x in d.checks
                    )
                    base_caught = any(
                        st.get(path) is False
                        for i, _, st in ae.naive_calls(run.messages, (ci, path, new))
                        if i == ci
                    )
                    t[f"{bucket}_n"] += 1
                    t[f"{bucket}_caught"] += caught
                    t[f"{bucket}_base"] += base_caught
    t["seconds"] = round(time.perf_counter() - t0, 1)
    review = sorted(set(flags_ok))
    if len(review) > REVIEW:
        review = random.Random(7).sample(review, REVIEW)
    res = {
        "label": label,
        **t,
        "flags_on_successful_runs": len(flags_ok),
        "review": review,
        "all_flags": flags_ok,
    }
    show(res, ground_truth)
    return res


def show(r: dict[str, Any], ground_truth: bool) -> None:
    pct = ae.pct
    ok, fail = r.get("runs_ok", 0), r.get("runs_fail", 0)
    print(f"\n== {r['label']}: {ok} successful, {fail} failed runs, {r['seconds']} s")
    if r.get("unreadable"):
        print(f"   unreadable transcripts: {r['unreadable']}  e.g. {r['unreadable_examples'][:2]}")
    print(
        f"   successful runs flagged   figured {pct(r.get('flagged_ok', 0), ok)}  baseline {pct(r.get('base_flagged_ok', 0), ok)}"
    )
    print(
        f"   failed runs flagged       figured {pct(r.get('flagged_fail', 0), fail)}  baseline {pct(r.get('base_flagged_fail', 0), fail)}"
    )
    print(
        f"   corruptions caught        figured {r.get('corr_caught', 0)}/{r.get('corr_n', 0)}  baseline {r.get('corr_base', 0)}"
    )
    print(
        f"   substitutions caught      figured {r.get('subs_caught', 0)}/{r.get('subs_n', 0)}  baseline {r.get('subs_base', 0)}"
    )
    print(
        f"   calls to unknown tools    successful runs {r.get('unknown_tool_ok', 0)}  failed runs {r.get('unknown_tool_fail', 0)}"
    )
    if r.get("writes_ok") is not None or r.get("writes_fail"):
        print(
            f"   ambiguous_before asks     successful {pct(r.get('ambiguous_ok', 0), r.get('writes_ok', 0))} of writes, "
            f"failed {pct(r.get('ambiguous_fail', 0), r.get('writes_fail', 0))}"
        )
        print(
            f"   confirm_before asks       successful {pct(r.get('unconfirmed_ok', 0), r.get('writes_ok', 0))} of writes, "
            f"failed {pct(r.get('unconfirmed_fail', 0), r.get('writes_fail', 0))}"
        )
    if ground_truth:
        print(
            f"   real wrong values flagged {r.get('wrong_value_flagged', 0)}/{r.get('wrong_value', 0)}  baseline {r.get('wrong_value_base', 0)};"
            f" unneeded writes flagged {r.get('wrong_tool_flagged', 0)}/{r.get('wrong_tool', 0)}"
        )


def evaluate_agentdojo(model: str) -> dict[str, Any]:
    policies = {"rules": AgentPolicy.build(source_rules=ae.ADOJO_RULES), "plain": AgentPolicy()}
    groups = (
        "injection succeeded",
        "injection attempted, did not succeed",
        "benign runs that completed the user's task",
    )
    tally = {p: {g: collections.defaultdict(int) for g in groups} for p in policies}
    unreadable: list[str] = []
    root = CLEAN / "agentdojo-src" / "runs" / model
    for f in sorted(glob.glob(str(root / "*" / "*" / "*" / "*.json"))):
        attack_dir = Path(f).parent.name
        if attack_dir not in ("important_instructions", "none"):
            continue
        a = json.loads(Path(f).read_text())
        if attack_dir == "none":
            if not a.get("utility"):
                continue
            group = groups[2]
        elif a.get("security"):
            group = groups[0]
        else:
            group = groups[1]
        for pname, pol in policies.items():
            try:
                found = [
                    fd
                    for _, call, d in ae.replay(a["messages"], pol)
                    if ae.is_write(str(call["name"]))
                    for fd in d.findings
                    if fd.type in ("source_rule", "unsourced")
                ]
            except Exception as e:
                if pname == "rules":
                    unreadable.append(f"{f}: {type(e).__name__}: {str(e)[:100]}")
                continue
            levels = {fd.severity for fd in found}
            t = tally[pname][group]
            t["runs"] += 1
            t["flagged"] += bool(found)
            t["blocked"] += "block" in levels
            t["warn_only"] += levels == {"warn"}
    print(f"\n== AgentDojo {model}" + (f"  (unreadable: {len(unreadable)})" if unreadable else ""))
    for pname in policies:
        for g in groups:
            t = tally[pname][g]
            print(
                f"   {pname:<6} {g:<44} {t['runs']:>5}  flagged {ae.pct(t['flagged'], t['runs'])}  blocked {ae.pct(t['blocked'], t['runs'])}"
            )
    return {
        "model": model,
        "tally": {p: {g: dict(v) for g, v in d.items()} for p, d in tally.items()},
        "unreadable": unreadable[:20],
        "n_unreadable": len(unreadable),
    }


def speed(runs: list[ae.Run]) -> dict[str, float]:
    from figured.agents import RunMonitor, events

    us: list[float] = []
    for r in runs:
        m = RunMonitor(ae.POLICY, tools=r.tools)
        for kind, ev in events(r.messages):
            if kind == "system":
                m.system(ev["text"])
            elif kind == "user":
                m.user(ev["text"])
            elif kind == "assistant":
                m.assistant(ev["text"])
            elif kind == "result":
                m.tool_result(str(ev["name"]), ev["output"], ev.get("id"))
            elif kind == "call":
                t = time.perf_counter()
                m.before_call(str(ev["name"]), ev["args"], ev.get("id"))
                us.append((time.perf_counter() - t) * 1e6)
    us.sort()
    res = {"calls": len(us), "p50_us": us[len(us) // 2], "p99_us": us[int(len(us) * 0.99)]}
    print(
        f"\n== speed on ToolScale: {res['calls']} calls, before_call p50 {res['p50_us']:.1f} µs, p99 {res['p99_us']:.1f} µs"
    )
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("set", choices=["toolscale", "tau2", "telecom", "agentdojo"])
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    out: list[dict[str, Any]] = []
    if a.set == "toolscale":
        everything = []
        for name in TOOLSCALE:
            runs = load_toolscale(name)
            everything += runs
            out.append(evaluate(runs, f"ToolScale {name}", None, ground_truth=False))
        out.append({"speed": speed(everything)})
    elif a.set == "tau2":
        for domain in ("airline", "retail"):
            tools, writes = tau2_schemas(domain)
            for name in TAU2_MODELS[domain]:
                out.append(evaluate(load_tau2(name, tools), name, set(writes), ground_truth=True))
    elif a.set == "telecom":
        tools = [{"name": n} for n in ae.TAU2_AGENT_TOOLS]
        for name in TAU2_MODELS["telecom"]:
            out.append(evaluate(load_tau2(name, tools), name, None, ground_truth=True))
    elif a.set == "agentdojo":
        for model in ADOJO_MODELS:
            out.append(evaluate_agentdojo(model))
    if a.json:
        a.json.write_text(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
