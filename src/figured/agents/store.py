"""Everything the agent has seen so far, and where a value could have come from.

Built for the hot path of an online guardrail. The latency-critical moment is the check before a tool
call executes, so all parsing happens when a source is added (a tool result arrives just before the
next model call, which takes seconds anyway). Numbers go into log-scale buckets, so a tolerance lookup
touches a few dozen entries however much the agent has seen; dates are indexed by month and day.
Identifier search is a C-level substring scan per source, newest first.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import dataclass, field

from figured.derive import Index, fmt
from figured.evidence import build_evidence
from figured.extract import scan_values
from figured.policy import DERIVATIONS, Policy

from .values import DateKey, parse_dates

_WS = re.compile(r"\s+")
_N = r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)"
_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
_WORDS.update({"eight": 8, "nine": 9, "ten": 10})
_SHIFT_ONE = re.compile(r"\b(?:next|following|previous|prior) day\b|\bday (?:after|before)\b", re.IGNORECASE)
_SHIFT_N = re.compile(
    _N + r" (day|week)s? (?:later|earlier|after|before|ahead|back|sooner)\b|\bby " + _N + r" (day|week)s?\b",
    re.IGNORECASE,
)
_PREFIXED = re.compile(r"^(#?[a-z]{0,2}[-#]?)(\d+)$")
_PHRASE_PARTS = re.compile(r"[a-z0-9][a-z0-9_\-]*")
_PAIR_DERIVATIONS = frozenset(DERIVATIONS) - {"cell", "column_sum", "row_sum"}
_BUCKET = math.log1p(0.001)
STRICT_TOLERANCE = 0.001
PAIR_WINDOW = 60
MAX_INDEX_CHARS = 1_000_000
_JOIN = "\n\x00\n"


def norm(text: str) -> str:
    return _WS.sub(" ", text.lower()).strip()


@dataclass(slots=True)
class Source:
    kind: str
    label: str
    step: int
    raw: str
    low: str
    numbers: list[float] | None = None
    dates: set[DateKey] | None = None
    _collapsed: str | None = field(default=None, repr=False)

    def ref(self) -> str:
        return f"{self.label}@{self.step}"

    @property
    def collapsed(self) -> str:
        if self._collapsed is None:
            self._collapsed = " ".join(self.low.split())
        return self._collapsed


@dataclass(frozen=True, slots=True)
class Hit:
    source: Source
    how: str
    explanation: str = ""


class SourceStore:
    def __init__(
        self,
        numeric_policy: Policy,
        as_of: dt.date | None = None,
        date_shift_days: int = 7,
        max_index_chars: int = MAX_INDEX_CHARS,
    ) -> None:
        self.sources: list[Source] = []
        self.as_of = as_of
        self.date_shift_days = date_shift_days
        self.max_index_chars = max_index_chars
        self.shift_days: set[int] = set()
        self._numeric_policy = numeric_policy
        self._pair_policy = numeric_policy.with_overrides(derivations=_PAIR_DERIVATIONS)
        self._buckets: dict[int, list[tuple[int, int, float]]] = {}
        self._zeros: list[tuple[int, int, float]] = []
        self._total = 0.0
        self._count = 0
        self._date_index: dict[tuple[int, int], list[tuple[int, int | None]]] = {}
        self._pair_cache: tuple[int, Index | None] | None = None

    def add(self, kind: str, label: str, step: int, text: str, numbers: list[float] | None = None) -> None:
        """Record a source and index its numbers and dates now, off the latency-critical path."""
        if not text and not numbers:
            return
        if kind == "user":
            self.shift_days |= _shifts(text)
        i = len(self.sources)
        src = Source(kind, label, step, text, text.lower(), numbers)
        self.sources.append(src)
        head = text[: self.max_index_chars]
        if src.numbers is None:
            src.numbers = _numbers_in(head) if head else []
        for pos, v in enumerate(src.numbers):
            entry = (i, pos, v)
            a = abs(v)
            if a == 0:
                self._zeros.append(entry)
            else:
                self._buckets.setdefault(math.floor(math.log(a) / _BUCKET), []).append(entry)
            self._total += v
            self._count += 1
        src.dates = parse_dates(head, self.as_of) if head else set()
        for y, m, d in src.dates:
            self._date_index.setdefault((m, d), []).append((i, y))

    def find(
        self, kind: str, value: object, allowed: set[str] | None = None, *, strict: bool = False
    ) -> Hit | None:
        """Most recent source that can account for `value`, restricted to `allowed` source kinds."""
        if kind == "number":
            return self._find_number(float(value), allowed, strict)  # type: ignore[arg-type]
        if kind == "date":
            return self._find_date(str(value), allowed)
        text = norm(str(value))
        if kind == "phrase":
            i = self._search(text, allowed, collapsed=True)
            if i >= 0:
                return Hit(self.sources[i], "exact")
            parts = [t for t in _PHRASE_PARTS.findall(text) if any(c.isdigit() for c in t)]
            owners = [self._search(p, allowed) for p in parts]
            if not owners or min(owners) < 0:
                return None
            return Hit(
                self.sources[owners[0]], "composed", "every number-bearing part appears in the sources"
            )
        best = max(self._search(v, allowed) for v in _variants(kind, text))
        if best >= 0:
            return Hit(self.sources[best], "exact")
        if kind == "identifier":
            m = _PREFIXED.match(text)
            if m and len(m.group(2)) >= 5:
                i = self._search(m.group(2), allowed)
                if i >= 0:
                    return Hit(
                        self.sources[i], "composed", f"{m.group(1) or ''} prefix added to {m.group(2)}"
                    )
        return None

    def _search(self, needle: str, allowed: set[str] | None, *, collapsed: bool = False) -> int:
        """Index of the newest source containing `needle` as a token, or -1."""
        for i in range(len(self.sources) - 1, -1, -1):
            src = self.sources[i]
            if allowed is not None and not _allowed(src, allowed):
                continue
            if _contains(src.collapsed if collapsed else src.low, needle):
                return i
        return -1

    def _newest(self, allowed: set[str] | None) -> Source | None:
        for i in range(len(self.sources) - 1, -1, -1):
            if allowed is None or _allowed(self.sources[i], allowed):
                return self.sources[i]
        return None

    # dates

    def _find_date(self, value: str, allowed: set[str] | None) -> Hit | None:
        keys = parse_dates(value)
        if not keys:
            return None
        best: tuple[int, str] | None = None
        for y, m, d in keys:
            for i, sy in self._date_index.get((m, d), ()):
                if not (sy is None or y is None or sy == y):
                    continue
                if allowed is not None and not _allowed(self.sources[i], allowed):
                    continue
                how = "date" if sy is not None or y is None else "date (year inferred)"
                if best is None or i > best[0] or (i == best[0] and how == "date"):
                    best = (i, how)
        if best is not None:
            return Hit(self.sources[best[0]], best[1])
        if not self.shift_days:
            return None
        found: tuple[int, int] | None = None
        for y, m, d in keys:
            for (sm, sd), entries in self._date_index.items():
                for i, sy in entries:
                    if allowed is not None and not _allowed(self.sources[i], allowed):
                        continue
                    year = y or sy or (self.as_of.year if self.as_of else None)
                    base_year = sy or year
                    if year is None or base_year is None:
                        continue
                    try:
                        gap = (dt.date(year, m, d) - dt.date(base_year, sm, sd)).days
                    except ValueError:
                        continue
                    shifted = gap and abs(gap) in self.shift_days and abs(gap) <= self.date_shift_days
                    if shifted and (found is None or i > found[0]):
                        found = (i, gap)
        if found is None:
            return None
        return Hit(
            self.sources[found[0]],
            f"derived:date shift ({found[1]:+d} days)",
            "the user asked to move a date",
        )

    # numbers

    def _nearest(self, a: float, rel: float, allowed: set[str] | None) -> tuple[int, int, float] | None:
        """Newest number v with |a - |v|| <= rel * |v|, the same test as Policy.close."""
        if a == 0:
            pool = self._zeros
        else:
            lo, hi = a / (1 + rel), a / (1 - rel) if rel < 1 else math.inf
            b_lo = math.floor(math.log(lo) / _BUCKET) - 1
            b_hi = math.floor(math.log(hi) / _BUCKET) + 1 if hi != math.inf else b_lo + 10_000
            pool = []
            get = self._buckets.get
            for b in range(b_lo, b_hi + 1):
                entries = get(b)
                if entries:
                    pool.extend(entries)
        best: tuple[int, int, float] | None = None
        for entry in pool:
            if best is not None and entry[:2] <= best[:2]:
                continue
            v = abs(entry[2])
            if not (a == v or (v and abs(a - v) / v <= rel)):
                continue
            if allowed is not None and not _allowed(self.sources[entry[0]], allowed):
                continue
            best = entry
        return best

    def _find_number(self, value: float, allowed: set[str] | None, strict: bool) -> Hit | None:
        rel = STRICT_TOLERANCE if strict else self._numeric_policy.rel_tolerance
        a = abs(value)
        hit = self._nearest(a, rel, allowed)
        if hit is not None:
            return Hit(self.sources[hit[0]], "exact")
        if strict and a:
            best: tuple[tuple[int, int, float], int] | None = None
            for k in range(2, 10):
                e = self._nearest(a / k, rel, allowed)
                if e is not None and (best is None or e[:2] > best[0][:2]):
                    best = (e, k)
            if best is not None:
                (i, _, v), k = best
                return Hit(self.sources[i], "derived:count", f"{v:g} × {k}")
        if float(value).is_integer() and a >= 10_000:
            i = self._search(str(int(a)), allowed)
            if i >= 0:
                return Hit(self.sources[i], "exact")
        newest = self._newest(allowed)
        if newest is None or strict:
            return None
        pol = self._numeric_policy
        if allowed is None:
            total, count = self._total, self._count
        else:
            vals = [v for s in self.sources if _allowed(s, allowed) for v in (s.numbers or ())]
            total, count = sum(vals), len(vals)
        if count >= 2 and pol.close(a, abs(total)):
            return Hit(newest, "derived:column_sum", f"sum of c0 over {count} rows = {fmt(total)}")
        index = self._pair_index(allowed)
        match = index.lookup(value, self._pair_policy) if index is not None else None
        if match is None:
            return None
        return Hit(newest, f"derived:{match.kind}", match.explanation)

    def _pair_index(self, allowed: set[str] | None) -> Index | None:
        """Pairwise derivations over the most recent numbers, the window the search has always used."""
        if allowed is None and self._pair_cache is not None and self._pair_cache[0] == self._count:
            return self._pair_cache[1]
        recent: list[float] = []
        for i in range(len(self.sources) - 1, -1, -1):
            src = self.sources[i]
            if allowed is not None and not _allowed(src, allowed):
                continue
            for v in reversed(src.numbers or ()):
                recent.append(v)
                if len(recent) >= PAIR_WINDOW:
                    break
            if len(recent) >= PAIR_WINDOW:
                break
        index = Index(build_evidence([[v] for v in recent]), self._pair_policy) if recent else None
        if allowed is None:
            self._pair_cache = (self._count, index)
        return index


def _shifts(text: str) -> set[int]:
    """Day offsets a user asked for: "a day later" -> {1}, "by two weeks" -> {14}."""
    out = {1} if _SHIFT_ONE.search(text) else set()
    for m in _SHIFT_N.finditer(text):
        n, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        k = int(n) if n.isdigit() else _WORDS.get(n.lower(), 0)
        out.add(k * (7 if unit.lower() == "week" else 1))
    return {k for k in out if k}


def _allowed(src: Source, allowed: set[str]) -> bool:
    return src.kind in allowed or src.label in allowed


def _variants(kind: str, text: str) -> list[str]:
    out = [text]
    if kind == "identifier" and text.startswith("#"):
        out.append(text[1:])
    if kind == "url":
        out.append(text.rstrip("/"))
    return out


def _contains(haystack: str, needle: str) -> bool:
    """Needle as a whole token. A run of digits may also be the tail of an identifier (paypal_5334408)."""
    if not needle:
        return False
    digit_start = needle[0].isdigit()
    n = len(needle)
    i = haystack.find(needle)
    while i >= 0:
        before = haystack[i - 1] if i > 0 else " "
        after = haystack[i + n] if i + n < len(haystack) else " "
        if not before.isalnum() and (before != "_" or digit_start) and not after.isalnum() and after != "_":
            return True
        i = haystack.find(needle, i + 1)
    return False


def _numbers_in(text: str) -> list[float]:
    """Numbers a source offers. For JSON, numbers inside string values come first and numeric values
    last, so quantities such as prices sit in the recent window that derivations search; strings that
    are only a long run of digits are identifiers, matched as text instead. String values are scanned
    in one pass, joined by a separator that cannot form a range."""
    stripped = text.lstrip()
    if stripped[:1] in "[{":
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            data = None
        if data is not None:
            strings: list[str] = []
            numeric: list[float] = []
            stack = [data]
            while stack:
                x = stack.pop()
                if isinstance(x, dict):
                    stack.extend(x.values())
                elif isinstance(x, list):
                    stack.extend(x)
                elif isinstance(x, str):
                    if not (x.isdigit() and len(x) >= 6) and any(c.isdigit() for c in x):
                        strings.append(x)
                elif isinstance(x, int | float) and not isinstance(x, bool):
                    numeric.append(float(x))
            return (scan_values(_JOIN.join(strings)) if strings else []) + numeric
    return scan_values(text)
