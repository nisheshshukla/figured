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

from .store import MAX_INDEX_CHARS, Hit, SourceStore
from .values import Kind, classify, extract_text_values, numeric_string, parse_dates

Severity = Literal["warn", "confirm", "block"]
CallKey = tuple[str, tuple[tuple[str, str], ...]]
Action = Literal["allow", "warn", "confirm", "block"]
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
_BOUND = re.compile(
    r"^(?:start|end|from|to|since|until|after|before|begin)(?:_?(?:date|time|at|day|ts|timestamp))?$"
    r"|^(?:date|time|created|updated)_?(?:from|to|start|end|after|before|since|until|min|max|gte|lte|gt|lt)$"
    r"|^(?:min|max)(?:_?\w+)?$|^\w+_(?:min|max|gte|lte)$"
    r"|^(?:limit|offset|page|page_size|per_page|top_k|top_n|count|size|max_results|num_results|n)$",
    re.IGNORECASE,
)
_TOO_DEEP = object()
_DIGIT_RUN = re.compile(r"(?<![\w.,$])\d{6,}(?![\w.,])")
_AFFIRM = re.compile(
    r"\b(?:yes|yeah|yep|sure|ok(?:ay)?|alright|confirm(?:ed)?|go ahead|proceed|sounds good|"
    r"that'?s (?:right|correct|fine)|that is (?:right|correct|fine)|please do|do it|absolutely|"
    r"let'?s (?:go|do|proceed))\b",
    re.IGNORECASE,
)
_NEGATE = re.compile(r"^\W*(?:no|nope|wait|stop|don'?t|do not|cancel)\b", re.IGNORECASE)
_NAMED = re.compile(
    r"[\"'`\u201c\u2018]([^\"'`\u201d\u2019\n]{2,120})[\"'`\u201d\u2019]"
    r"|(?<![\w@./-])((?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/[^\s\"'<>]*)?)"
    r"|(?<![\w./-])([\w-]+(?:/[\w.-]+)*\.[a-z][a-z0-9]{0,4})\b"
    r"|([\w.+-]+@[\w-]+(?:\.[\w-]+)+)",
    re.IGNORECASE,
)
_DESC_CUE = re.compile(r"such as|e\.g\.|for example|for instance|example|like|format", re.IGNORECASE)
_QUOTED = re.compile(r"['\"`]([^'\"`\s]{3,40})['\"`]")
_ONE_DIGIT_RUN = re.compile(r"^(\D*?)(\d{4,})(\D*)$")
_PATTERN_SHAPE = re.compile(r"^\^?([^\\\[\](){}.*+?|^$]*)\\d\{(\d+)\}([^\\\[\](){}.*+?|^$]*)\$?$")


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
    schema_formats: read ID formats from tool schemas: a `pattern` such as `^#W\\d{7}$`, or examples
        in a parameter's description ("the order id, such as '#W0000000'"). An agent that adds the
        prefix to digits the user typed is then not flagged. Only literal prefixes and suffixes are
        added; the digits must still be in context, as a whole token, and the count must match.
    named_sources: "confirm" turns a source-rule violation into a confirmation when the value came
        from a resource the user named (a file in quotes, a URL, an address): "pay the bill in
        'bill.txt'" legitimately takes the recipient from the file, and an injection in that file
        looks the same, so a person decides. Use it only where a person reads the confirmation; on
        AgentDojo it halves blocked benign runs and sends 38% of successful injections to that person
        instead of blocking them. "rule" (the default) keeps on_rule.
    confirm_before: tool patterns whose calls need the user's yes to the values: the user's last
        message must confirm the agent's last message, and every value checked in the call must
        appear in it. tau-bench's and many support policies require this before any change.
    ambiguous_before: tool patterns where an identifier the user neither typed nor confirmed, with
        other values of the same shape in context (three order IDs, two payment methods), is flagged
        as an ambiguous selection: a choice a person or a verifier should check.
    requires: tool pattern -> tool patterns at least one of which must have run earlier in the run
        ("cancel_reservation" requires "get_reservation_details").
    on_selection / on_requires: severity for the three checks above.
    on_unknown_tool: severity for a call to a tool that is not among the `tools` the monitor was given.
    read_tools: tool patterns that only read ("get_*", "search_*"...), matched against the name, the
        last segment of a server-prefixed name, and its snake_case form. Annotations a tool server
        supplies (readOnlyHint) are not trusted: a server can call anything read-only.
    search_bounds: "skip" (the default) does not check the bounds and paging of a read call (a
        start_date, an end, a min_amount, a limit): agents choose search windows and page sizes
        themselves, and a made-up bound reads nothing it should not. Identifiers in read calls are
        still checked, since a guessed user ID or zip is a real error. "check" checks them too.
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
    schema_formats: bool = True
    named_sources: Literal["confirm", "rule"] = "rule"
    confirm_before: tuple[str, ...] = ()
    ambiguous_before: tuple[str, ...] = ()
    requires: tuple[tuple[str, tuple[str, ...]], ...] = ()
    on_selection: Severity = "confirm"
    on_requires: Severity = "warn"
    on_unknown_tool: Severity = "block"
    read_tools: tuple[str, ...] = (
        "get_*", "find_*", "list_*", "search_*", "check_*", "read_*", "lookup_*", "fetch_*",
        "query_*", "view_*", "show_*", "describe_*", "count_*",
    )  # fmt: skip
    search_bounds: Literal["skip", "check"] = "skip"

    @classmethod
    def build(cls, source_rules: dict[str, set[str] | list[str]] | None = None, **kw: Any) -> AgentPolicy:
        rules = tuple((k, frozenset(v)) for k, v in (source_rules or {}).items())
        for name in ("kinds", "constants"):
            if name in kw:
                kw[name] = frozenset(x.lower() if name == "constants" else x for x in kw[name])
        for name in (
            "ignore",
            "block_unsourced",
            "pure_tools",
            "confirm_before",
            "ambiguous_before",
            "read_tools",
        ):
            if name in kw:
                kw[name] = tuple(kw[name])
        if isinstance(kw.get("requires"), dict):
            kw["requires"] = tuple((k, tuple(v)) for k, v in kw["requires"].items())
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

    def reads(self, tool: str) -> bool:
        """Also matches the last segment of a server-prefixed MCP name (pubmed-mcp-server-search_x)
        and camelCase (getOrder -> get_order)."""
        last = tool.rsplit("-", 1)[-1] if "-" in tool and "_" in tool.rsplit("-", 1)[-1] else tool
        snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", last).lower()
        return any(fnmatch.fnmatchcase(t, p) for t in {tool, last, snake} for p in self.read_tools)

    def needs_confirmation(self, tool: str) -> bool:
        return any(fnmatch.fnmatchcase(tool, p) for p in self.confirm_before)

    def checks_ambiguity(self, tool: str) -> bool:
        return any(fnmatch.fnmatchcase(tool, p) for p in self.ambiguous_before)

    def prerequisites(self, tool: str) -> tuple[str, ...]:
        return tuple(r for pattern, reqs in self.requires if fnmatch.fnmatchcase(tool, pattern) for r in reqs)


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
class Finding:
    type: Literal[
        "unsourced",
        "source_rule",
        "unchecked",
        "unconfirmed",
        "ambiguous_selection",
        "missing_prerequisite",
        "unknown_tool",
        "repeated_call",
        "retried_error",
        "over_budget",
    ]
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


