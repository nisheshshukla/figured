from __future__ import annotations

import datetime as dt
import json
import warnings
from types import SimpleNamespace

import pytest

from figured.agents import AgentPolicy, RunMonitor, UnrecognizedMessage, check_run, events


def kinds(messages: list) -> list[str]:  # type: ignore[type-arg]
    return [k for k, _ in events(messages)]


def test_openai_legacy_function_call_and_developer_role() -> None:
    msgs = [
        {"role": "developer", "content": "Rules."},
        {"role": "user", "content": "Order ORD-1234567"},
        {
            "role": "assistant",
            "content": None,
            "function_call": {"name": "get", "arguments": '{"id": "ORD-1234567"}'},
        },
        {"role": "function", "name": "get", "content": '{"ok": true}'},
    ]
    assert kinds(msgs) == ["system", "user", "call", "result"]
    assert check_run(msgs).ok


def test_responses_api_items() -> None:
    msgs = [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Refund ORD-1234567"}],
        },
        {
            "type": "function_call",
            "name": "refund",
            "arguments": '{"order_id": "ORD-1234576"}',
            "call_id": "c1",
        },
        {"type": "function_call_output", "call_id": "c1", "output": "done"},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Refunded."}]},
    ]
    evs = list(events(msgs))
    assert [k for k, _ in evs] == ["user", "call", "result", "assistant"]
    assert evs[2][1]["name"] == "refund"
    assert [c.where for c in check_run(msgs).unsourced] == ["refund.order_id"]


def test_gemini_parts() -> None:
    msgs = [
        {"role": "user", "parts": [{"text": "Look up ORD-1234567"}]},
        {"role": "model", "parts": [{"functionCall": {"name": "get", "args": {"id": "ORD-1234567"}}}]},
        {
            "role": "user",
            "parts": [{"functionResponse": {"name": "get", "response": {"owner": "cus_778121"}}}],
        },
        {"role": "function", "parts": [{"function_response": {"name": "get2", "response": {"x": 1}}}]},
        {
            "role": "model",
            "parts": [{"function_call": {"name": "email", "args": {"customer": "cus_778121"}}}],
        },
    ]
    evs = list(events(msgs))
    assert [k for k, _ in evs] == ["user", "call", "result", "result", "call"]
    assert evs[3][1]["name"] == "get2"
    assert check_run(msgs).ok


def test_bedrock_converse_blocks() -> None:
    msgs = [
        {"role": "user", "content": [{"text": "Ship to account AC-998877"}]},
        {
            "role": "assistant",
            "content": [{"toolUse": {"toolUseId": "t1", "name": "ship", "input": {"acct": "AC-998877"}}}],
        },
        {
            "role": "user",
            "content": [{"toolResult": {"toolUseId": "t1", "content": [{"json": {"label": "LB-55443"}}]}}],
        },
    ]
    evs = list(events(msgs))
    assert [k for k, _ in evs] == ["user", "call", "result"]
    assert evs[2][1]["name"] == "ship" and "LB-55443" in evs[2][1]["output"]


def test_langchain_dicts_and_objects() -> None:
    dicts = [
        {"type": "system", "content": "Rules."},
        {"type": "human", "content": "Order ORD-1234567"},
        {
            "type": "ai",
            "content": "",
            "tool_calls": [{"name": "get", "args": {"id": "ORD-1234567"}, "id": "x1"}],
        },
        {"type": "tool", "content": "ok", "tool_call_id": "x1"},
    ]
    assert kinds(dicts) == ["system", "user", "call", "result"]
    objs = [
        SimpleNamespace(type="human", content="Order ORD-1234567"),
        SimpleNamespace(
            type="ai", content="", tool_calls=[{"name": "get", "args": {"id": "ORD-1234576"}, "id": "x1"}]
        ),
    ]
    assert [c.where for c in check_run(objs).unsourced] == ["get.id"]


def test_sdk_objects_with_nested_tool_calls() -> None:
    fn = SimpleNamespace(name="get", arguments='{"id": "ORD-1234567"}')
    msg = SimpleNamespace(
        role="assistant", content=None, tool_calls=[SimpleNamespace(id="c1", type="function", function=fn)]
    )
    evs = list(events([{"role": "user", "content": "ORD-1234567"}, msg]))
    assert evs[1] == ("call", {"name": "get", "args": {"id": "ORD-1234567"}, "id": "c1"})


