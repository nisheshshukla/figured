"""Fuzz and robustness: whatever comes in, figured returns a verdict and never raises, and the same
input always gives the same answer. The explicit cases are every input class an adversarial review
found crashing in some version."""

from __future__ import annotations

import json
import math
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from figured import trace
from figured.agents import AgentPolicy, RunMonitor, check_run

scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**40), max_value=10**40),
    st.floats(allow_nan=True, allow_infinity=True, width=64),
    st.text(max_size=60),
    st.binary(max_size=20),
)
jsonish = st.recursive(
    scalars,
    lambda inner: st.one_of(
        st.lists(inner, max_size=5), st.dictionaries(st.text(max_size=12), inner, max_size=5)
    ),
    max_leaves=30,
)
texts = st.text(max_size=400)


def feed(m: RunMonitor, text: Any, output: Any, args: Any) -> list[str]:
    m.system(text)
    m.user(text)
    m.assistant(text)
    m.tool_result("lookup", output, "c1")
    d1 = m.before_call("act", args, "c2")
    m.tool_result("act", output, "c2")
    d2 = m.before_call("act", args)
    return [d1.action, d2.action, json.dumps(m.report().to_dict(), sort_keys=True, default=str)]


@settings(max_examples=300, deadline=None)
@given(text=texts, output=jsonish, args=jsonish)
def test_monitor_never_raises_and_is_deterministic(text: str, output: Any, args: Any) -> None:
    first = feed(RunMonitor(AgentPolicy.build(source_rules={"act.*": ["user"]})), text, output, args)
    second = feed(RunMonitor(AgentPolicy.build(source_rules={"act.*": ["user"]})), text, output, args)
    assert first == second
    assert all(a in ("allow", "warn", "confirm", "block") for a in first[:2])


@settings(max_examples=200, deadline=None)
@given(
    text=texts,
    rows=st.lists(
        st.dictionaries(
            st.text(min_size=1, max_size=8),
            st.one_of(
                st.integers(), st.floats(allow_nan=True, allow_infinity=True), st.text(max_size=12), st.none()
            ),
            max_size=6,
        ),
        max_size=8,
    ),
)
def test_trace_never_raises_and_is_deterministic(text: str, rows: list[dict[str, Any]]) -> None:
    a = trace(text, rows)
    b = trace(text, rows)
    assert a.to_dict() == b.to_dict()
    assert a.coincidence() is None or 0.0 <= a.coincidence() <= 1.0


def test_malformed_inputs_from_the_reviews() -> None:
    m = RunMonitor(tools=["not a dict", {"name": "t"}, {"function": {}}])
    m.system(None)  # type: ignore[arg-type]
    m.user(b"bytes \xff\x00")  # type: ignore[arg-type]
    m.user(123)  # type: ignore[arg-type]
    m.assistant(None)  # type: ignore[arg-type]
    m.tool_result("t", b"\x00binary")
    m.tool_result("t", {(1, 2): "tuple key", 3: "int key"})
    m.tool_result("t", {"n": float("nan"), "i": float("inf"), "big": 10**400})
    m.tool_result("t", '{"n": NaN, "i": Infinity, "s": "1e999 and 550e8400-e29b-41d4-a716-446655440000"}')
    deep: dict[str, Any] = {}
    node = deep
    for _ in range(10_000):
        node["x"] = {}
        node = node["x"]
    m.tool_result("t", deep)
    for args in (
        deep,
        {"k": b"v", (1, 2): 3},
        "[" * 50_000,
        '"bare"',
        None,
        7,
        [1, [2, [3]]],
        {"amount": float("nan")},
    ):
        assert m.before_call("t", args).action in ("allow", "warn", "confirm", "block")  # type: ignore[arg-type]
    assert trace(b"It was 5.", [{"a": 5}]).ok  # type: ignore[arg-type]
    assert trace(None, [{"a": 5}]).checked == 0  # type: ignore[arg-type]
    assert trace("5 and 10**400", [{"a": 10**400, "b": float("nan")}]).checked >= 0
    assert check_run([], on_unknown="ignore").ok


def test_unicode_and_lookalikes() -> None:
    m = RunMonitor(AgentPolicy.build(source_rules={"send.*": ["user"]}))
    m.user("Send to john@example.com, Zürich, 東京, order №12345.")
    assert m.before_call("send", {"to": "john@example.com"}).action == "allow"
    assert m.before_call("send", {"to": "j" + chr(0x43E) + "hn@example.com"}).action == "block"  # Cyrillic o
    assert (
        m.before_call("send", {"to": "john@example.com" + chr(0x200B)}).action == "block"
    )  # zero-width space
    assert m.before_call("note", {"city": "東京"}).action == "allow"


def test_numbers_at_the_edges_of_float() -> None:
    m = RunMonitor()
    m.tool_result("t", {"a": 1e308, "b": 5e-324, "c": -0.0, "d": 0})
    for v in (1e308, 5e-324, 0.0, -0.0, 1e309, -1e309, math.nan):
        assert m.before_call("t", {"v": v}).action in ("allow", "warn")