@dataclass(frozen=True, slots=True)
class Decision:
    """What the monitor says about a tool call before it runs."""

    action: Action
    findings: tuple[Finding, ...]
    checks: tuple[ValueCheck, ...]

    @property
    def allowed(self) -> bool:
        """The call may run now: allowed or only warned about."""
        return self.action in ("allow", "warn")

    @property
    def needs_confirmation(self) -> bool:
        """The call may run once a person confirms it; `reason()` says what to show them."""
        return self.action == "confirm"

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

    @property
    def confirmations(self) -> int:
        return sum(1 for f in self.findings if f.severity == "confirm")

    def explain(self) -> str:
        head = "OK" if self.ok else ("BLOCK" if self.blocked else "CONFIRM" if self.confirmations else "WARN")
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
        self._calls: Counter[CallKey] = Counter()
        self._errored: set[CallKey] = set()
        self._pending: dict[str, tuple[CallKey | None, list[str], list[str], str]] = {}
        self._last_by_tool: dict[str, tuple[CallKey | None, list[str], list[str], str]] = {}
        self._hints = _schema_hints(tools or [], self.policy.schema_formats)
        tools = [t for t in tools or () if isinstance(t, dict)]
        self._tools = {
            str(t.get("function", t).get("name")) for t in tools or () if t.get("function", t).get("name")
        }
        self._named: dict[str, str] = {}
        self._reads_cache: dict[str, bool] = {}
        self._ignored_cache: dict[str, bool] = {}
        self._pure_cache: dict[str, bool] = {}
        self._tool_cache: dict[str, tuple[bool, bool, tuple[str, ...]]] = {}
        self._birth_cache: dict[str, bool] = {}
        self._called: list[str] = []
        self._window: list[str] = []
        self._confirmed: str | None = None
        if system:
            self.system(system)

    def system(self, text: str) -> None:
        text = _as_text(text)
        if self.policy.as_of == "auto" and self.store.as_of is None:
            self.store.as_of = _read_as_of(text)
        self.store.add("system", "system", self._next(), text)

    def user(self, text: str) -> None:
        text = _as_text(text)
        self.store.add("user", "user", self._next(), text)
        for m in _NAMED.finditer(text):
            token = next(g for g in m.groups() if g)
            self._named.setdefault(_resource(token), token.strip())
        affirmed = bool(_AFFIRM.search(text)) and not _NEGATE.search(text)
        self._confirmed = "\n".join(self._window) if affirmed and self._window else None

    def tool_result(self, name: str, output: Any, call_id: str | None = None) -> None:
        """Record a tool result. A result never vouches for the arguments of the call that produced it:
        a lookup keyed by X returning X says nothing about where X came from, so an error that echoes
        a made-up ID, or a contacts search that echoes an injected address, adds no source. If a pure
        tool was fed values with no source, its whole output is tainted."""
        if isinstance(output, str):
            text = output
        elif isinstance(output, bytes):
            text = output.decode("utf-8", errors="replace")
        else:
            try:
                text = json.dumps(output, default=str)
            except (TypeError, ValueError, RecursionError):
                text = "\n".join(f"{p}: {v}" for p, v in _leaves(output) if v is not _TOO_DEEP)[
                    : 2 * MAX_INDEX_CHARS
                ]
        step = self._next()
        call = self._pending.pop(call_id, None) if call_id else None
        if call is None:
            call = self._last_by_tool.get(name)
        key, unsourced, echoes, named = call if call is not None else (None, [], [], "")
        tainted = bool(unsourced) and self.policy.pure(name)
        self.store.add("tool", f"tool:{name}", step, text, tainted=tainted, echoes=set(echoes), named=named)
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
        pure = self._pure_cache.get(name)
        if pure is None:
            pure = self._pure_cache[name] = pol.pure(name)
        ignored_cache = self._ignored_cache
        bounds_free = pol.search_bounds == "skip" and self._reads(name)
        named = ""
        leaves = _leaves(args)
        for path, value in leaves:
            where = f"{name}.{path}" if path else name
            ignored = ignored_cache.get(where)
            if ignored is None:
                ignored = ignored_cache[where] = pol.ignored(where)
            if ignored:
                continue
            if value is _TOO_DEEP:
                message = f"{where} is nested more than {_MAX_DEPTH} levels deep and was not checked"
                findings.append(Finding("unchecked", pol.on_unsourced, step, message, where))
                continue
            if pure:
                silent.extend(self._pure_unsourced(value))
                continue
            if not named and isinstance(value, str) and self._named:
                named = self._named.get(_resource(value), "")
            hint = self._hints.get(where)
            if hint and any(k in hint and hint[k] == value for k in ("default", "const")):
                continue
            rule = pol.rule_for(where) if pol.source_rules else None
            ruled = rule is not None
            kind = classify(value, hint)
            if (
                kind is None
                and isinstance(value, str)
                and value.strip()
                and (ruled or _off_enum(value, hint))
            ):
                kind = "phrase" if " " in value.strip() else "identifier"
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
            if bounds_free and kind in ("date", "number") and _BOUND.match(_key(path)):
                continue
            if kind == "number" and isinstance(value, str):
                value = numeric_string(value)
            if kind == "number" and isinstance(value, int) and abs(value) >= 10_000 and _ID_NAME.search(path):
                kind, value = "identifier", str(value)
            if self._skip(kind, value):
                continue
            formats = hint.get("_formats") if hint else None
            period_ok = kind == "date" and bool(_BOUND.match(_key(path)))
            checks.append(
                self._trace(
                    step, where, kind, value, findings, formats=formats, period_ok=period_ok, rule=rule
                )
            )
        flags = self._tool_cache.get(name)
        if flags is None:
            flags = self._tool_cache[name] = (
                pol.needs_confirmation(name),
                pol.checks_ambiguity(name),
                pol.prerequisites(name),
            )
        confirm_tool, ambiguity_tool, prerequisites = flags
        if confirm_tool or ambiguity_tool:
            self._selection(step, name, checks, findings)
            if confirm_tool:
                self._window = []
        if self._tools and name not in self._tools:
            message = f"{name} is not one of the agent's tools"
            findings.append(Finding("unknown_tool", pol.on_unknown_tool, step, message, name))
        missing = [r for r in prerequisites if not any(fnmatch.fnmatchcase(c, r) for c in self._called)]
        if missing and len(missing) == len(prerequisites):
            message = f"{name} runs before {' or '.join(missing)}, which it requires"
            findings.append(Finding("missing_prerequisite", pol.on_requires, step, message, name))
        self._called.append(name)
        # the same call twice: same tool, same leaves, in a canonical order (a stable sort by path keeps
        # list items in sequence while making dict key order irrelevant)
        key = (name, tuple(sorted(((p, repr(v)) for p, v in leaves), key=_first)))
        self._calls[key] += 1
        self.report_.tool_calls += 1
        unsourced = [_num_or_text(c.value) for c in checks if c.status != "sourced"] + silent
        echoes = unsourced + [
            _num_or_text(v) for _, v in leaves if isinstance(v, str | int | float) and not isinstance(v, bool)
        ]
        self._last_by_tool[name] = (key, unsourced, echoes, named)
        if call_id:
            self._pending[call_id] = (key, unsourced, echoes, named)
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
        levels = {f.severity for f in findings}
        action: Action = (
            "block"
            if "block" in levels
            else "confirm"
            if "confirm" in levels
            else "warn"
            if levels
            else "allow"
        )
        return Decision(action, tuple(findings), tuple(checks))

    def assistant(self, text: str) -> list[ValueCheck]:
        text = _as_text(text)
        step = self._next()
        if text and text.strip():
            self._window.append(text)
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

    def _reads(self, name: str) -> bool:
        cached = self._reads_cache.get(name)
        if cached is None:
            cached = self._reads_cache[name] = self.policy.reads(name)
        return cached

    def _pure_unsourced(self, value: Any) -> list[str]:
        """Numbers fed to a pure tool that are not in context. Not reported (a calculator may be given
        anything); used only so its output does not vouch for a result built from them."""
        if isinstance(value, bool) or value is None:
            return []
        if isinstance(value, int | float):
            try:
                nums = [float(value)]
            except OverflowError:
                nums = []
        else:
            nums = scan_values(str(value))
        out = []
        for v in nums:
            if v.is_integer() and abs(v) <= self.policy.small_ints and self.store.has_count(v):
                continue
            if self.store.find("number", v, strict=True) is None and self.store.find("number", v) is None:
                out.append(_num_or_text(v))
        return out

    def _selection(self, step: int, name: str, checks: list[ValueCheck], findings: list[Finding]) -> None:
        """Confirmation binding and ambiguous selections for a side-effecting call. Neither says the
        value is wrong; each says a person or a verifier should look before the call runs. Values the
        user typed need neither. The confirmed summary is everything the agent said since the last
        confirmed action, when the user's latest message agrees to it."""
        pol = self.policy
        store = self.store
        confirmed = self._confirmed
        values = [
            c
            for c in checks
            if c.where != "text" and c.status == "sourced" and not (c.source or "").startswith("user@")
        ]
        alts = {str(c.value): store.alternatives(str(c.value)) for c in values if c.kind == "identifier"}

        def in_summary(c: ValueCheck) -> bool:
            if confirmed is None:
                return False
            if c.kind == "identifier":
                return store.bound(str(c.value), confirmed, alts[str(c.value)])
            return _in_text(confirmed, c.kind, c.value, store.as_of)

        if pol.needs_confirmation(name):
            if confirmed is None:
                message = f"{name} runs without the user confirming it in their last message"
                findings.append(Finding("unconfirmed", pol.on_selection, step, message, name))
            else:
                absent = [c for c in values if not in_summary(c)]
                if absent:
                    shown = ", ".join(f"{c.where}={c.value}" for c in absent[:4])
                    verb = "was" if len(absent) == 1 else "were"
                    message = f"{shown} {verb} not in the summary the user confirmed"
                    findings.append(Finding("unconfirmed", pol.on_selection, step, message, name))
        if pol.checks_ambiguity(name):
            said = "\n".join(src.raw for src in store.sources if src.kind == "user")
            for c in values:
                if c.kind != "identifier" or not alts[str(c.value)]:
                    continue
                if store.bound(str(c.value), said, alts[str(c.value)]) or in_summary(c):
                    continue
                others = alts[str(c.value)]
                listed = ", ".join(others[:3])
                plural = "s" if len(others) > 1 else ""
                message = (
                    f"{c.where}={c.value} was chosen from context; nothing the user said or confirmed "
                    f"singles it out from {len(others)} other value{plural} of the same shape ({listed})"
                )
                findings.append(Finding("ambiguous_selection", pol.on_selection, step, message, c.where))

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
        formats: list[tuple[str, int, str]] | None = None,
        period_ok: bool = False,
        rule: frozenset[str] | None = None,
    ) -> ValueCheck:
        shown = literal if literal is not None else value
        strict = where != "text" and not prose
        shift_ok = self._birth_cache.get(where)
        if shift_ok is None:
            shift_ok = self._birth_cache[where] = not _BIRTH.search(where)
        if rule is None and where != "text" and self.policy.source_rules:
            rule = self.policy.rule_for(where)
        allowed = set(rule) if rule else None
        shift_ok = shift_ok and rule is None
        hit = self.store.find(
            kind, value, allowed, strict=strict, shift_ok=shift_ok, formats=formats, period_ok=period_ok
        )
        if hit is not None:
            if kind == "number" and hit.how.startswith("derived") and not strict:
                self.store.add("derived", "derived", step, "", numbers=[float(value)])
            return _check(step, where, shown, kind, "sourced", hit)
        if rule:
            anywhere = self.store.find(
                kind, value, strict=strict, shift_ok=shift_ok, formats=formats, period_ok=period_ok
            )
            if anywhere is not None:
                level: Severity = self.policy.on_rule
                message = (
                    f"{where}={shown!s} came from {anywhere.source.label}, "
                    f"but must come from {', '.join(sorted(rule))}"
                )
                named = anywhere.source.named
                if named and self.policy.named_sources == "confirm" and level == "block":
                    level = "confirm"
                    message = (
                        f"{where}={shown!s} came from {anywhere.source.label} "
                        f"({named}, which the user named), not from {', '.join(sorted(rule))}: "
                        "confirm it before acting"
                    )
                    others = self.store.alternatives(str(value), anywhere.source)
                    if others:
                        message += f"; the same result also holds {', '.join(others[:3])}"
                findings.append(Finding("source_rule", level, step, message, where))
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
            except (TypeError, ValueError, OverflowError):
                return True
            return f.is_integer() and abs(f) <= self.policy.small_ints
        constants = self.policy.constants
        return bool(constants) and str(value).strip().lower() in constants

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


