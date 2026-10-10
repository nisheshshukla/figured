"""What kind of value an argument or a claim is, and how to pull values out of free text."""

from __future__ import annotations

import calendar
import datetime as dt
import functools
import re
from dataclasses import dataclass
from typing import Any, Literal

Kind = Literal["identifier", "email", "url", "date", "number", "phrase"]

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# anything shaped like an address, letters from any script: a lookalike with a Cyrillic letter in place
# of a Latin one must be checked against the context like any other email, not skipped as non-ASCII
_MAILBOX = re.compile(r"\S+@\S+\.\S+")
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
_SHORT_ISO = re.compile(r"(?<![\w-])(\d{2})-(\d{2})-(\d{2})(?!\w)")
_ISO_DATETIME = re.compile(
    r"^\d{4}-\d{1,2}-\d{1,2}[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\s*(?:Z|[+-]\d{2}:?\d{2}|UTC)?$",
    re.IGNORECASE,
)
_PERIOD_MONTH = re.compile(r"\b" + _MONTH + r"\b\.?(?:,?\s+(\d{4}))?", re.IGNORECASE)
_PERIOD_YEAR = re.compile(
    r"\b(?:in|for|during|of|since|throughout|from|year)\s+((?:19|20)\d{2})\b(?!\s*[-/]\d)", re.IGNORECASE
)
_PERIOD_RELATIVE = re.compile(r"\b(this|current|last|previous|past|next)\s+(month|year)\b", re.IGNORECASE)
_DAY_OF_MONTH = re.compile(
    r"\bthe\s+(\d{1,2})(?:st|nd|rd|th)?\s+of\s+(this|the|next|last)\s+month\b", re.IGNORECASE
)
_SLASH = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})(?![\d/])")
_YMD_SLASH = re.compile(r"(?<![\d/])(\d{4})/(\d{1,2})/(\d{1,2})(?![\d/])")
_DMY_DOT = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d|\.\d)")
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
_MONTH_END = re.compile(r"\bend of (?:the |this )?(next )?month\b", re.IGNORECASE)
_INTL_MONTHS = {
    name: i + 1
    for i, names in enumerate(
        [
            "enero janvier januar jänner janeiro gennaio",
            "febrero février fevrier februar fevereiro febbraio",
            "marzo mars märz maerz março marco",
            "abril avril aprile",
            "mayo mai maio maggio",
            "junio juin juni junho giugno",
            "julio juillet juli julho luglio",
            "agosto août aout",
            "septiembre setiembre septembre setembro settembre",
            "octubre octobre oktober outubro ottobre",
            "noviembre novembre",
            "diciembre décembre decembre dezember dezembro dicembre",
        ]
    )
    for name in names.split()
}
_INTL = re.compile(
    r"\b(\d{1,2})(?:\.|º|ª|er)?\s+(?:de\s+|di\s+)?("
    + "|".join(sorted(_INTL_MONTHS, key=len, reverse=True))
    + r")\b\.?(?:,?\s+(?:de\s+|del\s+)?(\d{4}))?",
    re.IGNORECASE,
)
_CJK = re.compile(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")

DateKey = tuple[int | None, int, int]
_MONTH3 = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_RELATIVE_HINTS = ("today", "tonight", "tomorrow", "yesterday", "day")
_UNIT_NAMES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
    "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
)  # fmt: skip
_UNITS = {w: i for i, w in enumerate(_UNIT_NAMES)}
_TENS = {
    w: 10 * (i + 2)
    for i, w in enumerate(("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"))
}
_SCALES = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_NUMBER_WORD = "|".join([*_UNITS, *_TENS, "hundred", *_SCALES])
_NUMBER_WORDS = re.compile(
    r"\b(?:" + _NUMBER_WORD + r")(?:(?:[\s-]+(?:and[\s-]+)?)(?:" + _NUMBER_WORD + r"))*\b", re.IGNORECASE
)
_NUMBER_HINTS = ("teen", "hundred", "thousand", "illion", *_TENS)


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
    if isinstance(value, str) and (hint is None or not (hint.get("format") or hint.get("enum"))):
        return _classify_plain(value)  # the hint carries nothing that changes the kind
    if (
        hint
        and isinstance(hint.get("enum"), list)
        and str(value).strip().lower() in {str(e).lower() for e in hint["enum"]}
    ):
        return None
    if isinstance(value, int | float):
        return "number"
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    fmt = (hint or {}).get("format")
    if fmt == "email" or ("@" in s and (EMAIL.fullmatch(s) or _MAILBOX.fullmatch(s))):
        return "email"
    if fmt in ("uri", "url") or ("://" in s and URL.fullmatch(s)):
        return "url"
    if fmt in ("date", "date-time"):
        return "date"
    return _classify_text(s)


