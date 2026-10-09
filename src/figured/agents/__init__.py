"""Provenance checks for agent runs.

Every identifier, email, URL, date, and number an agent passes to a tool, or states in its answer,
should come from somewhere: the user, the system prompt, an earlier tool result, or arithmetic over
those. `RunMonitor` checks each tool call before it executes (an online guardrail); `check_run`
replays a finished transcript (an offline or trace-level eval).
"""

from __future__ import annotations

from typing import Any

from .formats import events
from .monitor import AgentPolicy, Decision, Finding, RunMonitor, RunReport, ValueCheck


def check_run(
    messages: list[dict[str, Any]],
    policy: AgentPolicy | None = None,
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> RunReport:
    """Replay a finished run (OpenAI or Anthropic message format) through a RunMonitor."""
    sys_text = system
    if sys_text is None:
        first = messages[0] if messages else {}
        if first.get("role") == "system" and isinstance(first.get("content"), str):
            sys_text = first["content"]
    monitor = RunMonitor(policy, system=sys_text, tools=tools)
    skip_first_system = sys_text is not None and system is None
    for i, (kind, ev) in enumerate(events(messages, None)):
        if kind == "system":
            if skip_first_system and i == 0:
                continue
            monitor.system(ev["text"])
        elif kind == "user":
            monitor.user(ev["text"])
        elif kind == "assistant":
            monitor.assistant(ev["text"])
        elif kind == "call":
            monitor.before_call(ev["name"], ev["args"], ev.get("id"))
        elif kind == "result":
            monitor.tool_result(ev["name"], ev["output"], ev.get("id"))
    return monitor.report()


__all__ = [
    "AgentPolicy",
    "Decision",
    "Finding",
    "RunMonitor",
    "RunReport",
    "ValueCheck",
    "check_run",
    "events",
]
