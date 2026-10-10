"""Everything the agent has seen so far, and where a value could have come from.

Built for the hot path of an online guardrail. The latency-critical moment is the check before a tool
call executes, so all parsing happens when a source is added (a tool result arrives just before the
next model call, which takes seconds anyway). Numbers go into log-scale buckets, so a tolerance lookup
touches a few dozen entries however much the agent has seen; dates are indexed by month and day.
Identifier search is a C-level substring scan per source, newest first.

Two kinds of check share the store. Argument checks are strict: values must be copied (an identifier
may differ only in case and separators), an amount must match to the cent and in sign, and the only
arithmetic accepted is a price times a stated count, a stated percentage of an amount, or the sum of
two money fields in one source. A digit run inside another identifier does not vouch for anything. Claim
checks on the agent's own text are looser, because prose rounds, sums, and refers to "the card ending
in 4242".
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from figured.derive import Index, fmt
from figured.evidence import build_evidence
from figured.extract import scan_values
from figured.policy import DERIVATIONS, Policy

from .values import DateKey, number_words, parse_dates

_WS = re.compile(r"\s+")
_NUM_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}  # fmt: skip
_N = r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_LATER = r"later|after|ahead|forward|out"
_EARLIER = r"earlier|before|sooner|prior"
_SHIFT_PHRASES = (
    (re.compile(r"\b(?:next|following) day\b|\bday after\b", re.IGNORECASE), 1, 1),
    (re.compile(r"\b(?:previous|prior) day\b|\bday before\b", re.IGNORECASE), 1, -1),
    (re.compile(r"\bnext week\b", re.IGNORECASE), 7, 1),
    (re.compile(r"\b(?:last|previous) week\b", re.IGNORECASE), 7, -1),
)
_SHIFT_N = re.compile(_N + r" (day|week)s? (" + _LATER + "|" + _EARLIER + r"|back)\b", re.IGNORECASE)
_SHIFT_BY = re.compile(r"\b(?:by|in) " + _N + r" (day|week)s?\b", re.IGNORECASE)
_COUNT_WORDS = re.compile(r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b", re.IGNORECASE)
_PREFIXED = re.compile(r"^(#?[a-z]{0,2}[-#]?)(\d+)$")
_PHRASE_PARTS = re.compile(r"[a-z0-9][a-z0-9_\-]*")
_PHRASE_SKIP = frozenset(
    {
        "street", "st", "avenue", "ave", "road", "rd", "drive", "dr", "lane", "ln", "boulevard", "blvd",
        "court", "ct", "place", "pl", "way", "suite", "ste", "apartment", "apt", "unit", "floor", "fl",
        "building", "bldg", "north", "south", "east", "west", "the", "and",
    }
)  # fmt: skip
_PAIR_DERIVATIONS = frozenset(DERIVATIONS) - {"cell", "column_sum", "row_sum"}
_BUCKET = math.log1p(0.001)
STRICT_TOLERANCE = 0.001
STRICT_ABS = 0.005
SUM_FIELDS = 8
SHIFT_HORIZON_DAYS = 730
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|percent\b|per cent\b)", re.IGNORECASE)
_EXAMPLE_CUES = ("e.g.", "for example", "for instance", "such as", "like", "in the form")
_EXAMPLE_VALUE = re.compile(r"[\s:,(\"'`]*(#?[\w@.+\-/]*\d[\w@.+\-/]*|[\w.+\-]+@[\w.\-]+)")
_SEPARATORS = r"[\s\-./()]{0,3}"
_SQUEEZE = (" ", "\t", "\n", "\r", "-", ".", "/", "(", ")")
_MONEY_KEY = re.compile(
    r"price|amount|total|cost|fee|tax|balance|fare|charge|subtotal|refund|payment|paid|rent|bill|tip|discount",
    re.IGNORECASE,
)
_CURRENCY = re.compile(r"[$€£¥]\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
PAIR_WINDOW = 60
MAX_COUNT = 12
MAX_INDEX_CHARS = 1_000_000
_JOIN = "\n\x00\n"
_PLAIN_DIGITS = re.compile(r"(?<![\d.,$€£¥\w-])\d{5,}(?![\d,%\w]|\.\d)")


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
    tainted: bool = False
    echoes: frozenset[str] = frozenset()
    money: list[float] = field(default_factory=list)
    squeezed: str = ""
    named: str = ""
    _records: dict[str, set[str]] | None = field(default=None, repr=False)
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
        date_shift_days: int = 31,
        max_index_chars: int = MAX_INDEX_CHARS,
    ) -> None:
        self.sources: list[Source] = []
        self.as_of = as_of
        self.date_shift_days = date_shift_days
        self.max_index_chars = max_index_chars
        self.shifts: set[int] = set()
        self.counts: set[int] = set()
        self.rates: set[float] = set()
        self.latest: dt.date | None = None
        self._numeric_policy = numeric_policy
        self._pair_policy = numeric_policy.with_overrides(derivations=_PAIR_DERIVATIONS)
        self._buckets: dict[int, list[tuple[int, int, float]]] = {}
        self._zeros: list[tuple[int, int, float]] = []
        self._total = 0.0
        self._count = 0
        self._date_index: dict[tuple[int, int], list[tuple[int, int | None]]] = {}
        self._pair_cache: tuple[int, Index | None] | None = None

    def add(
        self,
        kind: str,
        label: str,
        step: int,
        text: str,
        numbers: list[float] | None = None,
        *,
        tainted: bool = False,
        echoes: set[str] | None = None,
        named: str = "",
    ) -> None:
        """Record a source and index it now, off the latency-critical path.

        `echoes` are values the call that produced this result used without a source; the result does
        not vouch for them (an error that repeats a made-up ID). A `tainted` source vouches for
        nothing (the output of a calculator fed made-up operands). Both are kept for explanations."""
        if not text and not numbers:
            return
        i = len(self.sources)
        if kind == "system":
            echoes = (echoes or set()) | _examples(text)
        echo = frozenset(f for e in echoes or () for f in (norm(e), norm(e).lstrip("#")))
        src = Source(
            kind, label, step, text, text.lower(), numbers, tainted=tainted, echoes=echo, named=named
        )
        self.sources.append(src)
        if tainted:
            src.numbers, src.dates = [], set()
            return
        if kind == "user":
            self.shifts |= _shifts(text)
            self.counts |= {_NUM_WORDS[w.lower()] for w in _COUNT_WORDS.findall(text)}
        head = text[: self.max_index_chars]
        src.squeezed = _squeeze(src.low[: self.max_index_chars])
        if src.numbers is None:
            src.numbers, lengths, src.money = _numbers_in(head) if head else ([], [], [])
            if kind in ("user", "tool"):
                self.counts |= {n for n in lengths if 2 <= n <= MAX_COUNT}
            if kind in ("user", "system"):
                src.numbers += number_words(head)
        if "%" in head or "percent" in head or "per cent" in head:
            self.rates |= {
                float(m.group(1)) / 100 for m in _PERCENT.finditer(head) if 0 < float(m.group(1)) < 100
            }
        if kind == "user":
            self.counts |= {int(v) for v in src.numbers if v.is_integer() and 2 <= v <= MAX_COUNT}
        if echo:
            echoed = {f for e in echo for f in scan_values(e)}
            src.numbers = [v for v in src.numbers if v not in echoed]
            src.money = [v for v in src.money if v not in echoed]
        for pos, v in enumerate(src.numbers):
            entry = (i, pos, v)
            a = abs(v)
            if a == 0:
                self._zeros.append(entry)
            else:
                self._buckets.setdefault(math.floor(math.log(a) / _BUCKET), []).append(entry)
            self._total += v
            self._count += 1
        src.dates = parse_dates(head, self.as_of, intl=kind in ("user", "system")) if head else set()
        if echo:
            src.dates -= {k for e in echo for k in parse_dates(e, self.as_of)}
        for y, m, d in src.dates:
            self._date_index.setdefault((m, d), []).append((i, y))
            if y is not None and 1900 < y < 2200:
                try:
                    day = dt.date(y, m, d)
                except ValueError:
                    continue
                if self.latest is None or day > self.latest:
                    self.latest = day

    def find(
        self,
        kind: str,
        value: object,
        allowed: set[str] | None = None,
        *,
        strict: bool = False,
        shift_ok: bool = True,
        formats: list[tuple[str, int, str]] | None = None,
    ) -> Hit | None:
        """Most recent untainted source that can account for `value`, limited to `allowed` kinds."""
        if kind == "number":
            number = float(value)  # type: ignore[arg-type]
            return self._find_number(number, allowed, strict) if math.isfinite(number) else None
        if kind == "date":
            return self._find_date(str(value), allowed, shift_ok)
        text = norm(str(value))
        if kind == "phrase":
            i = self._search(text, allowed, collapsed=True)
            if i >= 0:
                return Hit(self.sources[i], "exact")
            return self._composed_phrase(text, allowed)
        best = max(self._search(v, allowed, tails=not strict) for v in _variants(kind, text))
        if best >= 0:
            return Hit(self.sources[best], "exact")
        if kind == "url":
            return self._url(text, allowed)
        if kind == "identifier":
            return (
                (self._formatted(text, formats, allowed) if formats else None)
                or self._separated(text, allowed)
                or self._prefixed(text, allowed)
            )
        return None

    def attributes(self, value: str) -> set[str]:
        """What tool results say about the record an identifier names: the other fields of the JSON
        object holding it ("Headphones", "mastercard", "2478" for an item or a card). Parsed on first
        use, so only side-effecting calls that ask pay for it."""
        key = norm(value).lstrip("#")
        out: set[str] = set()
        for src in self.sources:
            if src.kind == "tool" and not src.tainted:
                if src._records is None:
                    src._records = _records(src.raw[: self.max_index_chars])
                out |= src._records.get(key, set())
        return out

    def bound(self, value: str, text: str, alternatives: list[str]) -> bool:
        """`text` singles out `value`: it names the value, or an attribute of its record that the other
        candidates do not all share ("the headphones", "the Mastercard ending in 2478")."""
        low = text.lower()
        v = norm(value)
        if _contains(low, v) or _contains(low, v.lstrip("#")):
            return True
        mine = self.attributes(v)
        if not mine:
            return False
        theirs = [self.attributes(a) for a in alternatives]
        return any(_contains(low, a) and (not theirs or not all(a in t for t in theirs)) for a in mine)

    def alternatives(self, value: str, within: Source | None = None) -> list[str]:
        """Other values of the same shape as `value` in context (or in one source): letters stay letters,
        digit runs keep their length. Three order IDs, two payment methods: a choice was made."""
        text = norm(value)
        if "@" in text:
            rx = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
        else:
            parts = re.findall(r"[a-z]+|\d+|[^a-z\d]+", text)
            if not any(p[0].isdigit() for p in parts):
                return []
            shape = "".join(
                "[a-z]+" if p[0].isalpha() else rf"\d{{{len(p)}}}" if p[0].isdigit() else re.escape(p)
                for p in parts
            )
            rx = re.compile(r"(?<![a-z0-9_#])" + shape + r"(?![a-z0-9_])")
        found: dict[str, None] = {}
        for src in [within] if within is not None else reversed(self.sources):
            if src.tainted or src.kind == "derived":
                continue
            same = len(src.raw) == len(src.low)
            for m in rx.finditer(src.low):
                if m.group(0) != text and m.group(0).lstrip("#") != text.lstrip("#"):
                    found.setdefault(src.raw[m.start() : m.end()] if same else m.group(0))
                    if len(found) >= 20:
                        return list(found)
        return list(found)

    def tainted_mention(self, kind: str, value: object) -> Source | None:
        """The newest tainted source that mentions `value`, for explaining a miss."""
        if kind == "number" and not math.isfinite(float(value)):  # type: ignore[arg-type]
            return None
        needle = _num_text(float(value)) if kind == "number" else norm(str(value))  # type: ignore[arg-type]
        for i in range(len(self.sources) - 1, -1, -1):
            src = self.sources[i]
            if (src.tainted or needle in src.echoes) and _contains(src.low, needle):
                return src
        return None

    def has_count(self, n: float) -> bool:
        return n.is_integer() and int(n) in self.counts

    # text

    def _search(
        self, needle: str, allowed: set[str] | None, *, collapsed: bool = False, tails: bool = False
    ) -> int:
        """Index of the newest untainted source containing `needle` as a token, or -1."""
        for i in range(len(self.sources) - 1, -1, -1):
            src = self.sources[i]
            if src.tainted or (allowed is not None and not _allowed(src, allowed)):
                continue
            if src.echoes and (needle in src.echoes or needle.lstrip("#") in src.echoes):
                continue
            if _contains(src.collapsed if collapsed else src.low, needle, tails=tails):
                return i
        return -1

    def _matching(
        self, pattern: str, allowed: set[str] | None, needle: str, literal: str, *, squeezed: bool = False
    ) -> int:
        """Newest source matching `pattern`. A source is searched only if it contains `literal` (in its
        separator-free form with `squeezed`), a C-level check, so the pattern is compiled only when
        some source could match."""
        rx: re.Pattern[str] | None = None
        for i in range(len(self.sources) - 1, -1, -1):
            src = self.sources[i]
            if src.tainted or (allowed is not None and not _allowed(src, allowed)):
                continue
            if src.echoes and needle in src.echoes:
                continue
            if literal not in (src.squeezed if squeezed else src.low):
                continue
            rx = rx or re.compile(pattern)
            if rx.search(src.low):
                return i
        return -1

    def _formatted(
        self, text: str, formats: list[tuple[str, int, str]], allowed: set[str] | None
    ) -> Hit | None:
        """An ID in the format a tool's schema states, around digits found in context: "#W9502127" for
        the 9502127 the user typed, when the schema says "such as '#W0000000'". Only the literal
        prefix and suffix are added; the digits must be a whole token in context and the count must
        match, so "credit_card_7334" from "ending in 7334" still fails a seven-digit format."""
        for prefix, n, suffix in formats:
            p, s = prefix.lower(), suffix.lower()
            if len(text) == len(p) + n + len(s) and text.startswith(p) and text.endswith(s):
                core, wrapped = text[len(p) : len(text) - len(s)], ""
            elif text.isdigit() and len(text) == n:
                core, wrapped = p + text + s, text
            else:
                continue
            if not (wrapped or core.isdigit()):
                continue
            i = self._search(core, allowed)
            if i >= 0:
                why = f"the {prefix}{'#' * n}{suffix} format from the tool's schema"
                return Hit(self.sources[i], "normalized", why)
        return None

    def _separated(self, text: str, allowed: set[str] | None) -> Hit | None:
        """The same identifier written with different separators or spacing: "ORD 88213" for
        ORD-88213, "DE89 3704 0044" for DE8937040044, "(415) 555-0132" for +14155550132. Only
        letters and digits must match, in order; separators may be added or dropped between them."""
        core = re.sub(r"[^a-z0-9]", "", text)
        if len(core) < 6 or not any(c.isdigit() for c in core):
            return None
        cores = [core]
        if text.startswith("+") and core.isdigit():
            cores += [core[k:] for k in (1, 2, 3) if len(core) - k >= 9]
        for c in cores:
            pattern = r"(?<![a-z0-9])" + _SEPARATORS.join(map(re.escape, c)) + r"(?![a-z0-9])"
            i = self._matching(pattern, allowed, text, c, squeezed=True)
            if i >= 0:
                how = "the same characters with different separators"
                if c != core:
                    how = "the same number without its country code"
                return Hit(self.sources[i], "normalized", how)
        return None

    def _url(self, text: str, allowed: set[str] | None) -> Hit | None:
        """A URL whose source omitted or differed in the scheme or "www." ("example.com/docs" for
        https://example.com/docs). The host must start the match, so evil-example.com is not example.com."""
        base = re.sub(r"^[a-z][a-z0-9+.\-]*://(?:www\.)?", "", text).rstrip("/")
        if "/" not in base and "." not in base:
            return None
        pattern = r"(?:^|(?<=[\s\"'(<\[=])|(?<=://))(?:www\.)?" + re.escape(base) + r"/?(?![\w/\-%]|\.\w)"
        i = self._matching(pattern, allowed, text, base)
        return (
            Hit(self.sources[i], "normalized", "the same address with a different scheme") if i >= 0 else None
        )

    def _prefixed(self, text: str, allowed: set[str] | None) -> Hit | None:
        """`#W9502127` from a user who typed 9502127: only when the bare digits came from the user and
        an identifier of exactly that shape (same prefix, same digit count) appears in the sources."""
        m = _PREFIXED.match(text)
        if not m or len(m.group(2)) < 5 or not m.group(1):
            return None
        prefix, digits = m.group(1), m.group(2)
        user_ok = {"user"} if allowed is None else ({"user"} & allowed)
        i = self._search(digits, user_ok) if user_ok else -1
        if i < 0:
            return None
        shape = re.compile(
            r"(?<![a-z0-9_])" + re.escape(prefix) + r"\d{" + str(len(digits)) + r"}(?![a-z0-9_])"
        )
        for src in self.sources:
            if not src.tainted and src.kind != "user" and shape.search(src.low):
                return Hit(
                    self.sources[i], "composed", f"{prefix} prefix added to {digits}, matching IDs seen"
                )
        return None

    def _composed_phrase(self, text: str, allowed: set[str] | None) -> Hit | None:
        tokens = _PHRASE_PARTS.findall(text)
        numbered = [t for t in tokens if any(c.isdigit() for c in t)]
        words = [t for t in tokens if t.isalpha() and len(t) >= 3 and t not in _PHRASE_SKIP]
        if not numbered:
            return None
        owners = [self._worded(t, allowed) for t in numbered] + [self._search(t, allowed) for t in words]
        if min(owners) < 0:
            return None
        return Hit(self.sources[owners[0]], "composed", "every number and word appears in the sources")

    def _worded(self, token: str, allowed: set[str] | None) -> int:
        """A number in a phrase ("491" in "Suite 491") must sit beside words in its source too, so an
        address cannot borrow its house number from a price."""
        t = re.escape(token)
        pattern = r"(?<![\w.])" + t + r"\s+[a-z]|[a-z#]\s*" + t + r"(?!\w|\.\d)"
        return self._matching(pattern, allowed, token, token)

    def _newest(self, allowed: set[str] | None) -> Source | None:
        for i in range(len(self.sources) - 1, -1, -1):
            src = self.sources[i]
            if not src.tainted and (allowed is None or _allowed(src, allowed)):
                return src
        return None

    # dates

    def _year_ok(self, y: int | None, sy: int | None, m: int, d: int) -> tuple[bool, str]:
        if sy is not None or y is None:
            return (sy is None or y is None or sy == y), "date"
        if self.as_of is None:
            return True, "date (year inferred)"
        return y == _nearest_year(self.as_of, m, d), "date (year inferred from the reference date)"

    def _find_date(self, value: str, allowed: set[str] | None, shift_ok: bool) -> Hit | None:
        keys = parse_dates(value)
        if not keys:
            return None
        best: tuple[int, str] | None = None
        for y, m, d in keys:
            for i, sy in self._date_index.get((m, d), ()):
                ok, how = self._year_ok(y, sy, m, d)
                if not ok or (allowed is not None and not _allowed(self.sources[i], allowed)):
                    continue
                if best is None or i > best[0] or (i == best[0] and how == "date"):
                    best = (i, how)
        if best is not None:
            return Hit(self.sources[best[0]], best[1])
        if not self.shifts or not shift_ok:
            return None
        now = self.as_of or self.latest
        found: tuple[int, int] | None = None
        for y, m, d in keys:
            for (sm, sd), entries in self._date_index.items():
                for i, sy in entries:
                    if allowed is not None and not _allowed(self.sources[i], allowed):
                        continue
                    base_year = sy or y or (self.as_of.year if self.as_of else None)
                    if base_year is None:
                        continue
                    try:
                        base = dt.date(base_year, sm, sd)
                        target = dt.date(y if y is not None else base_year, m, d)
                    except ValueError:
                        continue
                    if now is not None and abs((base - now).days) > SHIFT_HORIZON_DAYS:
                        continue
                    gap = (target - base).days
                    newer = found is None or i > found[0]
                    if gap in self.shifts and abs(gap) <= self.date_shift_days and newer:
                        found = (i, gap)
        if found is None:
            return None
        how = f"derived:date shift ({found[1]:+d} days)"
        return Hit(self.sources[found[0]], how, "the user asked to move a date")

    # numbers

    def _nearest(
        self, a: float, rel: float, allowed: set[str] | None, sign: int = 0
    ) -> tuple[int, int, float] | None:
        """Newest number v with |a - |v|| / |v| <= rel. With a sign, v must have that sign."""
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
            raw = entry[2]
            if sign and raw and (raw > 0) != (sign > 0):
                continue
            v = abs(raw)
            if not (a == v or (v and abs(a - v) / v <= rel)):
                continue
            if allowed is not None and not _allowed(self.sources[entry[0]], allowed):
                continue
            best = entry
        return best

    def _find_number(self, value: float, allowed: set[str] | None, strict: bool) -> Hit | None:
        a = abs(value)
        if strict:
            return self._find_strict(value, allowed)
        hit = self._nearest(a, self._numeric_policy.rel_tolerance, allowed)
        if hit is not None:
            return Hit(self.sources[hit[0]], "exact")
        if float(value).is_integer() and a >= 10_000:
            i = self._search(str(int(a)), allowed, tails=True)
            if i >= 0:
                return Hit(self.sources[i], "exact")
        newest = self._newest(allowed)
        if newest is None:
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

    def _find_strict(self, value: float, allowed: set[str] | None) -> Hit | None:
        """An argument amount: the same number to the cent and in sign, or a price times a stated count,
        a stated percentage of an amount (a tip, a tax, a discount), or two amounts of one source added."""
        a = abs(value)
        sign = (value > 0) - (value < 0)
        rel = min(STRICT_TOLERANCE, STRICT_ABS / a) if a else 0.0
        hit = self._nearest(a, rel, allowed, sign)
        if hit is not None:
            return Hit(self.sources[hit[0]], "exact")
        if not a:
            return None
        best: tuple[tuple[int, int, float], str] | None = None
        factors = [
            (float(k), f"× {k}, a count stated in the conversation", "count") for k in sorted(self.counts)
        ]
        for r in sorted(self.rates):
            pct = f"{r * 100:g}%"
            factors += [(r, f"× {pct}, a stated percentage", "percent")]
            factors += [(1 + r, f"plus {pct}", "percent"), (1 - r, f"minus {pct}", "percent")]
        for factor, words, how in factors:
            e = self._nearest(a / factor, rel, allowed, sign)
            if e is not None and (best is None or e[:2] > best[0][:2]):
                best = (e, f"derived:{how}|{e[2]:g} {words}")
        if best is not None:
            how, explanation = best[1].split("|", 1)
            return Hit(self.sources[best[0][0]], how, explanation)
        return self._same_source_sum(value, rel, allowed)

    def _same_source_sum(self, value: float, rel: float, allowed: set[str] | None) -> Hit | None:
        """`value` is two money fields of one source added: the prices of two items of one order, a
        price and its tax. Fields are money by name (price, total, fee, tax...) or by a currency sign. Only
        sources with a few such fields count: among a search result's sixty fares, some pair adds up to
        almost any amount."""
        tol = rel * abs(value)
        for i in range(len(self.sources) - 1, -1, -1):
            src = self.sources[i]
            if src.tainted or (allowed is not None and not _allowed(src, allowed)):
                continue
            if len(src.money) > SUM_FIELDS:
                continue
            nums = sorted(src.money)
            lo, hi = 0, len(nums) - 1
            while lo < hi:
                total = nums[lo] + nums[hi]
                if abs(total - value) <= tol:
                    return Hit(src, "derived:sum", f"{nums[lo]:g} + {nums[hi]:g} from the same source")
                if total < value:
                    lo += 1
                else:
                    hi -= 1
        return None

    def _pair_index(self, allowed: set[str] | None) -> Index | None:
        """Pairwise derivations over the most recent numbers, the window the search has always used."""
        if allowed is None and self._pair_cache is not None and self._pair_cache[0] == self._count:
            return self._pair_cache[1]
        recent: list[float] = []
        for i in range(len(self.sources) - 1, -1, -1):
            src = self.sources[i]
            if src.tainted or (allowed is not None and not _allowed(src, allowed)):
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


def _nearest_year(as_of: dt.date, m: int, d: int) -> int | None:
    """The year that puts month/day closest to the reference date."""
    best: tuple[int, int] | None = None
    for y in (as_of.year - 1, as_of.year, as_of.year + 1):
        try:
            gap = abs((dt.date(y, m, d) - as_of).days)
        except ValueError:
            continue
        if best is None or gap < best[0]:
            best = (gap, y)
    return best[1] if best else None


def _shifts(text: str) -> set[int]:
    """Signed day offsets a user asked for: "a day later" -> {+1}, "two weeks earlier" -> {-14}.
    Undirected wording ("move it by two days", "push it back a day") allows both directions."""
    out: set[int] = set()
    for rx, days, sign in _SHIFT_PHRASES:
        if rx.search(text):
            out.add(days * sign)
    for m in _SHIFT_N.finditer(text):
        k = _amount(m.group(1), m.group(2))
        word = m.group(3).lower()
        if re.fullmatch(_LATER, word):
            out.add(k)
        elif re.fullmatch(_EARLIER, word):
            out.add(-k)
        else:
            out |= {k, -k}
    for m in _SHIFT_BY.finditer(text):
        k = _amount(m.group(1), m.group(2))
        out |= {k, -k}
    return {k for k in out if k}


def _amount(n: str, unit: str) -> int:
    k = int(n) if n.isdigit() else _NUM_WORDS.get(n.lower(), 0)
    return k * (7 if unit.lower() == "week" else 1)


def _allowed(src: Source, allowed: set[str]) -> bool:
    return src.kind in allowed or src.label in allowed


def _variants(kind: str, text: str) -> list[str]:
    out = [text]
    if kind == "identifier" and text.startswith("#"):
        out.append(text[1:])
    if kind == "url":
        out.append(text.rstrip("/"))
    return out


def _num_text(v: float) -> str:
    return str(int(v)) if v.is_integer() else repr(v)


def _contains(haystack: str, needle: str, *, tails: bool = False) -> bool:
    """Needle as a whole token. With `tails`, it may also be the tail of an identifier after an underscore
    (5334408 or card_5334408 in paypal_5334408, gift_card_5334408), which prose uses ("the account
    ending in 5334408", "gift card_5334408") but an argument must not."""
    if not needle:
        return False
    n = len(needle)
    i = haystack.find(needle)
    while i >= 0:
        before = haystack[i - 1] if i > 0 else " "
        after = haystack[i + n] if i + n < len(haystack) else " "
        if not before.isalnum() and (before != "_" or tails) and not after.isalnum() and after != "_":
            return True
        i = haystack.find(needle, i + 1)
    return False


def _numbers_in(text: str) -> tuple[list[float], list[int], list[float]]:
    """Numbers a source offers, the lengths of its JSON arrays (natural counts: passengers, items), and
    the numbers that are money (a JSON value under a key such as price or total, or a currency amount).
    For JSON, numbers inside string values come first and numeric values last, so quantities such as
    prices sit in the recent window that derivations search; strings that are only a long run of
    digits are identifiers, matched as text instead. String values are scanned in one pass, joined by
    a separator that cannot form a range."""
    stripped = text.lstrip()
    if stripped[:1] in "[{":
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            data = None
        if data is not None:
            strings: list[str] = []
            numeric: list[float] = []
            lengths: list[int] = []
            money: list[float] = []
            stack: list[tuple[Any, bool]] = [(data, False)]
            while stack:
                x, is_money = stack.pop()
                if isinstance(x, dict):
                    stack.extend((v, is_money or _money_key(k)) for k, v in x.items())
                elif isinstance(x, list):
                    lengths.append(len(x))
                    stack.extend((v, is_money) for v in x)
                elif isinstance(x, str):
                    if not (x.isdigit() and len(x) >= 6) and any(c.isdigit() for c in x):
                        strings.append(x)
                        if "$" in x or "€" in x or "£" in x or "¥" in x:
                            money += _currency(x)
                elif isinstance(x, int | float) and not isinstance(x, bool):
                    numeric.append(float(x))
                    if is_money:
                        money.append(float(x))
            return (_quantities(_JOIN.join(strings)) if strings else []) + numeric, lengths, money
    return _quantities(text), [], _currency(text)


def _examples(text: str) -> set[str]:
    """Values a system prompt gives as examples ("IDs look like #W0000000", "e.g. jane@example.com").
    They show a format; they are not data, so they do not vouch for an argument."""
    low = text.lower()
    out: set[str] = set()
    for cue in _EXAMPLE_CUES:
        i = low.find(cue)
        while i >= 0:
            m = _EXAMPLE_VALUE.match(text, i + len(cue))
            if m:
                out.add(m.group(1).rstrip(".,'\""))
            i = low.find(cue, i + 1)
    return out


@functools.lru_cache(maxsize=4096)
def _money_key(key: object) -> bool:
    return bool(_MONEY_KEY.search(str(key)))


def _squeeze(text: str) -> str:
    """`text` without separators, for a fast first check of identifiers written with other spacing."""
    for ch in _SQUEEZE:
        if ch in text:
            text = text.replace(ch, "")
    return text


def _id_like(x: str) -> bool:
    return len(x) >= 4 and any(c.isdigit() for c in x) and " " not in x


def _records(text: str) -> dict[str, set[str]]:
    """Identifier -> the scalar fields beside it in its JSON object (one level of nesting included,
    so an order's record holds its items' names),
    for IDs held as values ({"item_id": "42..", "name": "Headphones"}) or as keys
    ({"credit_card_95..": {"brand": "mastercard", "last_four": "2478"}})."""
    if text.lstrip()[:1] not in "[{":
        return {}
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return {}
    out: dict[str, set[str]] = {}

    def scalars(d: dict[str, Any]) -> set[str]:
        vals: set[str] = set()
        for v in d.values():
            if isinstance(v, dict):
                items = list(v.values())
            elif isinstance(v, list):
                items = [y for x in v for y in (x.values() if isinstance(x, dict) else [x])]
            else:
                items = [v]
            for x in items:
                if isinstance(x, str | int | float) and not isinstance(x, bool):
                    t = norm(str(x))
                    if len(t) >= 3 and len(t) <= 60:
                        vals.add(t)
        return vals

    stack: list[Any] = [data]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            attrs = scalars(x)
            for k, v in x.items():
                if isinstance(v, str) and _id_like(v):
                    out.setdefault(norm(v).lstrip("#"), set()).update(attrs - {norm(v)})
                if _id_like(str(k)) and isinstance(v, dict):
                    out.setdefault(norm(str(k)).lstrip("#"), set()).update(scalars(v))
                if isinstance(v, dict | list):
                    stack.append(v)
        elif isinstance(x, list):
            stack.extend(x)
    return out


def _currency(text: str) -> list[float]:
    return [float(m.group(1).replace(",", "")) for m in _CURRENCY.finditer(text)]


def _quantities(text: str) -> list[float]:
    """Numbers in prose that are quantities. A bare run of five or more digits ("my zip is 19122",
    an order number) is an identifier: it can be found as text, but it does not vouch for an amount.
    Amounts in prose carry a separator, a decimal point, or a currency sign, or arrive as JSON numbers."""
    vals = scan_values(text)
    plain = {float(d) for d in _PLAIN_DIGITS.findall(text)}
    return [v for v in vals if v not in plain] if plain else vals