@functools.lru_cache(maxsize=8192)
def _classify_plain(value: str) -> Kind | None:
    """`classify` for a string with no schema hint, cached: an agent repeats the same user ID, order
    ID, and dates across its calls."""
    s = value.strip()
    if not s:
        return None
    if "@" in s and (EMAIL.fullmatch(s) or _MAILBOX.fullmatch(s)):
        return "email"
    if "://" in s and URL.fullmatch(s):
        return "url"
    return _classify_text(s)


def _classify_text(s: str) -> Kind | None:
    has_digit = any(map(str.isdigit, s))
    if not has_digit:
        return None
    if len(s) <= 40 and is_date_literal(s):
        return "date"
    if s[0] in _NUMERIC_START and numeric_string(s) is not None:
        return "number"
    if len(s) > 80 or len(s.split()) > 6:
        return None
    return "phrase" if any(map(str.isspace, s)) else "identifier"


_NUMERIC_START = frozenset("0123456789+-$€£¥")


_NUMERIC = re.compile(r"^[-+]?[$€£¥]?\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")


def numeric_string(s: str) -> float | None:
    """The value of a string that is a quantity ("49.90", "$1,200", "12%", "7"), or None. A plain run
    of five or more digits, or one with a leading zero, is an identifier (a zip, an item ID), not a
    quantity."""
    s = s.strip()
    if not _NUMERIC.match(s):
        return None
    core = s.lstrip("+-")
    if core.isdigit() and (len(core) >= 5 or (len(core) > 1 and core[0] == "0")):
        return None
    try:
        return float(
            s.lstrip("+")
            .replace("$", "")
            .replace("€", "")
            .replace("£", "")
            .replace("¥", "")
            .replace(",", "")
            .rstrip("%")
            .strip()
        )
    except ValueError:
        return None


def is_date_literal(s: str) -> bool:
    s = s.strip()
    if not any(c in s for c in "-/.") and not any(mon in s.lower() for mon in _MONTH3):
        return False
    if _ISO_DATETIME.match(s):
        return True
    for rx in (_ISO, _SLASH, _YMD_SLASH, _DMY_DOT, _MONTH_DAY, _DAY_MONTH):
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


