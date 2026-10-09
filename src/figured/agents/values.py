"""What kind of value an argument or a claim is, and how to pull values out of free text."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Literal

Kind = Literal["identifier", "email", "url", "date", "number", "phrase"]

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
URL = re.compile(r"https?://[^\s<>\"'`)\]}]+", re.IGNORECASE)
_TOKEN = re.compile(r"#?[A-Za-z0-9][A-Za-z0-9_\-]*[A-Za-z0-9]|#?[A-Za-z0-9]")
_MEASURE = re.compile(r"^\d+(?:\.\d+)?-[A-Za-z]+$|^\d+(?:\.\d+)?[A-Za-z]*(?:-\d+(?:\.\d+)?[A-Za-z]*)+$")
_EXAMPLE = re.compile(r"(?:e\.g\.|for example|for instance|such as|like)[\s:,(\"']*$", re.IGNORECASE)
_ORDINAL_OR_TIME = re.compile(
    r"^\d+(?:st|nd|rd|th|am|pm|h|hr|hrs|min|mins|s|ms|k|m|b|x|kg|g|lb|lbs|cm|mm|km|mi|ft|in)$", re.I
)

MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ]
    )
    for m in names
}
_MONTH = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_SLASH = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})(?![\d/])")
_MONTH_DAY = re.compile(_MONTH + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4}))?", re.IGNORECASE)
_DAY_MONTH = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MONTH + r"\b\.?(?:,?\s+(\d{4}))?", re.IGNORECASE
)
_DAY_LIST = re.compile(
    r"(?:\s*(?:,|or|and|to|through|-|–))+\s*(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b(?![/:\d]|\.\d)"
)
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_RELATIVE = re.compile(
    r"\b(today|tonight|tomorrow|yesterday|day after tomorrow|" + "|".join(WEEKDAYS) + r")\b", re.IGNORECASE
)

DateKey = tuple[int | None, int, int]
_MONTH3 = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_RELATIVE_HINTS = ("today", "tonight", "tomorrow", "yesterday", "day")


@dataclass(frozen=True, slots=True)
class TextValue:
    kind: Kind
    literal: str
    value: Any
    start: int
    end: int


def classify(value: Any, hint: dict[str, Any] | None = None) -> Kind | None:
    """The kind of a tool-argument leaf, or None when it is not something that needs a source."""
    if value is None or isinstance(value, bool):
        return None
    if hint and "enum" in hint:
        return None
    if isinstance(value, int | float):
        return "number"
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    fmt = (hint or {}).get("format")
    if fmt == "email" or EMAIL.fullmatch(s):
        return "email"
    if fmt in ("uri", "url") or URL.fullmatch(s):
        return "url"
    if fmt in ("date", "date-time") or is_date_literal(s):
        return "date"
    if len(s) > 80 or len(s.split()) > 6:
        return None
    if not any(ch.isdigit() for ch in s):
        return None
    return "phrase" if any(ch.isspace() for ch in s) else "identifier"


def is_date_literal(s: str) -> bool:
    s = s.strip()
    for rx in (_ISO, _SLASH, _MONTH_DAY, _DAY_MONTH):
        m = rx.match(s)
        if m and m.end() >= len(s.rstrip(".")) - 9:
            return bool(parse_dates(s))
    return False


def looks_like_identifier(token: str) -> bool:
    core = token.lstrip("#")
    if len(core) < 5 or not any(ch.isdigit() for ch in core):
        return False
    if _ORDINAL_OR_TIME.match(core) or _MEASURE.match(core):
        return False
    digits = sum(ch.isdigit() for ch in core)
    upper = sum(ch.isupper() for ch in core)
    if "_" in core or token.startswith("#"):
        return True
    if not any(ch.isalpha() for ch in core):
        return False
    return digits >= 2 or upper >= 2


def parse_dates(text: str, as_of: dt.date | None = None) -> set[DateKey]:
    """Every calendar date mentioned in `text`, with relative words resolved against `as_of`."""
    out: set[DateKey] = set()
    low = text.lower()
    has_digit_dash = "-" in text
    has_slash = "/" in text
    has_month = any(mon in low for mon in _MONTH3)
    for m in _ISO.finditer(text) if has_digit_dash else ():
        _add(out, int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for m in _SLASH.finditer(text) if has_slash else ():
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        year = y + 2000 if y < 100 else y
        _add(out, year, a, b)
        if a != b:
            _add(out, year, b, a)
    for m in _MONTH_DAY.finditer(text) if has_month else ():
        month = MONTHS[m.group(1).lower()[:3]]
        named_year = int(m.group(3)) if m.group(3) else None
        _add(out, named_year, month, int(m.group(2)))
        pos = m.end()
        while more := _DAY_LIST.match(text, pos):
            _add(out, named_year, month, int(more.group(1)))
            pos = more.end()
    for m in _DAY_MONTH.finditer(text) if has_month else ():
        _add(out, int(m.group(3)) if m.group(3) else None, MONTHS[m.group(2).lower()[:3]], int(m.group(1)))
    if as_of is not None and any(w in low for w in _RELATIVE_HINTS):
        for m in _RELATIVE.finditer(text):
            word = m.group(1).lower()
            if word in ("today", "tonight"):
                days = [0]
            elif word == "tomorrow":
                days = [1]
            elif word == "yesterday":
                days = [-1]
            elif word == "day after tomorrow":
                days = [2]
            else:
                ahead = (WEEKDAYS.index(word) - as_of.weekday()) % 7 or 7
                days = [ahead, ahead + 7, ahead - 7]
            for d in days:
                when = as_of + dt.timedelta(days=d)
                out.add((when.year, when.month, when.day))
    return out


def _add(out: set[DateKey], y: int | None, m: int, d: int) -> None:
    if 1 <= m <= 12 and 1 <= d <= 31:
        out.add((y, m, d))


def extract_text_values(text: str) -> list[TextValue]:
    """Emails, URLs, dates, and identifier-like tokens in free text, in reading order, without overlaps."""
    found: list[TextValue] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in taken)

    for m in URL.finditer(text):
        lit = m.group(0).rstrip(".,;:!?")
        found.append(TextValue("url", lit, lit, m.start(), m.start() + len(lit)))
        taken.append((m.start(), m.start() + len(lit)))
    for m in EMAIL.finditer(text):
        if free(m.start(), m.end()):
            found.append(TextValue("email", m.group(0), m.group(0), m.start(), m.end()))
            taken.append((m.start(), m.end()))
    for rx in (_ISO, _SLASH, _MONTH_DAY, _DAY_MONTH):
        for m in rx.finditer(text):
            if free(m.start(), m.end()):
                keys = parse_dates(m.group(0))
                if keys:
                    found.append(TextValue("date", m.group(0), keys, m.start(), m.end()))
                    taken.append((m.start(), m.end()))
    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        if _EXAMPLE.search(text[max(0, m.start() - 24) : m.start()]):
            continue
        if free(m.start(), m.end()) and looks_like_identifier(tok):
            found.append(TextValue("identifier", tok, tok, m.start(), m.end()))
            taken.append((m.start(), m.end()))
    found.sort(key=lambda v: v.start)
    return found


def spans_of(values: list[TextValue]) -> list[tuple[int, int]]:
    return [(v.start, v.end) for v in values]
