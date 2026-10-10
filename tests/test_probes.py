"""Adversarial regression suite: the probes two independent reviewers wrote against 0.4.1 and 0.4.2.

Each probe is a short run and an expected verdict for the last call (agents) or an expected `ok`
(trace). Probes figured got right are pinned: a change that breaks one fails here. Probes it got
wrong are recorded as `known_wrong` and marked xfail(strict): a change that fixes one also fails
here, so the record gets updated rather than silently drifting. Vectors: tests/vectors/probes.json
and tests/vectors/trace_probes.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from figured import Policy, trace
from figured.agents import AgentPolicy, RunMonitor

VECTORS = Path(__file__).parent / "vectors"
AGENT_PROBES = json.loads((VECTORS / "probes.json").read_text())
TRACE_PROBES = json.loads((VECTORS / "trace_probes.json").read_text())
FLAGS = ("warn", "confirm", "block")


def run_probe(p: dict[str, Any]) -> str:
    policy = AgentPolicy.build(**p["policy"]) if p["policy"] else None
    m = RunMonitor(policy, system=p.get("system"), tools=p.get("tools"))
    action = None
    for s in p["steps"]:
        kind = s[0]
        if kind == "system":
            m.system(s[1])
        elif kind == "user":
            m.user(s[1])
        elif kind == "assistant":
            m.assistant(s[1])
        elif kind == "tool":
            m.tool_result(s[1], s[2], s[3] if len(s) > 3 else None)
        elif kind == "call":
            action = m.before_call(s[1], s[2], s[3] if len(s) > 3 else None).action
    assert action is not None
    return action


def correct(expect: str, action: str) -> bool:
    return action == "allow" if expect == "pass" else action in FLAGS


def params(probes: list[dict[str, Any]]) -> list[Any]:
    out = []
    for p in probes:
        marks = (
            [pytest.mark.xfail(strict=True, reason="known wrong; update the vector if fixed")]
            if p["outcome"] == "known_wrong"
            else []
        )
        out.append(pytest.param(p, id=p["id"], marks=marks))
    return out


@pytest.mark.parametrize("p", params(AGENT_PROBES))
def test_agent_probe(p: dict[str, Any]) -> None:
    action = run_probe(p)
    assert correct(p["expect"], action), f"{p['desc']}: expected {p['expect']}, got {action}"


def trace_kwargs(policy: dict[str, Any]) -> dict[str, Any]:
    kw = dict(policy)
    if isinstance(kw.get("policy"), dict) and "__policy__" in kw["policy"]:
        d = dict(kw["policy"]["__policy__"])
        d["derivations"] = frozenset(d["derivations"])
        d["year_range"] = tuple(d["year_range"])
        kw["policy"] = Policy(**d)
    if isinstance(kw.get("derivations"), list):
        kw["derivations"] = set(kw["derivations"])
    return kw


@pytest.mark.parametrize("p", params(TRACE_PROBES))
def test_trace_probe(p: dict[str, Any]) -> None:
    report = trace(p["text"], p["rows"], **trace_kwargs(p["policy"]))
    assert report.ok == p["expect_ok"], f"{p['desc']}: {report.explain()}"


def test_probe_tallies_are_as_published() -> None:
    """The counts the docs quote: fresh reviewer 30/30 and 29/30; code-access reviewer 25/37, 19/21, 25/30."""
    by = {}
    for p in AGENT_PROBES:
        key = (p["source"], p["group"])
        n, c = by.get(key, (0, 0))
        by[key] = (n + 1, c + (p["outcome"] == "correct"))
    assert by[("review-0.4.2-fresh", "correct_calls")] == (30, 30)
    assert by[("review-0.4.2-fresh", "bad_calls")] == (30, 29)
    assert by[("review-0.4.1", "heuristics")] == (37, 25)
    assert by[("review-0.4.1", "security")] == (21, 19)
    assert by[("review-0.4.1", "false_flags")] == (30, 25)
