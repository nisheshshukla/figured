"""Everything the agent has seen so far, and where a value could have come from."""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field

from figured.derive import Index
from figured.evidence import build_evidence
from figured.extract import extract_numbers
from figured.policy import Policy

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
STRICT_DERIVATIONS = frozenset({"cell"})
_PREFIXED = re.compile(r"^(#?[a-z]{0,2}[-#]?)(\d+)$")


def norm(text: str) -> str:
    return _WS.sub(" ", text.lower()).strip()


@dataclass(slots=True)
class Source:
    kind: str
    label: str
    step: int
    text: str
    dates: set[DateKey] = field(default_factory=set)

    def ref(self) -> str:
        return f"{self.label}@{self.step}"


@dataclass(frozen=True, slots=True)
class Hit:
    source: Source
    how: str
    explanation: str = ""


class SourceStore:
    def __init__(
        self, numeric_policy: Policy, as_of: dt.date | None = None, date_shift_days: int = 7
    ) -> None:
        self.sources: list[Source] = []
        self.as_of = as_of
        self.date_shift_days = date_shift_days
        self.shift_days: set[int] = set()
        self._numeric_policy = numeric_policy
        self._strict_policy = numeric_policy.with_overrides(
            derivations=STRICT_DERIVATIONS, rel_tolerance=0.001
        )
        self._numbers: list[tuple[float, int]] = []
        self._index: dict[bool, tuple[int, Index]] = {}

    def add(self, kind: str, label: str, step: int, text: str, numbers: list[float] | None = None) -> None:
        if not text and not numbers:
            return
        src = Source(kind, label, step, norm(text), parse_dates(text, self.as_of))
        if kind == "user":
            self.shift_days |= _shifts(text)
        self.sources.append(src)
        idx = len(self.sources) - 1
        vals = numbers if numbers is not None else _numbers_in(text)
        self._numbers.extend((v, idx) for v in vals)

    def find(
        self, kind: str, value: object, allowed: set[str] | None = None, *, strict: bool = False
    ) -> Hit | None:
        """Most recent source that can account for `value`, restricted to `allowed` source kinds."""
        candidates = [s for s in reversed(self.sources) if allowed is None or _allowed(s, allowed)]
        if kind == "number":
            return self._find_number(float(value), candidates, allowed, strict)  # type: ignore[arg-type]
        if kind == "date":
            return self._find_date(str(value), candidates)
        text = norm(str(value))
        variants = _variants(kind, text)
        for src in candidates:
            for v in variants:
                if _contains(src.text, v):
                    return Hit(src, "exact")
        if kind == "identifier":
            m = _PREFIXED.match(text)
            if m and len(m.group(2)) >= 5:
                for src in candidates:
                    if _contains(src.text, m.group(2)):
                        return Hit(src, "composed", f"{m.group(1) or ''} prefix added to {m.group(2)}")
        if kind == "phrase":
            parts = [t for t in re.findall(r"[a-z0-9][a-z0-9_\-]*", text) if any(c.isdigit() for c in t)]
            if parts:
                owners: list[Source] = []
                for p in parts:
                    owner = next((s for s in candidates if _contains(s.text, p)), None)
                    if owner is None:
                        return None
                    owners.append(owner)
                return Hit(owners[0], "composed", "every number-bearing part appears in the sources")
        return None

    def _find_date(self, value: str, candidates: list[Source]) -> Hit | None:
        keys = parse_dates(value)
        if not keys:
            return None
        for src in candidates:
            for y, m, d in keys:
                for sy, sm, sd in src.dates:
                    if sm == m and sd == d and (sy is None or y is None or sy == y):
                        how = "date" if sy is not None or y is None else "date (year inferred)"
                        return Hit(src, how)
        if self.shift_days:
            for y, m, d in keys:
                for src in candidates:
                    for sy, sm, sd in src.dates:
                        year = y or sy or (self.as_of.year if self.as_of else None)
                        base_year = sy or year
                        if year is None or base_year is None:
                            continue
                        try:
                            gap = (dt.date(year, m, d) - dt.date(base_year, sm, sd)).days
                        except ValueError:
                            continue
                        if gap and abs(gap) in self.shift_days and abs(gap) <= self.date_shift_days:
                            return Hit(
                                src, f"derived:date shift ({gap:+d} days)", "the user asked to move a date"
                            )
        return None

    def _find_number(
        self, value: float, candidates: list[Source], allowed: set[str] | None, strict: bool
    ) -> Hit | None:
        pol = self._strict_policy if strict else self._numeric_policy
        ok = None if allowed is None else {i for i, s in enumerate(self.sources) if _allowed(s, allowed)}
        for v, owner in reversed(self._numbers):
            if (ok is None or owner in ok) and pol.close(abs(value), abs(v)):
                return Hit(self.sources[owner], "exact")
        if strict:
            for v, owner in reversed(self._numbers):
                if (ok is None or owner in ok) and v:
                    for k in range(2, 10):
                        if pol.close(abs(value), abs(v * k)):
                            return Hit(self.sources[owner], "derived:count", f"{v:g} × {k}")
        if float(value).is_integer() and abs(value) >= 10_000:
            digits = str(int(abs(value)))
            for src in candidates:
                if _contains(src.text, digits):
                    return Hit(src, "exact")
        if not candidates:
            return None
        index = self._numeric_index(ok, strict)
        match = index.lookup(value, pol) if index is not None else None
        if match is None:
            return None
        return Hit(candidates[0], f"derived:{match.kind}", match.explanation)

    def _numeric_index(self, only: set[int] | None, strict: bool) -> Index | None:
        cached = self._index.get(strict)
        if only is None and cached is not None and cached[0] == len(self._numbers):
            return cached[1]
        nums = [v for v, owner in reversed(self._numbers) if only is None or owner in only]
        if not nums:
            return None
        index = Index(
            build_evidence([[v] for v in nums]), self._strict_policy if strict else self._numeric_policy
        )
        if only is None:
            self._index[strict] = (len(self._numbers), index)
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
    start = 0
    while True:
        i = haystack.find(needle, start)
        if i < 0:
            return False
        before = haystack[i - 1] if i > 0 else " "
        after = haystack[i + len(needle)] if i + len(needle) < len(haystack) else " "
        before_ok = not before.isalnum() and (before != "_" or digit_start)
        after_ok = not after.isalnum() and after != "_"
        if before_ok and after_ok:
            return True
        start = i + 1


def _numbers_in(text: str) -> list[float]:
    """Numbers a source offers. For JSON, numbers inside string values come first and numeric values
    last, so quantities such as prices sit in the recent window that derivations search; strings that
    are only a long run of digits are identifiers, matched as text instead."""
    stripped = text.lstrip()
    if stripped[:1] in "[{":
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if data is not None:
            from_strings: list[float] = []
            numeric: list[float] = []
            stack = [data]
            while stack:
                x = stack.pop()
                if isinstance(x, dict):
                    stack.extend(x.values())
                elif isinstance(x, list):
                    stack.extend(x)
                elif isinstance(x, str):
                    if not (x.isdigit() and len(x) >= 6):
                        from_strings.extend(f.value for f in extract_numbers(x))
                elif isinstance(x, int | float) and not isinstance(x, bool):
                    numeric.append(float(x))
            return from_strings + numeric
    return [f.value for f in extract_numbers(text)]