def _first(pair: tuple[str, str]) -> str:
    return pair[0]


def _as_text(x: Any) -> str:
    """Whatever a caller hands in as a message: bytes are decoded, other objects stringified."""
    if isinstance(x, str):
        return x
    if x is None:
        return ""
    if isinstance(x, bytes | bytearray):
        return bytes(x).decode("utf-8", errors="replace")
    return str(x)


def _key(path: str) -> str:
    """The last name in an argument path: "filters.start_date" -> "start_date"."""
    return path.rsplit(".", 1)[-1].replace("[]", "")


def _resource(token: str) -> str:
    """A file, URL, or address as the user named it, normalized for comparison with an argument."""
    t = token.strip().strip(".,;:!?").lower()
    if "://" in t or t.startswith("www."):
        t = re.sub(r"^[a-z]+://(?:www\.)?|^www\.", "", t)
    return t.rstrip("/")


def _in_text(text: str, kind: Kind, value: Any, as_of: dt.date | None) -> bool:
    """`value` appears in an agent's message the user confirmed."""
    low = text.lower()
    if kind == "number":
        try:
            v = float(value)
        except OverflowError:
            return False
        return any(abs(v - x) <= 0.005 for x in scan_values(text))
    if kind == "date":
        want = parse_dates(str(value))
        have = parse_dates(text, as_of)
        return any((y, m, d) in have or (None, m, d) in have for y, m, d in want)
    needle = str(value).lower()
    squeeze = re.sub(r"[\s\-]", "", low)
    return any(n and (n in low or n.replace("-", "") in squeeze) for n in (needle, needle.lstrip("#")))