def test_tau2_and_agentdojo_shapes() -> None:
    tau2 = [
        {
            "role": "user",
            "content": "My phone is 555-123-2002",
            "tool_calls": [{"id": "u1", "name": "check_status", "arguments": {}}],
        },
        {"role": "tool", "id": "u1", "requestor": "user", "content": "Airplane mode on, line L-9988"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "a1", "name": "get_line", "arguments": {"line": "L-9988"}}],
        },
        {"role": "tool", "id": "a1", "requestor": "assistant", "content": '{"plan": "P-100"}'},
    ]
    evs = list(events(tau2))
    assert [k for k, _ in evs] == ["user", "call", "result"]
    assert evs[2][1]["name"] == "get_line"
    assert [c.where for c in check_run(tau2).unsourced] == ["get_line.line"]
    adojo = [
        {"role": "user", "content": "Pay the bill in bill.txt"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"function": "read_file", "args": {"file_path": "bill.txt"}, "id": "t1"}],
        },
        {
            "role": "tool",
            "tool_call_id": "t1",
            "tool_call": {"function": "read_file"},
            "content": "IBAN UK12345678901234567890",
        },
    ]
    assert [k for k, _ in events(adojo)] == ["user", "call", "result"]


def test_unknown_shapes_fail_loudly() -> None:
    with pytest.raises(UnrecognizedMessage):
        check_run([{"speaker": "bot", "said": "hi"}])
    with pytest.raises(UnrecognizedMessage):
        check_run([{"role": "user", "content": "hi"}, {"who": "x"}])
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assert check_run([{"who": "x"}], on_unknown="warn").ok
        assert w
    assert check_run([{"who": "x"}], on_unknown="ignore").ok
    assert check_run([{"role": "assistant", "content": ""}]).ok


def test_as_of_from_prose_and_later_system_messages() -> None:
    m = RunMonitor()
    m.system("Today is Friday, October 9, 2026.")
    m.user("Book it for tomorrow.")
    assert m.before_call("book", {"date": "2026-10-10"}).action == "allow"
    blocks = [
        {"role": "system", "content": [{"type": "text", "text": "The current date is 2026-10-09."}]},
        {"role": "user", "content": "next Friday"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "1", "function": {"name": "s", "arguments": json.dumps({"date": "2026-10-16"})}}
            ],
        },
    ]
    assert check_run(blocks).ok
    fixed = RunMonitor(AgentPolicy(as_of=dt.date(2024, 1, 1)))
    fixed.system("Today is 2026-10-09.")
    assert fixed.store.as_of == dt.date(2024, 1, 1)


def test_error_heuristic_ignores_null_errors() -> None:
    m = RunMonitor()
    m.user("job J-123456")
    m.before_call("status", {"job": "J-123456"}, call_id="1")
    m.tool_result("status", '{"error": null, "state": "running"}', call_id="1")
    assert all(f.type != "retried_error" for f in m.before_call("status", {"job": "J-123456"}).findings)
    m.tool_result("status", '{"error": "timeout"}')
    assert any(f.type == "retried_error" for f in m.before_call("status", {"job": "J-123456"}).findings)


def test_deep_arguments_and_numeric_strings() -> None:
    deep: dict = {}  # type: ignore[type-arg]
    node = deep
    for _ in range(5000):
        node["x"] = {}
        node = node["x"]
    m = RunMonitor()
    d = m.before_call("t", deep)
    assert d.action == "warn" and d.findings[0].type == "unchecked"
    assert m.before_call("t", "[" * 50_000 + "]" * 50_000).action == "allow"
    m.tool_result("price", {"price": 49.9})
    d = m.before_call("pay", {"amount": "$49.90", "qty": "7", "zip": "19122"})
    assert [c.kind for c in d.checks] == ["number", "identifier"]
    assert d.checks[0].status == "sourced" and d.checks[1].status == "unsourced"


def test_integer_ids_match_quoted_ids_but_not_amounts() -> None:
    m = RunMonitor()
    m.user("my zip is 19122")
    m.tool_result("item", '{"item_id": "1656367028"}')
    assert m.before_call("buy", {"item_id": 1656367028}).action == "allow"
    assert m.before_call("refund", {"amount": 19122}).action == "warn"
