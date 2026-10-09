"""Online checks for agent runs: values an agent acts on should appear in, or follow from, what it saw.

Feed the monitor the run as it happens. Before each tool call executes, `before_call` looks for every
identifier, email, URL, date, and amount in the arguments among the user's messages, the system
prompt, and earlier tool results, applies source rules, and watches for repeated calls and budgets.
It returns a decision the caller can act on. `assistant` does the same for values in the agent's text.

What this catches is a value that appears nowhere in the context: a mistyped or guessed ID, an
invented email or zip code, a placeholder, an amount that is no price, total, or stated multiple. It
cannot tell which of two real values was the right one; that takes the system's own validation or a
model that reads intent.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from figured.extract import extract_numbers, scan_values
from figured.policy import Policy

from .store import Hit, SourceStore
from .values import Kind, classify, extract_text_values, numeric_string, parse_dates

Severity = Literal["warn", "block"]
_ERROR = re.compile(
    r'^\s*(?:error|exception|traceback)\b|"error"\s*:(?!\s*(?:null\b|false\b|""|\[\]|\{\}|0\b))',
    re.IGNORECASE,
)
_AS_OF = re.compile(
    r"(?:current (?:date|time)|today(?:'s date)?|the date)\s*(?:is|:)?\s*[,:]?\s*"
    r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day,?\s*)?(.{6,30})",
    re.IGNORECASE,
)
_BIRTH = re.compile(r"birth|dob\b|\.dob|born", re.IGNORECASE)
_ID_NAME = re.compile(
    r"(?:^|[._\-\[])(?:id|ids|item|items|sku|zip|postal|code|account|number|no)\]?$|id\]?$", re.IGNORECASE
)
_MAX_DEPTH = 64
_TOO_DEEP = object()
_DIGIT_RUN = re.compile(r"(?<![\w.,$])\d{6,}(?![\w.,])")


@dataclass(frozen=True)
class AgentPolicy:
    """What must have a source, what to tolerate, and what to do about it.

    kinds: value kinds that need a source.
    small_ints: integers with absolute value at or below this are counts, not claims, and are skipped.
    constants: values that never need a source (country codes, currencies, fixed enums).
    ignore: "tool.path" patterns to skip, such as "think.*" for a scratchpad tool.
    block_unsourced: "tool.path" patterns where an unsourced value blocks the call regardless of
        on_unsourced, typically arguments of tools with side effects ("refund.*", "transfer.*").
    source_rules: "tool.path" pattern -> allowed source kinds ("user", "system", "tool",
        "tool:<name>", "derived"). A value found only in a disallowed source is a violation; this is
        how a recipient that arrived in a tool result instead of from the user gets caught.
    free_text: for long free-text arguments (a memo, a message body), "extract" checks the
        identifiers, emails, URLs, and dates inside them; "skip" ignores them.
    pure_tools: tools whose output is computed only from their arguments (a calculator, a unit
        converter). If such a call used values not in context, nothing in its output counts as a
        source. Other tools' results are real data, so only the unsourced values they echo back are
        discounted.
    check_text: also check values the agent states in its own messages.
    max_repeats: an identical call (same tool, same arguments) this many times is flagged.
    max_tool_calls: budget for the run; None for no budget.
    on_unsourced / on_rule / on_repeat / on_budget: "warn" or "block".
    as_of: date for resolving "tomorrow", weekdays, and dates written without a year; "auto" reads it
        from any system message ("Today is 2026-10-09", "The current date is October 9, 2026").
    date_shift_days: the largest date move a user can ask for ("two weeks later") that still counts
        as derived. 0 turns shifts off.
    rel_tolerance: for numbers in the agent's text, as in figured.trace. Argument amounts are exact.
    """

    kinds: frozenset[str] = frozenset({"identifier", "email", "url", "date", "number", "phrase"})
    small_ints: float = 10
    constants: frozenset[str] = frozenset()
    ignore: tuple[str, ...] = ()
    block_unsourced: tuple[str, ...] = ()
    source_rules: tuple[tuple[str, frozenset[str]], ...] = ()
    free_text: Literal["extract", "skip"] = "extract"
    pure_tools: tuple[str, ...] = ()
    check_text: bool = True
    max_repeats: int = 3
    max_tool_calls: int | None = None
    on_unsourced: Severity = "warn"
    on_rule: Severity = "block"
    on_repeat: Severity = "warn"
    on_budget: Severity = "block"
    as_of: dt.date | Literal["auto"] | None = "auto"
    date_shift_days: int = 31
    rel_tolerance: float = 0.015

    @classmethod
    def build(cls, source_rules: dict[str, set[str] | list[str]] | None = None, **kw: Any) -> AgentPolicy:
        rules = tuple((k, frozenset(v)) for k, v in (source_rules or {}).items())
        for name in ("kinds", "constants"):
            if name in kw:
                kw[name] = frozenset(x.lower() if name == "constants" else x for x in kw[name])
        for name in ("ignore", "block_unsourced", "pure_tools"):
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

    def pure(self, tool: str) -> bool:
        return any(fnmatch.fnmatchcase(tool, p) for p in self.pure_tools)


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
    type: Literal["unsourced", "source_rule", "unchecked", "repeated_call", "retried_error", "over_budget"]
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
        summary = f"{traced}/{len(self.checks)} values found in context · {len(self.findings)} findings"
        lines = [f"{head} · {self.tool_calls} tool calls · {summary}"]
        for c in self.checks:
            mark = "✓" if c.status == "sourced" else "✗"
            src = f"{c.source} ({c.how})" if c.source else "not in context"
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
    """Incremental, in-process checks for one agent run."""

    def __init__(
        self,
        policy: AgentPolicy | None = None,
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        self.policy = policy or AgentPolicy()
        as_of = self.policy.as_of if isinstance(self.policy.as_of, dt.date) else None
        self.store = SourceStore(
            Policy(rel_tolerance=self.policy.rel_tolerance, max_rows=40, max_cells=60),
            as_of,
            self.policy.date_shift_days,
        )
        self.report_ = RunReport()
        self.step = 0
        self._calls: Counter[str] = Counter()
        self._errored: set[str] = set()
        self._pending: dict[str, tuple[str, list[str]]] = {}
        self._last_by_tool: dict[str, tuple[str, list[str]]] = {}
        self._hints = _schema_hints(tools or [])
        if system:
            self.system(system)

    def system(self, text: str) -> None:
        if self.policy.as_of == "auto" and self.store.as_of is None:
            self.store.as_of = _read_as_of(text)
        self.store.add("system", "system", self._next(), text)

    def user(self, text: str) -> None:
        self.store.add("user", "user", self._next(), text)

    def tool_result(self, name: str, output: Any, call_id: str | None = None) -> None:
        """Record a tool result. If the call that produced it used values with no source, the result
        is tainted: an error that echoes a made-up ID, or a calculator fed made-up operands, does not
        vouch for those values later."""
        text = output if isinstance(output, str) else json.dumps(output, default=str)
        step = self._next()
        call = self._pending.pop(call_id, None) if call_id else None
        if call is None:
            call = self._last_by_tool.get(name)
        key, unsourced = call if call is not None else (None, [])
        tainted = bool(unsourced) and self.policy.pure(name)
        self.store.add("tool", f"tool:{name}", step, text, tainted=tainted, echoes=set(unsourced))
        if key and _ERROR.search(text[:200]):
            self._errored.add(key)

    def before_call(self, name: str, args: Any, call_id: str | None = None) -> Decision:
        step = self._next()
        pol = self.policy
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, RecursionError):
                args = {"_raw": args}
        checks: list[ValueCheck] = []
        findings: list[Finding] = []
        silent: list[str] = []
        pure = pol.pure(name)
        for path, value in _leaves(args):
            where = f"{name}.{path}" if path else name
            if pol.ignored(where):
                continue
            if value is _TOO_DEEP:
                message = f"{where} is nested more than {_MAX_DEPTH} levels deep and was not checked"
                findings.append(Finding("unchecked", pol.on_unsourced, step, message, where))
                continue
            if pure:
                silent.extend(self._pure_unsourced(value))
                continue
            hint = self._hints.get(where)
            if hint and any(k in hint and hint[k] == value for k in ("default", "const")):
                continue
            kind = classify(value, hint)
            if kind is None:
                if isinstance(value, str) and pol.free_text == "extract" and _is_free_text(value):
                    found = extract_text_values(value)
                    for tv in found:
                        if tv.kind in pol.kinds and not self._skip(tv.kind, tv.literal):
                            checks.append(self._trace(step, where, tv.kind, tv.literal, findings, prose=True))
                    if "identifier" in pol.kinds:
                        taken = [(tv.start, tv.end) for tv in found]
                        for dm in _DIGIT_RUN.finditer(value):
                            if all(dm.end() <= a or dm.start() >= b for a, b in taken):
                                checks.append(
                                    self._trace(step, where, "identifier", dm.group(0), findings, prose=True)
                                )
                continue
            if kind not in pol.kinds:
                continue
            if kind == "number" and isinstance(value, str):
                value = numeric_string(value)
            if kind == "number" and isinstance(value, int) and abs(value) >= 10_000 and _ID_NAME.search(path):
                kind, value = "identifier", str(value)
            if self._skip(kind, value):
                continue
            checks.append(self._trace(step, where, kind, value, findings))
        try:
            key = f"{name}({json.dumps(args, sort_keys=True, default=str)})"
        except (ValueError, RecursionError):
            key = f"{name}({id(args)})"
        self._calls[key] += 1
        self.report_.tool_calls += 1
        unsourced = [_num_or_text(c.value) for c in checks if c.status != "sourced"] + silent
        self._last_by_tool[name] = (key, unsourced)
        if call_id:
            self._pending[call_id] = (key, unsourced)
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
            message = f"tool call {self.report_.tool_calls} exceeds the budget of {pol.max_tool_calls}"
            findings.append(Finding("over_budget", pol.on_budget, step, message, name))
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
            seen: list[tuple[float, bool]] = []
            for fig in extract_numbers(text):
                if any(not (fig.end <= s or fig.start >= e) for s, e in taken):
                    continue
                if fig.looks_like_year or fig.is_percent:
                    continue
                if not fig.is_currency and abs(fig.value) <= 100:
                    seen.append((fig.value, self._small_is_sourced(fig.value)))
                    continue
                total = _running_total(seen, fig.value, self.policy.rel_tolerance)
                if total is not None:
                    self.store.add("derived", "derived", step, "", numbers=[fig.value])
                    checks.append(
                        ValueCheck(
                            step, "text", fig.literal, "number", "sourced", "text", "derived:sum", total
                        )
                    )
                    seen.append((fig.value, True))
                else:
                    check = self._trace(step, "text", "number", fig.value, findings, literal=fig.literal)
                    checks.append(check)
                    seen.append((fig.value, check.status == "sourced"))
        self.report_.checks.extend(checks)
        self.report_.findings.extend(findings)
        return checks

    def report(self) -> RunReport:
        return self.report_

    def _pure_unsourced(self, value: Any) -> list[str]:
        """Numbers fed to a pure tool that are not in context. Not reported (a calculator may be given
        anything); used only so its output does not vouch for a result built from them."""
        if isinstance(value, bool) or value is None:
            return []
        nums = [float(value)] if isinstance(value, int | float) else scan_values(str(value))
        out = []
        for v in nums:
            if v.is_integer() and abs(v) <= self.policy.small_ints:
                continue
            if self.store.find("number", v, strict=True) is None and self.store.find("number", v) is None:
                out.append(_num_or_text(v))
        return out

    def _small_is_sourced(self, v: float) -> bool:
        return self.store.has_count(v) or self.store.find("number", v, strict=True) is not None

    def _trace(
        self,
        step: int,
        where: str,
        kind: Kind,
        value: Any,
        findings: list[Finding],
        literal: str | None = None,
        prose: bool = False,
    ) -> ValueCheck:
        shown = literal if literal is not None else value
        strict = where != "text" and not prose
        shift_ok = not _BIRTH.search(where)
        rule = self.policy.rule_for(where) if where != "text" else None
        hit = self.store.find(kind, value, set(rule) if rule else None, strict=strict, shift_ok=shift_ok)
        if hit is not None:
            if kind == "number" and hit.how.startswith("derived") and not strict:
                self.store.add("derived", "derived", step, "", numbers=[float(value)])
            return _check(step, where, shown, kind, "sourced", hit)
        if rule:
            anywhere = self.store.find(kind, value, strict=strict, shift_ok=shift_ok)
            if anywhere is not None:
                message = (
                    f"{where}={shown!s} came from {anywhere.source.label}, "
                    f"but must come from {', '.join(sorted(rule))}"
                )
                findings.append(Finding("source_rule", self.policy.on_rule, step, message, where))
                return _check(step, where, shown, kind, "violation", anywhere)
            message = f"{where}={shown!s} is not in context, and must come from {', '.join(sorted(rule))}"
            findings.append(Finding("source_rule", self.policy.on_rule, step, message, where))
        else:
            severity: Severity = (
                "block"
                if where != "text" and self.policy.blocks_unsourced(where)
                else self.policy.on_unsourced
            )
            message = f"{where}={shown!s} is not in context"
            echo = self.store.tainted_mention(kind, value)
            if echo is not None and echo.kind == "system":
                message += f" (it appears only as an example in {echo.ref()})"
            elif echo is not None:
                message += (
                    f" (it appears only in {echo.ref()}, the output of a call that used it without a source)"
                )
            findings.append(Finding("unsourced", severity, step, message, where))
        return ValueCheck(step, where, shown, kind, "unsourced")

    def _skip(self, kind: Kind, value: Any) -> bool:
        if kind == "number":
            if value is None:
                return True
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


def _running_total(seen: list[tuple[float, bool]], value: float, rel: float) -> str | None:
    """`value` is the sum of the last k (two or more) numbers stated earlier in the same message, or the
    product of two of the last four (a price times a count), and every operand was itself found in
    context. "$300 + $113 = $413" with made-up operands is not a total; it is three made-up numbers."""
    total = 0.0
    for k, (v, ok) in enumerate(reversed(seen), start=1):
        if not ok:
            break
        total += v
        if k >= 2 and total and abs(total - value) <= rel * abs(total):
            return f"sum of the {k} amounts listed before it in the same message"
    recent = seen[-4:]
    for i in range(len(recent)):
        for j in range(i + 1, len(recent)):
            (a, a_ok), (b, b_ok) = recent[i], recent[j]
            prod = a * b
            if a_ok and b_ok and prod and abs(prod - value) <= rel * abs(prod):
                return f"product of {a:g} and {b:g} stated earlier in the same message"
    return None


def _num_or_text(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _read_as_of(text: str) -> dt.date | None:
    for m in _AS_OF.finditer(text):
        for y, mo, d in sorted(parse_dates(m.group(1)), key=lambda k: k[0] is None):
            if y is not None:
                try:
                    return dt.date(y, mo, d)
                except ValueError:
                    continue
    return None


def _is_free_text(s: str) -> bool:
    return len(s) > 80 or len(s.split()) > 6


def _leaves(obj: Any) -> list[tuple[str, Any]]:
    """(path, value) for every leaf. Recursion stops at _MAX_DEPTH, far below Python's stack limit."""
    out: list[tuple[str, Any]] = []
    _walk(obj, "", 0, out)
    return out


