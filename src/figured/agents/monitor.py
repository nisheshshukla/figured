"""Online provenance checks for agent runs: every value an agent acts on or states has a source.

Feed the monitor the run as it happens. Before each tool call executes, `before_call` traces every
identifier, email, URL, date, and number in the arguments to the user, the system prompt, or an
earlier tool result, applies source rules, and watches for repeated calls and budgets. It returns a
decision the caller can act on. `assistant` does the same for claims in the agent's text.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from figured.extract import extract_numbers
from figured.policy import Policy

from .store import Hit, SourceStore
from .values import Kind, classify, extract_text_values

Severity = Literal["warn", "block"]
_ERROR = re.compile(r"^\s*(?:error|exception|traceback)\b|\"error\"\s*:", re.IGNORECASE)
_AS_OF = re.compile(
    r"(?:current (?:date|time)|today(?:'s date)?|the date)\s*(?:is|:)?\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE
)


@dataclass(frozen=True)
class AgentPolicy:
    """What must be traced, what to tolerate, and what to do about it.

    kinds: value kinds that need a source.
    small_ints: integers with absolute value at or below this are counts, not claims, and are skipped.
    constants: values that never need a source (country codes, currencies, fixed enums).
    ignore: "tool.path" patterns to skip, such as "think.*" for a scratchpad tool.
    block_unsourced: "tool.path" patterns where an unsourced value blocks the call regardless of
        on_unsourced, typically arguments of tools with side effects ("refund.*", "transfer.*").
    source_rules: "tool.path" pattern -> allowed source kinds ("user", "system", "tool",
        "tool:<name>", "derived"). A value found only in a disallowed source is a violation; this is
        how a recipient that arrived in a tool result instead of from the user gets caught.
    check_text: also trace values the agent states in its own messages.
    max_repeats: an identical call (same tool, same arguments) this many times is flagged.
    max_tool_calls: budget for the run; None for no budget.
    on_unsourced / on_rule / on_repeat / on_budget: "warn" or "block".
    as_of: date for resolving "tomorrow" and weekdays; "auto" reads it from the system prompt.
    date_shift_days: when the user asks to move a date ("a day later"), a date within this many
        days of a sourced date counts as derived. 0 turns it off.
    rel_tolerance: for numbers, as in figured.trace.
    """

    kinds: frozenset[str] = frozenset({"identifier", "email", "url", "date", "number", "phrase"})
    small_ints: float = 10
    constants: frozenset[str] = frozenset()
    ignore: tuple[str, ...] = ()
    block_unsourced: tuple[str, ...] = ()
    source_rules: tuple[tuple[str, frozenset[str]], ...] = ()
    check_text: bool = True
    max_repeats: int = 3
    max_tool_calls: int | None = None
    on_unsourced: Severity = "warn"
    on_rule: Severity = "block"
    on_repeat: Severity = "warn"
    on_budget: Severity = "block"
    as_of: dt.date | Literal["auto"] | None = "auto"
    date_shift_days: int = 7
    rel_tolerance: float = 0.015

    @classmethod
    def build(cls, source_rules: dict[str, set[str] | list[str]] | None = None, **kw: Any) -> AgentPolicy:
        rules = tuple((k, frozenset(v)) for k, v in (source_rules or {}).items())
        for name in ("kinds", "constants"):
            if name in kw:
                kw[name] = frozenset(x.lower() if name == "constants" else x for x in kw[name])
        for name in ("ignore", "block_unsourced"):
            if name in kw:
                kw[name] = tuple(kw[name])
        return cls(source_rules=rules, **kw)

    def rule_for(self, where: str) -> frozenset[str] | None:
        for pattern, kinds in self.source_rules:
            if fnmatch.fnmatchcase(where, pattern):
                return kinds
        return None

    def blocks_unsourced(self, where: str) -> bool:
        return any(fnmatch.fnmatchcase(where, p) for p in self.block_unsourced)

    def ignored(self, where: str) -> bool:
        return any(fnmatch.fnmatchcase(where, p) for p in self.ignore)


@dataclass(frozen=True)
class ValueCheck:
    step: int
    where: str
    value: Any
    kind: Kind
    status: Literal["sourced", "unsourced", "violation"]
    source: str | None = None
    how: str = ""
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {
            "step": self.step,
            "where": self.where,
            "value": self.value,
            "kind": self.kind,
            "status": self.status,
        }
        if self.source:
            d["source"] = self.source
        if self.how:
            d["how"] = self.how
        if self.explanation:
            d["explanation"] = self.explanation
        return d


@dataclass(frozen=True)
class Finding:
    type: Literal["unsourced", "source_rule", "repeated_call", "retried_error", "over_budget"]
    severity: Severity
    step: int
    message: str
    where: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "severity": self.severity,
            "step": self.step,
            "where": self.where,
            "message": self.message,
        }


@dataclass(frozen=True)
class Decision:
    """What the monitor says about a tool call before it runs."""

    action: Literal["allow", "warn", "block"]
    findings: tuple[Finding, ...]
    checks: tuple[ValueCheck, ...]

    @property
    def allowed(self) -> bool:
        return self.action != "block"

    def reason(self) -> str:
        return "; ".join(f.message for f in self.findings)


@dataclass
class RunReport:
    checks: list[ValueCheck] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    tool_calls: int = 0

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def unsourced(self) -> list[ValueCheck]:
        return [c for c in self.checks if c.status != "sourced"]

    @property
    def blocked(self) -> bool:
        return any(f.severity == "block" for f in self.findings)

    def explain(self) -> str:
        head = "OK" if self.ok else ("BLOCK" if self.blocked else "WARN")
        traced = sum(1 for c in self.checks if c.status == "sourced")
        summary = f"{traced}/{len(self.checks)} values traced · {len(self.findings)} findings"
        lines = [f"{head} · {self.tool_calls} tool calls · {summary}"]
        for c in self.checks:
            mark = "✓" if c.status == "sourced" else "✗"
            src = f"{c.source} ({c.how})" if c.source else "no source"
            lines.append(f"  {mark} [{c.step}] {c.where:<34} {str(c.value)[:28]:<28} {src}")
        for f in self.findings:
            lines.append(f"  ! [{f.step}] {f.type}: {f.message}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "blocked": self.blocked,
            "tool_calls": self.tool_calls,
            "checks": [c.to_dict() for c in self.checks],
            "findings": [f.to_dict() for f in self.findings],
        }


class RunMonitor:
    """Incremental, in-process provenance and loop checks for one agent run."""

    def __init__(
        self,
        policy: AgentPolicy | None = None,
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        self.policy = policy or AgentPolicy()
        as_of = self.policy.as_of
        if as_of == "auto":
            m = _AS_OF.search(system or "")
            as_of = dt.date.fromisoformat(m.group(1)) if m else None
        self.store = SourceStore(
            Policy(rel_tolerance=self.policy.rel_tolerance, max_rows=40, max_cells=60),
            as_of,
            self.policy.date_shift_days,
        )
        self.report_ = RunReport()
        self.step = 0
        self._calls: Counter[str] = Counter()
        self._errored: set[str] = set()
        self._pending: dict[str, str] = {}
        self._hints = _schema_hints(tools or [])
        if system:
            self.system(system)

    def system(self, text: str) -> None:
        self.store.add("system", "system", self._next(), text)

    def user(self, text: str) -> None:
        self.store.add("user", "user", self._next(), text)

    def tool_result(self, name: str, output: Any, call_id: str | None = None) -> None:
        text = output if isinstance(output, str) else json.dumps(output, default=str)
        step = self._next()
        self.store.add("tool", f"tool:{name}", step, text)
        key = self._pending.pop(call_id, None) if call_id else None
        if key is None:
            key = next((k for k in reversed(list(self._calls)) if k.startswith(name + "(")), None)
        if key and _ERROR.search(text[:200]):
            self._errored.add(key)

    def before_call(self, name: str, args: Any, call_id: str | None = None) -> Decision:
        step = self._next()
        pol = self.policy
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"_raw": args}
        checks: list[ValueCheck] = []
        findings: list[Finding] = []
        for path, value in _leaves(args):
            where = f"{name}.{path}" if path else name
            if pol.ignored(where):
                continue
            kind = classify(value, self._hints.get(where))
            if kind is None or kind not in pol.kinds or self._skip(kind, value):
                continue
            checks.append(self._trace(step, where, kind, value, findings))
        key = f"{name}({json.dumps(args, sort_keys=True, default=str)})"
        self._calls[key] += 1
        self.report_.tool_calls += 1
        if call_id:
            self._pending[call_id] = key
        n = self._calls[key]
        if n >= pol.max_repeats:
            findings.append(
                Finding(
                    "repeated_call",
                    pol.on_repeat,
                    step,
                    f"{name} called {n} times with identical arguments",
                    name,
                )
            )
        if key in self._errored:
            findings.append(
                Finding(
                    "retried_error",
                    pol.on_repeat,
                    step,
                    f"{name} retried with the same arguments after an error",
                    name,
                )
            )
        if pol.max_tool_calls is not None and self.report_.tool_calls > pol.max_tool_calls:
            findings.append(
                Finding(
                    "over_budget",
                    pol.on_budget,
                    step,
                    f"tool call {self.report_.tool_calls} exceeds the budget of {pol.max_tool_calls}",
                    name,
                )
            )
        self.report_.checks.extend(checks)
        self.report_.findings.extend(findings)
        action: Literal["allow", "warn", "block"] = "allow"
        if findings:
            action = "block" if any(f.severity == "block" for f in findings) else "warn"
        return Decision(action, tuple(findings), tuple(checks))

    def assistant(self, text: str) -> list[ValueCheck]:
        step = self._next()
        if not self.policy.check_text or not text:
            return []
        checks: list[ValueCheck] = []
        findings: list[Finding] = []
        values = extract_text_values(text)
        taken = [(v.start, v.end) for v in values]
        for v in values:
            if v.kind in self.policy.kinds and not self._skip(v.kind, v.value):
                checks.append(self._trace(step, "text", v.kind, v.literal, findings))
        if "number" in self.policy.kinds:
            seen: list[float] = []
            for fig in extract_numbers(text):
                if any(not (fig.end <= s or fig.start >= e) for s, e in taken):
                    continue
                if fig.looks_like_year or fig.is_percent:
                    continue
                if not fig.is_currency and abs(fig.value) <= 100:
                    seen.append(fig.value)
                    continue
                total = _running_total(seen, fig.value, self.policy.rel_tolerance)
                if total is not None:
                    self.store.add("derived", "derived", step, "", numbers=[fig.value])
                    checks.append(
                        ValueCheck(
                            step, "text", fig.literal, "number", "sourced", "text", "derived:sum", total
                        )
                    )
                else:
                    checks.append(
                        self._trace(step, "text", "number", fig.value, findings, literal=fig.literal)
                    )
                seen.append(fig.value)
        self.report_.checks.extend(checks)
        self.report_.findings.extend(findings)
        return checks

    def report(self) -> RunReport:
        return self.report_

    def _trace(
        self,
        step: int,
        where: str,
        kind: Kind,
        value: Any,
        findings: list[Finding],
        literal: str | None = None,
    ) -> ValueCheck:
        shown = literal if literal is not None else value
        rule = self.policy.rule_for(where) if where != "text" else None
        hit = self.store.find(kind, value, set(rule) if rule else None, strict=where != "text")
        if hit is not None:
            if kind == "number" and hit.how.startswith("derived"):
                self.store.add("derived", "derived", step, "", numbers=[float(value)])
            return _check(step, where, shown, kind, "sourced", hit)
        if rule:
            anywhere = self.store.find(kind, value)
            if anywhere is not None:
                findings.append(
                    Finding(
                        "source_rule",
                        self.policy.on_rule,
                        step,
                        f"{where}={shown!s} came from {anywhere.source.label}, "
                        f"but must come from {', '.join(sorted(rule))}",
                        where,
                    )
                )
                return _check(step, where, shown, kind, "violation", anywhere)
        if rule:
            findings.append(
                Finding(
                    "source_rule",
                    self.policy.on_rule,
                    step,
                    f"{where}={shown!s} has no source, and must come from {', '.join(sorted(rule))}",
                    where,
                )
            )
        else:
            severity = (
                "block"
                if where != "text" and self.policy.blocks_unsourced(where)
                else self.policy.on_unsourced
            )
            findings.append(Finding("unsourced", severity, step, f"{where}={shown!s} has no source", where))
        return ValueCheck(step, where, shown, kind, "unsourced")

    def _skip(self, kind: Kind, value: Any) -> bool:
        if kind == "number":
            try:
                f = float(value)
            except (TypeError, ValueError):
                return True
            return f.is_integer() and abs(f) <= self.policy.small_ints
        return str(value).strip().lower() in self.policy.constants

    def _next(self) -> int:
        self.step += 1
        return self.step


def _check(
    step: int, where: str, value: Any, kind: Kind, status: Literal["sourced", "violation"], hit: Hit
) -> ValueCheck:
    return ValueCheck(step, where, value, kind, status, hit.source.ref(), hit.how, hit.explanation)


def _running_total(seen: list[float], value: float, rel: float) -> str | None:
    """`value` is the sum of the last k (two or more) numbers stated earlier in the same message, or the
    product of two of the last four (a price times a count)."""
    total = 0.0
    for k, v in enumerate(reversed(seen), start=1):
        total += v
        if k >= 2 and total and abs(total - value) <= rel * abs(total):
            return f"sum of the {k} amounts listed before it in the same message"
    recent = seen[-4:]
    for i in range(len(recent)):
        for j in range(i + 1, len(recent)):
            prod = recent[i] * recent[j]
            if prod and abs(prod - value) <= rel * abs(prod):
                return f"product of {recent[i]:g} and {recent[j]:g} stated earlier in the same message"
    return None


def _leaves(obj: Any, path: str = "") -> list[tuple[str, Any]]:
    if isinstance(obj, dict):
        out: list[tuple[str, Any]] = []
        for k, v in obj.items():
            out.extend(_leaves(v, f"{path}.{k}" if path else str(k)))
        return out
    if isinstance(obj, list):
        out = []
        for v in obj:
            out.extend(_leaves(v, f"{path}[]"))
        return out
    return [(path, obj)]


def _schema_hints(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    hints: dict[str, dict[str, Any]] = {}
    for t in tools:
        fn = t.get("function", t)
        name = fn.get("name")
        params = fn.get("parameters") or fn.get("input_schema") or {}
        if name:
            _walk_schema(params, name, hints)
    return hints


def _walk_schema(schema: dict[str, Any], path: str, hints: dict[str, dict[str, Any]]) -> None:
    if not isinstance(schema, dict):
        return
    if schema.get("type") == "object" or "properties" in schema:
        for k, sub in (schema.get("properties") or {}).items():
            _walk_schema(sub, f"{path}.{k}", hints)
    elif schema.get("type") == "array" and isinstance(schema.get("items"), dict):
        _walk_schema(schema["items"], f"{path}[]", hints)
    else:
        hints[path] = schema