def parse_dates(text: str, as_of: dt.date | None = None, *, intl: bool = True) -> set[DateKey]:
    """Every calendar date mentioned in `text`, with relative words resolved against `as_of`. `intl`
    also reads day-month dates in Spanish, French, German, Portuguese, and Italian; it is off for tool
    results, which are almost always ISO, because it is the costliest pattern."""
    out: set[DateKey] = set()
    low = text.lower()
    has_digit_dash = "-" in text
    has_slash = "/" in text
    has_month = any(mon in low for mon in _MONTH3)
    for m in _ISO.finditer(text) if has_digit_dash else ():
        _add(out, int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for m in _SHORT_ISO.finditer(text) if has_digit_dash else ():
        _add(out, 2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for m in _YMD_SLASH.finditer(text) if has_slash else ():
        _add(out, int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for m in _DMY_DOT.finditer(text) if "." in text else ():
        _add(out, int(m.group(3)), int(m.group(2)), int(m.group(1)))
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
    for m in _INTL.finditer(text) if intl else ():
        _add(out, int(m.group(3)) if m.group(3) else None, _INTL_MONTHS[m.group(2).lower()], int(m.group(1)))
    for m in _CJK.finditer(text) if "月" in text else ():
        _add(out, int(m.group(1)) if m.group(1) else None, int(m.group(2)), int(m.group(3)))
    if as_of is not None and "of" in low and "month" in low:
        for m in _DAY_OF_MONTH.finditer(text):
            step = {"next": 1, "last": -1}.get(m.group(2).lower(), 0)
            y, mo = divmod(as_of.year * 12 + as_of.month - 1 + step, 12)
            _add(out, y, mo + 1, int(m.group(1)))
    if as_of is not None and "end of" in low:
        for m in _MONTH_END.finditer(text):
            first = (as_of.replace(day=1) + dt.timedelta(days=32 * (2 if m.group(1) else 1))).replace(day=1)
            last = first - dt.timedelta(days=1)
            out.add((last.year, last.month, last.day))
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


def number_words(text: str) -> list[float]:
    """Amounts written in English words: "two hundred fifty" -> 250, "twenty-five" -> 25. Only values
    above twelve; smaller ones are counts, handled separately."""
    low = text.lower()
    if not any(h in low for h in _NUMBER_HINTS):
        return []
    out: list[float] = []
    for m in _NUMBER_WORDS.finditer(low):
        total = current = 0
        for w in re.split(r"[\s-]+", m.group(0)):
            if w in _UNITS:
                current += _UNITS[w]
            elif w in _TENS:
                current += _TENS[w]
            elif w == "hundred":
                current = (current or 1) * 100
            elif w in _SCALES:
                total += (current or 1) * _SCALES[w]
                current = 0
        value = total + current
        if value > 12:
            out.append(float(value))
    return out


def date_periods(text: str, as_of: dt.date | None = None) -> set[DateKey]:
    """The first and last days of periods a user names, and the day after each, which search tools
    take as range bounds: "August" -> 08-01, 08-31, 09-01; "in 2024" -> 2024-01-01, 2024-12-31,
    2025-01-01; "last month" against the reference date. A month without a year has no year."""
    out: set[DateKey] = set()
    low = text.lower()

    def month(y: int | None, mo: int) -> None:
        last = calendar.monthrange(y or 2024, mo)[1]
        _add(out, y, mo, 1)
        _add(out, y, mo, last)
        if y is None and mo == 2:
            _add(out, None, 2, 28)
        ny, nm = (y + 1 if y else None, 1) if mo == 12 else (y, mo + 1)
        _add(out, ny, nm, 1)

    def year(y: int) -> None:
        _add(out, y, 1, 1)
        _add(out, y, 12, 31)
        _add(out, y + 1, 1, 1)

    if any(mon in low for mon in _MONTH3):
        for m in _PERIOD_MONTH.finditer(text):
            name = m.group(1).lower()
            if (
                name == "may"
                and not m.group(2)
                and not re.search(r"\b(?:in|of|for|during|since|until|through)\s+$", low[: m.start()])
            ):
                continue
            month(int(m.group(2)) if m.group(2) else None, MONTHS[name[:3]])
    for m in _PERIOD_YEAR.finditer(text):
        year(int(m.group(1)))
    if as_of is not None and ("month" in low or "year" in low):
        for m in _PERIOD_RELATIVE.finditer(text):
            step = {"last": -1, "previous": -1, "past": -1, "next": 1}.get(m.group(1).lower(), 0)
            if m.group(2).lower() == "month":
                y, mo = divmod(as_of.year * 12 + as_of.month - 1 + step, 12)
                month(y, mo + 1)
            else:
                year(as_of.year + step)
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
    for rx in (_ISO, _SLASH, _MONTH_DAY, _DAY_MONTH, _INTL, _CJK):
        for m in rx.finditer(text):
            if free(m.start(), m.end()):
                keys = parse_dates(m.group(0))
                if keys:
                    found.append(TextValue("date", m.group(0), keys, m.start(), m.end()))
                    taken.append((m.start(), m.end()))
    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        if not looks_like_identifier(tok) or not free(m.start(), m.end()):
            continue
        if not _EXAMPLE.search(text[max(0, m.start() - 24) : m.start()]):
            found.append(TextValue("identifier", tok, tok, m.start(), m.end()))
            taken.append((m.start(), m.end()))
    found.sort(key=lambda v: v.start)
    return found


def spans_of(values: list[TextValue]) -> list[tuple[int, int]]:
    return [(v.start, v.end) for v in values]