def _off_enum(value: str, hint: dict[str, Any] | None) -> bool:
    """A string argument whose schema lists an enum, with a value that is not one of them."""
    if not hint or "enum" not in hint:
        return False
    options = {str(e).lower() for e in hint["enum"]} if isinstance(hint["enum"], list) else set()
    return value.strip().lower() not in options


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


def _schema_hints(tools: list[dict[str, Any]], formats: bool = True) -> dict[str, dict[str, Any]]:
    hints: dict[str, dict[str, Any]] = {}
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function", t)
        name = fn.get("name")
        params = fn.get("parameters") or fn.get("input_schema") or fn.get("inputSchema") or {}
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
                shapes = _formats(schema) if formats else []
                hints[path] = {**schema, "_formats": shapes} if shapes else schema
    return hints


def _formats(schema: dict[str, Any]) -> list[tuple[str, int, str]]:
    """ID formats a schema states: a `pattern` like ^#W\\d{7}$, `examples`, or quoted examples in the
    description ("such as '#W0000000'"), each as (literal prefix, digit count, literal suffix)."""
    out: list[tuple[str, int, str]] = []
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and (m := _PATTERN_SHAPE.match(pattern)) and (m.group(1) or m.group(3)):
        out.append((m.group(1), int(m.group(2)), m.group(3)))
    examples = [e for e in schema.get("examples") or () if isinstance(e, str)]
    desc = schema.get("description")
    if isinstance(desc, str) and _DESC_CUE.search(desc):
        examples += _QUOTED.findall(desc)
    for e in examples:
        m = _ONE_DIGIT_RUN.match(e)
        if m and (m.group(1) or m.group(3)):
            shape = (m.group(1), len(m.group(2)), m.group(3))
            if shape not in out:
                out.append(shape)
    return out