def _walk(x: Any, path: str, depth: int, out: list[tuple[str, Any]]) -> None:
    if depth > _MAX_DEPTH:
        out.append((path, _TOO_DEEP))
    elif isinstance(x, dict):
        for k, v in x.items():
            _walk(v, f"{path}.{k}" if path else str(k), depth + 1, out)
    elif isinstance(x, list):
        for v in x:
            _walk(v, f"{path}[]", depth + 1, out)
    else:
        out.append((path, x))


def _schema_hints(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    hints: dict[str, dict[str, Any]] = {}
    for t in tools:
        fn = t.get("function", t)
        name = fn.get("name")
        params = fn.get("parameters") or fn.get("input_schema") or {}
        if not name:
            continue
        stack: list[tuple[Any, str, int]] = [(params, name, 0)]
        while stack:
            schema, path, depth = stack.pop()
            if not isinstance(schema, dict) or depth > _MAX_DEPTH:
                continue
            if schema.get("type") == "object" or "properties" in schema:
                for k, sub in (schema.get("properties") or {}).items():
                    stack.append((sub, f"{path}.{k}", depth + 1))
            elif schema.get("type") == "array" and isinstance(schema.get("items"), dict):
                stack.append((schema["items"], f"{path}[]", depth + 1))
            else:
                hints[path] = schema
    return hints
