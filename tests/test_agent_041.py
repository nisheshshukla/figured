"""Dates in search tools, numbers written two ways, read-call bounds, and AgentDojo's newer format."""

from __future__ import annotations

import datetime as dt

from figured.agents import AgentPolicy, RunMonitor, check_run
from figured.agents.values import classify, date_periods, parse_dates


def test_timestamps_and_two_digit_years_are_dates() -> None:
    assert classify("2025-08-02T23:59:59Z") == "date"
    assert classify("2025-08-02T00:00:00+05:30") == "date"
    assert parse_dates("expires 26-01-09, show 25-09-10-19-00, call 555-12-34") == {
        (2026, 1, 9),
        (2025, 9, 10),
    }
    m = RunMonitor()
    m.user("I was charged twice on August 2nd.")
    assert m.before_call("dispute", {"date": "2025-08-02T23:59:59Z"}).action == "allow"
    m.tool_result("batches", {"expiration_date": "26-01-01"})
    assert m.before_call("fill", {"expiration_date": "2026-01-01"}).action == "allow"


def test_periods_the_user_names() -> None:
    assert date_periods("my expenses from August") == {(None, 8, 1), (None, 8, 31), (None, 9, 1)}
    assert date_periods("donations made in 2024") == {(2024, 1, 1), (2024, 12, 31), (2025, 1, 1)}
    assert (2025, 9, 30) in date_periods("anything this month?", dt.date(2025, 9, 15))
    assert (2025, 8, 1) in date_periods("last month", dt.date(2025, 9, 15))
    assert date_periods("May I ask something?") == set()
    assert (2025, 5, 31) in date_periods("in May 2025")
    assert (2024, 2, 28) in date_periods("for February") or (None, 2, 28) in date_periods("for February")
    assert parse_dates("the 8th of this month", dt.date(2024, 5, 15)) == {(2024, 5, 8)}
    m = RunMonitor()
    m.user("Show me my grocery spending in August.")
    check = m.before_call("summarize", {"until": "2025-08-31"}).checks[0]
    assert check.status == "sourced" and check.how == "derived:period"
    assert m.before_call("summarize", {"until": "2025-08-30"}).action == "warn"


def test_bare_digit_amounts_in_user_text() -> None:
    m = RunMonitor()
    m.user("Please send 15000 to my savings, and a budget of 25000 for the trip.")
    assert m.before_call("transfer", {"amount": 15000}).action == "allow"
    assert m.before_call("budget", {"amount": 25000}).action == "allow"
    m.user("My zip is 19122 and my order is 9502127.")
    assert m.before_call("refund", {"amount": 19122}).action == "warn"
    assert m.before_call("refund", {"amount": 9502127}).action == "warn"
    m.tool_result("account", {"balance": 12345.0})
    assert m.before_call("close", {"account_number": "12345"}).action == "warn"


def test_read_calls_may_choose_their_own_bounds() -> None:
    m = RunMonitor()
    m.user("Anything odd on my account lately? My id is AC-778812.")
    d = m.before_call(
        "search_transactions",
        {
            "account_id": "AC-778812",
            "start_date": "2025-08-15",
            "end_date": "2025-09-10",
            "limit": 50,
            "min_amount": 100,
        },
    )
    assert d.action == "allow" and [c.where for c in d.checks] == ["search_transactions.account_id"]
    assert m.before_call("search_transactions", {"account_id": "AC-778813"}).action == "warn"
    assert m.before_call("refund", {"amount": 100}).action == "warn"
    strict = RunMonitor(AgentPolicy(search_bounds="check"))
    assert strict.before_call("search_transactions", {"start_date": "2025-08-15"}).action == "warn"
    tools = [{"name": "transactions", "annotations": {"readOnlyHint": True}}]
    hinted = RunMonitor(tools=tools)  # a server's own annotation is not trusted
    assert hinted.before_call("transactions", {"start_date": "2025-08-15"}).action == "warn"
    listed = RunMonitor(AgentPolicy.build(read_tools=["transactions"]), tools=tools)
    assert listed.before_call("transactions", {"start_date": "2025-08-15"}).action == "allow"
    prefixed = RunMonitor()
    assert (
        prefixed.before_call("pubmed-mcp-server-search_pubmed", {"start_date": "2022-01-01"}).action
        == "allow"
    )
    assert prefixed.before_call("getTransactions", {"start_date": "2022-01-01"}).action == "allow"
    assert RunMonitor().before_call("get_customer", {"dob": "1970-01-01"}).action == "warn"


def test_agentdojo_content_blocks() -> None:
    msgs = [
        {"role": "system", "content": [{"type": "text", "content": "You are an assistant."}]},
        {"role": "user", "content": [{"type": "text", "content": "Pay the bill 'bill.txt'."}]},
        {
            "role": "assistant",
            "content": [{"type": "text", "content": "Reading it."}],
            "tool_calls": [
                {"function": "send_money", "args": {"recipient": "UK99999999999999999999"}, "id": "1"}
            ],
        },
    ]
    rep = check_run(msgs)
    assert [c.where for c in rep.unsourced] == ["send_money.recipient"]
