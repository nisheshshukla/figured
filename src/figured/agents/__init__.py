"""Checks for agent runs: values an agent acts on should appear in, or follow from, what it saw.

Every identifier, email, URL, date, and amount an agent passes to a tool, or states in its answer,
is looked for in what the user said, the system prompt, and earlier tool results, including simple
arithmetic over those. `RunMonitor` checks each tool call before it executes (an online guardrail);
`check_run` replays a finished transcript (an offline or trace-level eval).
"""

from __future__ import annotations

from typing import Any

from .formats import OnUnknown, UnrecognizedMessage, events
from .formats import _text as _content_text
from .monitor import AgentPolicy, Decision, Finding, RunMonitor, RunReport, ValueCheck


def check_run(
    messages: list[Any],
    policy: AgentPolicy | None = None,
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    on_unknown: OnUnknown = "raise",
) -> RunReport:
    """Replay a finished run through a RunMonitor.

    Reads OpenAI (Chat Completions and Responses), Anthropic, Gemini, Bedrock Converse, and LangChain
    message shapes. A message it cannot read raises UnrecognizedMessage unless `on_unknown` is "warn"
    or "ignore", so an unreadable transcript is never reported as clean.
    """
    monitor = RunMonitor(policy, tools=tools)
    if system:
        monitor.system(system)
    for kind, ev in events(messages, None, on_unknown):
        if kind == "system":
            monitor.system(ev["text"])
        elif kind == "user":
            monitor.user(ev["text"])
        elif kind == "assistant":
            monitor.assistant(ev["text"])
        elif kind == "call":
            monitor.before_call(str(ev["name"]), ev["args"], ev.get("id"))
        elif kind == "result":
            monitor.tool_result(str(ev["name"]), ev["output"], ev.get("id"))
    return monitor.report()


__all__ = [
    "AgentPolicy",
    "Decision",
    "Finding",
    "OnUnknown",
    "RunMonitor",
    "RunReport",
    "UnrecognizedMessage",
    "ValueCheck",
    "_content_text",
    "check_run",
    "events",
]
