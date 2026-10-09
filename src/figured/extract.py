"""Find the numbers in a piece of text, with their positions and meaning."""

from __future__ import annotations

import re
from dataclasses import dataclass

SCALES: dict[str, float] = {
    "thousand": 1e3,
    "k": 1e3,
    "million": 1e6,
    "mn": 1e6,
    "m": 1e6,
    "mm": 1e6,
    "billion": 1e9,
    "bn": 1e9,
    "b": 1e9,
    "trillion": 1e12,
    "tn": 1e12,
    "t": 1e12,
}
PERCENT_WORDS = frozenset({"%", "percent", "pct", "percentage points", "pp"})

_NUMBER = re.compile(
    r"""
    (?<![\w.])
    (?P<sign>[-−]\s?)?
    (?P<currency>[$€£¥])?
    (?P<body>
        \d+(?:\.\d+)?[eE][+-]?\d+
      | \d{1,3}(?:,\d{3})+(?:\.\d+)?
      | \d+(?:\.\d+)?
    )
    (?!\d)
    (?!(?:st|nd|rd|th)\b)
    (?:
        \s?(?P<pct>%)
      | \s?(?P<word>percentage\ points|percent|pct|pp|thousand|million|billion|trillion
                    |mn|mm|bn|tn|k|m|b|t)\b(?![-'][A-Za-z])
    )?
    (?![A-Za-z_])
    """,
    re.VERBOSE | re.IGNORECASE,
)
_START = re.compile(r"(?:[-−]\s?)?[$€£¥]?\d")
_RANGE_JOIN = re.compile(r"^\s*(?:to|and|or|-|–|—)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Figure:
    """One number found in the text."""

    literal: str
    value: float
    start: int
    end: int
    is_percent: bool = False
    is_currency: bool = False
    has_scale: bool = False
    range_partner: float | None = None

    @property
    def is_range(self) -> bool:
        return self.range_partner is not None

    @property
    def bounds(self) -> tuple[float, float]:
        other = self.range_partner if self.range_partner is not None else self.value
        lo, hi = sorted((abs(self.value), abs(other)))
        return lo, hi

    @property
    def looks_like_year(self) -> bool:
        body = self.literal.lstrip("-−$€£¥ ").strip()
        return body.isdigit() and len(body) == 4 and not self.is_percent and not self.has_scale


def extract_numbers(text: str) -> list[Figure]:
    """Return every number-like token in reading order.

    Handles thousands separators, decimals, scientific notation, currency symbols, scale
    words (thousand, million, k, bn, ...), percent markers, and ranges such as
    "40 to 50 million", where the scale of the second number is applied to the first.
    """
    out: list[Figure] = []
    pos = 0
    match, search = _NUMBER.match, _START.search
    while (start := search(text, pos)) is not None:
        at = start.start()
        m = match(text, at) or (match(text, start.end() - 1) if start.end() - 1 > at else None)
        if m is None:
            pos = start.end()
            continue
        pos = m.end()
        body = m.group("body").replace(",", "")
        value = float(body)
        word = (m.group("word") or "").lower()
        pct = bool(m.group("pct")) or word in PERCENT_WORDS
        scale = SCALES.get(word)
        if scale:
            value *= scale
        if m.group("sign"):
            value = -value
        out.append(
            Figure(
                literal=m.group(0).strip(),
                value=value,
                start=m.start(),
                end=m.end(),
                is_percent=pct,
                is_currency=bool(m.group("currency")),
                has_scale=scale is not None,
            )
        )
    return _apply_ranges(text, out)


def scan_values(text: str) -> list[float]:
    """The values `extract_numbers` would return, without building Figure objects. For hot paths."""
    vals: list[float] = []
    spans: list[tuple[int, int, float | None, bool]] = []
    pos = 0
    match, search, scales, pct_words = _NUMBER.match, _START.search, SCALES, PERCENT_WORDS
    while (start := search(text, pos)) is not None:
        at = start.start()
        m = match(text, at) or (match(text, start.end() - 1) if start.end() - 1 > at else None)
        if m is None:
            pos = start.end()
            continue
        pos = m.end()
        sign, body, pct, word = m.group("sign", "body", "pct", "word")
        v = float(body.replace(",", "") if "," in body else body)
        scale: float | None = None
        if word:
            w = word.lower()
            is_pct = w in pct_words
            scale = scales.get(w)
            if scale:
                v *= scale
        else:
            is_pct = pct is not None
        if sign:
            v = -v
        vals.append(v)
        spans.append((m.start(), pos, scale, is_pct))
    for i in range(len(spans) - 1):
        _, a_end, a_scale, a_pct = spans[i]
        b_start, _, b_scale, _ = spans[i + 1]
        if b_scale and not a_scale and not a_pct and _RANGE_JOIN.match(text[a_end:b_start]):
            vals[i] *= b_scale
    return vals


def _apply_ranges(text: str, figures: list[Figure]) -> list[Figure]:
    if len(figures) < 2:
        return figures
    fixed = list(figures)
    for i in range(len(fixed) - 1):
        a, b = fixed[i], fixed[i + 1]
        if not _RANGE_JOIN.match(text[a.end : b.start]):
            continue
        if b.has_scale and not a.has_scale and not a.is_percent:
            a = Figure(a.literal, a.value * _scale_of(b), a.start, a.end, False, a.is_currency, True)
        elif b.is_percent and not a.is_percent and not a.has_scale:
            a = Figure(a.literal, a.value, a.start, a.end, True, a.is_currency, False)
        if a.is_percent == b.is_percent and a.looks_like_year == b.looks_like_year and not a.looks_like_year:
            fixed[i] = Figure(
                a.literal, a.value, a.start, a.end, a.is_percent, a.is_currency, a.has_scale, b.value
            )
            fixed[i + 1] = Figure(
                b.literal, b.value, b.start, b.end, b.is_percent, b.is_currency, b.has_scale, a.value
            )
        else:
            fixed[i] = a
    return fixed


def _scale_of(fig: Figure) -> float:
    word = fig.literal.split()[-1].lower() if " " in fig.literal else _trailing_word(fig.literal)
    return SCALES.get(word, 1.0)


def _trailing_word(literal: str) -> str:
    m = re.search(r"[a-z]+$", literal, re.IGNORECASE)
    return m.group(0).lower() if m else ""
