"""Deterministic verification: the code, not the model, decides which findings go to the lawyer.

`verify` is pure: no I/O, no LLM. It checks every quote against the masked clause text, reads
numbers and units from the verified quote and from the proposal itself, compares them with the
rule's `check` and writes `needs_review` / `review_reasons` from scratch on every call.

Design (`docs/agent_design.md` §5 "Doğrulama katmanı"):

- `extract` is the one reader of values. It returns the values it read, the value expressions
  it could not read and the span of every expression. A number written in the text (digits or
  number words) followed (directly or one word later, with no sentence boundary between) by a word
  that starts with a unit stem of the rule's kind is never silently dropped: it becomes a value or
  an unreadable expression. A unit never crosses a sentence boundary: ".", "!" or "?", whitespace,
  then a word written upper case first and lower case next ("5. Ayrıca"). Nothing else is a
  boundary: not a comma, not ",-", not "." before a lower-case word ("45. iş gününde"), not a
  word written all in upper case. A masking placeholder ("[TCKN_1]") is one word, never a
  number: a number the masker took for personal data is neither a value nor an unreadable
  expression. `masked_value_in_clause` makes
  up for it: a `compliant` finding whose clause holds a TCKN or TELEFON placeholder is queued.
  A word that starts with a number word but is not one ("beşer", "üçüncü"), standing where the
  number would be, is an unreadable expression too.
- What continues a number (a multiplier or fraction word, a slash, a space-grouped digit run) is
  folded into the number's token when the text is tokenized, and `_readable` - the one gate every
  value passes - never accepts such a token. No reading path can read part of a number.
- A sign or prefix belongs only to the number next to it, with at most a space between.
- A quote match may not cut a word, a number or a value expression of any kind: an occurrence
  that does is skipped.
- `align` is the one duration conversion. Within a family (gün/hafta, ay/yıl) it is exact; across
  families it turns ay/yıl into a day interval (28n-31n, 365n-366n). The check, the "same value?"
  test, the cross-check against the model's value and the proposal check all go through it.
- Comparisons are on intervals; an exact value is a one-point interval.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, get_args

from src.models import (
    Check,
    CheckOp,
    CheckUnit,
    Clause,
    CodeCheck,
    Finding,
    ReviewReason,
    Rule,
)
from src.prepare.pii import PLACEHOLDER
from src.text import ALL_STEPS, normalize

Kind = Literal["duration", "percent", "amount"]
Interval = tuple[float, float]

# Reasons are written in the order of the signal table, which is the order of the Literal.
_REASON_ORDER: tuple[ReviewReason, ...] = get_args(ReviewReason)
_CHECK_UNITS: tuple[CheckUnit, ...] = get_args(CheckUnit)

_KIND_OF_UNIT: dict[CheckUnit, Kind] = {
    "gün": "duration",
    "hafta": "duration",
    "ay": "duration",
    "yıl": "duration",
    "%": "percent",
    "TL": "amount",
}

# --- Duration conversion tables (used only by `align`) -------------------------------------------
_DAY_FAMILY: dict[CheckUnit, float] = {"gün": 1.0, "hafta": 7.0}
_MONTH_FAMILY: dict[CheckUnit, float] = {"ay": 1.0, "yıl": 12.0}
_DAYS_IN: dict[CheckUnit, Interval] = {"ay": (28.0, 31.0), "yıl": (365.0, 366.0)}

# --- Closed list of duration forms --------------------------------------------------------------
# Bare stem, -lik, possessive, dative, locative, ablative, genitive, plural, possessive + case,
# copula.
_DURATION_FORM_LISTS: dict[CheckUnit, tuple[str, ...]] = {
    "gün": (
        "gün", "günlük", "günü", "güne", "günde", "günden", "günün", "günler",
        "gününe", "gününde", "gününden", "gündür",
    ),
    "hafta": (
        "hafta", "haftalık", "haftası", "haftaya", "haftada", "haftadan", "haftanın",
        "haftalar", "haftasına", "haftasında", "haftasından", "haftadır",
    ),
    "ay": (
        "ay", "aylık", "ayı", "aya", "ayda", "aydan", "ayın", "aylar",
        "ayına", "ayında", "ayından", "aydır",
    ),
    "yıl": (
        "yıl", "yıllık", "yılı", "yıla", "yılda", "yıldan", "yılın", "yıllar",
        "yılına", "yılında", "yılından", "yıldır",
    ),
}  # fmt: skip
_DURATION_FORMS: dict[str, CheckUnit] = {
    form: unit for unit, forms in _DURATION_FORM_LISTS.items() for form in forms
}
_DURATION_STEMS: tuple[str, ...] = ("gün", "hafta", "ay", "yıl")
# Time units the code does not convert: after a number they are unreadable, never dropped.
_OTHER_TIME_STEMS: tuple[str, ...] = ("sene", "saat", "dakika")

_TL_SYMBOL = "₺"
_TL_WORDS = frozenset({"tl", "try"})
_FOREIGN_SYMBOLS = frozenset({"$", "€", "£", "¥"})
_FOREIGN_STEMS: tuple[str, ...] = (
    "usd", "eur", "avro", "dolar", "gbp", "sterlin", "chf", "jpy", "frank", "frang",
)  # fmt: skip

# The only word that may stand between a number and its unit, per kind.
_BRIDGES: dict[Kind, frozenset[str]] = {
    "duration": frozenset({"takvim"}),
    "amount": frozenset({"türk"}),
    "percent": frozenset(),
}
# A unit-less number joined by one of these to a number with a unit gets that unit.
_CONNECTORS = frozenset({"ila", "ile", "veya", "-"})
_MAX_GROUP = 6  # longest parenthesized group skipped between a number and its unit

# --- Number words ---------------------------------------------------------------------------------
_ONES: dict[str, int] = {
    "bir": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5, "altı": 6, "yedi": 7, "sekiz": 8, "dokuz": 9,
}  # fmt: skip
_TENS: dict[str, int] = {
    "on": 10, "yirmi": 20, "otuz": 30, "kırk": 40, "elli": 50,
    "altmış": 60, "yetmiş": 70, "seksen": 80, "doksan": 90,
}  # fmt: skip
_HUNDRED = "yüz"
_PERCENT_WORD = "yüzde"
_NUMBER_WORDS: tuple[str, ...] = (*_ONES, *_TENS, _HUNDRED)
# A word starting with one of these right after a number continues it ("1,5 milyon", "bir buçuk",
# "1 milyonluk"); after the bare word itself a following number belongs to it too ("1 milyon 500
# bin", "iki virgül beş").
_MULTIPLIERS: tuple[str, ...] = (
    "bin", "milyon", "milyar", "trilyon", "mn", "mln", "mlr",
    "buçuk", "yarım", "çeyrek", "virgül", "nokta",
)  # fmt: skip
# These also stand for a number with nothing before them ("bin gün", "yarım yıl").
_LEADING_MULTIPLIERS = frozenset({"bin", "milyon", "milyar", "trilyon", "yarım", "çeyrek"})
# Digits joined by a slash or the fraction slash U+2044 (NFKC turns "½" into 1, U+2044, 2).
_SLASH_GAP_RE = re.compile(r" ?[/\u2044] ?")
# A later group of a space-grouped number: exactly three digits.
_GROUP_RE = re.compile(r"\d{3}(?!\d)")
# Normalized text has every whitespace run collapsed to one space: "at most a space between".
_SPACE_GAPS = ("", " ")
# Marks that end a sentence; whitespace and an upper-case word must follow (`_sentence_start`).
_SENTENCE_ENDS = frozenset(".!?")

# A masking placeholder is one word; a digit run is the longest sequence of digits, dots and
# commas (agent_design.md §5).
_TOKEN_RE = re.compile(
    r"(?P<placeholder>"
    + PLACEHOLDER.pattern
    + r")|(?P<number>\d[\d.,]*)|(?P<word>[^\W\d_]+)|(?P<symbol>[%()\-$"
    + _TL_SYMBOL
    + "€£¥])"
)
# Placeholder kinds that can stand for a number taken for personal data (masked_value_in_clause).
_NUMBER_PLACEHOLDERS = ("[TCKN_", "[TELEFON_")
# Dot is the thousands separator, comma the decimal separator, in every kind.
_DIGITS_RE = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?")
# A number as a quote match may not cut it: digits joined by single dots or commas.
_NUMBER_SPAN_RE = re.compile(r"\d+(?:[.,]\d+)*")


@dataclass(frozen=True)
class Quantity:
    amount: float
    unit: CheckUnit


@dataclass(frozen=True)
class Extraction:
    """Values read from a text, in text order, and the value expressions that could not be read.

    `spans` holds the start and end, in the normalized text, of every expression (read and
    unreadable: prefix, number, gap word and unit). Quote matching uses it; it is not part of
    equality, which compares what was read.
    """

    values: tuple[Quantity, ...]
    unreadable: tuple[str, ...]
    spans: tuple[tuple[int, int], ...] = field(default=(), compare=False)


@dataclass(frozen=True)
class _Token:
    kind: Literal["number", "word", "symbol"]
    text: str  # lower-cased with Turkish rules
    start: int
    end: int
    value: float | None = None  # number tokens: None when the digits or words are malformed
    spelled: bool = False  # number written in words
    # Digit run ending in "." or ",": the run does not fit the number format. Its value (without
    # the last character) is used only where no unit follows ("%15." at a sentence end); followed
    # by a unit ("15. günü") the run is an unreadable expression.
    trailing: bool = False
    # Word that starts with a number word but is not one ("beşer", "birkaç", "üçüncü").
    inflected: bool = False
    # Number token that took in what continues it ("1,5 milyon", "1/2", "1 000"): never read.
    continued: bool = False
    # Nothing but at most a space separates this token from the one before it.
    joined_left: bool = False
    # A sentence boundary stands right before this token: it starts a new sentence.
    boundary_left: bool = False


# --- Conversion and comparison --------------------------------------------------------------------


def align(a: Quantity, b: Quantity) -> tuple[Interval, Interval]:
    """The one duration conversion: express `a` and `b` in a common unit, as intervals.

    Same unit, or both gün/hafta, or both ay/yıl: exact points. One ay/yıl and one gün/hafta: both
    in days, ay = 28-31 and yıl = 365-366 days. Units of different kinds raise `ValueError`.
    """
    if a.unit == b.unit:
        return (a.amount, a.amount), (b.amount, b.amount)
    if _KIND_OF_UNIT[a.unit] != "duration" or _KIND_OF_UNIT[b.unit] != "duration":
        raise ValueError(f"Farklı türden birimler karşılaştırılamaz: {a.unit} / {b.unit}.")
    if a.unit in _MONTH_FAMILY and b.unit in _MONTH_FAMILY:
        x, y = a.amount * _MONTH_FAMILY[a.unit], b.amount * _MONTH_FAMILY[b.unit]
        return (x, x), (y, y)

    def in_days(q: Quantity) -> Interval:
        if q.unit in _DAY_FAMILY:
            d = q.amount * _DAY_FAMILY[q.unit]
            return d, d
        lo, hi = _DAYS_IN[q.unit]
        return q.amount * lo, q.amount * hi

    return in_days(a), in_days(b)


def _compare(value: Interval, op: CheckOp, threshold: Interval) -> bool | None:
    """True / False when every point of both intervals agrees, None otherwise."""
    v_lo, v_hi = value
    t_lo, t_hi = threshold
    if op == ">=":
        return True if v_lo >= t_hi else False if v_hi < t_lo else None
    if op == ">":
        return True if v_lo > t_hi else False if v_hi <= t_lo else None
    if op == "<=":
        return True if v_hi <= t_lo else False if v_lo > t_hi else None
    if op == "<":
        return True if v_hi < t_lo else False if v_lo >= t_hi else None
    # "==": endpoints are not enough; a point inside an interval is neither equal nor unequal.
    if v_lo == v_hi == t_lo == t_hi:
        return True
    if v_hi < t_lo or t_hi < v_lo:
        return False
    return None


def _evaluate(values: Sequence[Quantity], check: Check) -> bool | None:
    """The code's verdict on the check over every value; None when the verdicts differ."""
    threshold = Quantity(check.value, check.unit)
    outcomes: set[bool | None] = set()
    for value in values:
        value_iv, threshold_iv = align(value, threshold)
        outcomes.add(_compare(value_iv, check.op, threshold_iv))
    if len(outcomes) == 1:
        return outcomes.pop()
    return None


def _same(a: Quantity, b: Quantity) -> bool:
    """Same value after conversion: equal points, or a point inside the other's interval."""
    ia, ib = align(a, b)
    a_point, b_point = ia[0] == ia[1], ib[0] == ib[1]
    if a_point and b_point:
        return ia == ib
    if a_point:
        return ib[0] <= ia[0] <= ib[1]
    if b_point:
        return ia[0] <= ib[0] <= ia[1]
    return ia == ib


def _single(extraction: Extraction) -> bool:
    """At least one value, nothing unreadable, and every pair of values the same."""
    vals = extraction.values
    if not vals or extraction.unreadable:
        return False
    return all(_same(a, b) for i, a in enumerate(vals) for b in vals[i + 1 :])


# --- Extraction -----------------------------------------------------------------------------------


def _lower(text: str) -> str:
    """Lower-case with Turkish rules (İ -> i, I -> ı), one character in, one character out."""
    out: list[str] = []
    for ch in text:
        if ch == "İ":
            out.append("i")
        elif ch == "I":
            out.append("ı")
        else:
            low = ch.lower()
            out.append(low if len(low) == 1 else ch)
    return "".join(out)


def _parse_digits(raw: str) -> float | None:
    if not _DIGITS_RE.fullmatch(raw):
        return None
    ascii_digits = "".join(str(unicodedata.decimal(c)) if c.isdigit() else c for c in raw)
    return float(ascii_digits.replace(".", "").replace(",", "."))


def _gap(norm: str, left: _Token, right: _Token) -> str:
    """What separates two tokens. A separator ending a digit run ("2," in "Ek-2, 225") is
    punctuation, so it is part of the gap, not of the number."""
    return norm[left.end - left.trailing : right.start]


def _spelled_number(norm: str, raw: Sequence[_Token], i: int) -> tuple[float | None, int] | None:
    """Read 1-999 written in words starting at `i`; returns the value and the next index.

    The words of one number are separated by at most a space: across punctuation ("Madde Kırk.
    Beş gün") they are two numbers. When the word in the next open place (tens after hundreds,
    ones after tens) starts with a number word but is not one ("on beşi", "yirmi beşinden"), the
    number cannot be read: the value is None and the inflected word belongs to the number.
    """

    def word(k: int) -> str | None:
        if k >= len(raw) or raw[k].kind != "word":
            return None
        if k > i and _gap(norm, raw[k - 1], raw[k]) not in _SPACE_GAPS:
            return None
        return raw[k].text

    j, total = i, 0
    open_places: tuple[dict[str, int], ...] = ()
    w = word(j)
    if w is not None and w in _ONES and word(j + 1) == _HUNDRED:
        total, j = _ONES[w] * 100, j + 2
        open_places = (_TENS, _ONES)
    elif w == _HUNDRED:
        total, j = 100, j + 1
        open_places = (_TENS, _ONES)
    w = word(j)
    if w is not None and w in _TENS:
        total, j = total + _TENS[w], j + 1
        open_places = (_ONES,)
    w = word(j)
    if w is not None and w in _ONES:
        total, j = total + _ONES[w], j + 1
        open_places = ()
    if j == i:
        return None
    w = word(j)
    if w is not None and any(w.startswith(tuple(place)) for place in open_places):
        return None, j + 1
    return float(total), j


def _superscript(text: str) -> bool:
    """The text holds a superscript or subscript character ("30¹": a footnote mark that NFKC
    turns into a digit joined to the number)."""
    return any(unicodedata.decomposition(c).startswith(("<super>", "<sub>")) for c in text)


def _tokenize(text: str) -> tuple[str, list[_Token]]:
    normalized = normalize(text, ALL_STEPS)
    norm = normalized.text
    # Placeholders are upper case, so the tokens are found in `norm`; their text is taken from the
    # lowered copy, which has the same length.
    lowered = _lower(norm)
    raw: list[_Token] = []
    for m in _TOKEN_RE.finditer(norm):
        group = m.lastgroup
        if group == "placeholder":
            raw.append(_Token("word", lowered[m.start() : m.end()], m.start(), m.end()))
        elif group == "number":
            digits = m.group()
            value = _parse_digits(digits)
            trailing = False
            if value is None and digits[-1] in ".,":
                value = _parse_digits(digits[:-1])
                trailing = value is not None
            src_start, src_end = normalized.span(m.start(), m.end())
            if _superscript(text[src_start:src_end]):
                # The digits written are not the digits read: the run is malformed.
                value, trailing = None, False
            raw.append(_Token("number", digits, m.start(), m.end(), value, trailing=trailing))
        elif group == "word":
            word = lowered[m.start() : m.end()]
            inflected = (
                word.startswith(_NUMBER_WORDS)
                and word not in _NUMBER_WORDS
                and word != _PERCENT_WORD
            )
            raw.append(_Token("word", word, m.start(), m.end(), inflected=inflected))
        else:
            raw.append(_Token("symbol", m.group(), m.start(), m.end()))
    tokens: list[_Token] = []
    i = 0
    while i < len(raw):
        spelled = _spelled_number(norm, raw, i)
        if spelled is None:
            tokens.append(raw[i])
            i += 1
            continue
        value, j = spelled
        text_ = " ".join(t.text for t in raw[i:j])
        tokens.append(_Token("number", text_, raw[i].start, raw[j - 1].end, value, spelled=True))
        i = j
    joined = _join_continuations(norm, lowered, tokens)
    out: list[_Token] = []
    for k, tok in enumerate(joined):
        if k == 0:
            out.append(tok)
            continue
        out.append(
            replace(
                tok,
                joined_left=_gap(norm, joined[k - 1], tok) in _SPACE_GAPS,
                boundary_left=_sentence_start(norm, tok),
            )
        )
    return norm, out


def _sentence_start(norm: str, tok: _Token) -> bool:
    """A sentence boundary stands right before `tok` (agent_design.md §5).

    ".", "!" or "?", then whitespace (one space once normalized, a line break included), then a
    word of two or more letters written upper case first and lower case next ("Ayrıca", "İş"),
    read from the normalized text, which keeps the case. A single capital letter, a word all in
    upper case ("AYRICA") and a word in lower case ("45. iş") start no sentence: the old reading
    stands, on the safe side.
    """
    return (
        tok.start >= 2
        and norm[tok.start - 2] in _SENTENCE_ENDS
        and norm[tok.start - 1] == " "
        and tok.end - tok.start >= 2
        and norm[tok.start].isupper()
        and norm[tok.start + 1].islower()
    )


def _join_continuations(norm: str, lowered: str, tokens: Sequence[_Token]) -> list[_Token]:
    """Fold what continues a number into the number's token, marked `continued` (never read).

    A number continues into a multiplier or fraction word right after it ("1,5 milyon", "bir
    buçuk"), into digits joined by "/" or U+2044 ("1/2") and into space-grouped digits whose later
    groups have exactly three digits ("1 000 000"). Whitespace is collapsed by normalization, so
    one space stands for any whitespace run, a no-break space included. A punctuation mark ends
    the number ("Ek-2, 225": two numbers).

    A multiplier or fraction word with no number before it ("bin beş yüz gün", "bin 500 gün",
    "yarım yıl", "bin TL") is a number too, with the number after it folded in: it is never read,
    and what follows it is not read as a number of its own.
    """
    out: list[_Token] = []
    i = 0
    while i < len(tokens):
        j = i
        leading = tokens[i].kind == "word" and tokens[i].text in _LEADING_MULTIPLIERS
        while (tokens[i].kind == "number" or leading) and j + 1 < len(tokens):
            cur, nxt = tokens[j], tokens[j + 1]
            gap = _gap(norm, cur, nxt)
            digits = cur.kind == nxt.kind == "number" and not cur.spelled and not nxt.spelled
            if digits and (
                _SLASH_GAP_RE.fullmatch(gap) or (gap == " " and _GROUP_RE.match(nxt.text))
            ):
                j += 1
            elif (
                cur.kind == "number"
                and nxt.kind == "word"
                and gap in _SPACE_GAPS
                and nxt.text.startswith(_MULTIPLIERS)
            ):
                j += 1
                after = tokens[j + 1] if j + 1 < len(tokens) else None
                if (
                    nxt.text in _MULTIPLIERS
                    and after is not None
                    and after.kind == "number"
                    and norm[nxt.end : after.start] in _SPACE_GAPS
                ):
                    j += 1
            elif j == i and leading and nxt.kind == "number" and gap in _SPACE_GAPS:
                j += 1
            else:
                break
        if j == i and not leading:
            out.append(tokens[i])
        else:
            first, last = tokens[i], tokens[j]
            out.append(
                _Token(
                    "number",
                    lowered[first.start : last.end],
                    first.start,
                    last.end,
                    first.value,
                    spelled=first.spelled,
                    trailing=last.trailing,
                    continued=True,
                )
            )
        i = j + 1
    return out


_Prefix = Literal["%", "TL", "foreign"]


def _prefix(tokens: Sequence[_Token], i: int) -> _Prefix | None:
    """The sign or prefix word written right before token `i`, with at most a space between.

    "%" / "yüzde" is a percent prefix, "₺" / "TL" an amount prefix; another currency's sign, code
    or word ("$", "USD", "EUR", "dolar") is a foreign amount prefix. Across punctuation a sign
    belongs to no number ("1.000 ₺, 30 gün": 30 has no prefix).
    """
    if i == 0 or not tokens[i].joined_left:
        return None
    prev = tokens[i - 1]
    if prev.kind == "symbol":
        if prev.text == "%":
            return "%"
        if prev.text == _TL_SYMBOL:
            return "TL"
        return "foreign" if prev.text in _FOREIGN_SYMBOLS else None
    if prev.kind == "word":
        if prev.text == _PERCENT_WORD:
            return "%"
        if prev.text in _TL_WORDS:
            return "TL"
        if prev.text.startswith(_FOREIGN_STEMS):
            return "foreign"
    return None


def _is_prefix(tokens: Sequence[_Token], j: int) -> bool:
    """Token `j` is the sign or prefix word of the number right after it."""
    return (
        j + 1 < len(tokens)
        and tokens[j + 1].kind == "number"
        and _prefix(tokens, j + 1) is not None
    )


def _classify(
    tokens: Sequence[_Token], j: int, kind: Kind
) -> tuple[Literal["read", "unreadable"], CheckUnit | None] | None:
    """Is token `j` a unit of `kind` after a number: readable, unreadable, or not a unit at all.

    A sign is a unit only with at most a space before it ("1.000, ₺" has none). A sign right after
    a number is that number's unit even when another number follows it ("1.000 ₺ 30 gün",
    "15% 30 gün"); `_unit_after` decides whether a sign one word later is a prefix of the next
    number instead.
    """
    tok = tokens[j]
    if tok.kind == "symbol" and not tok.joined_left:
        return None
    if kind == "duration":
        if tok.kind == "word" and tok.text.startswith(_DURATION_STEMS):
            unit = _DURATION_FORMS.get(tok.text)
            return ("read", unit) if unit else ("unreadable", None)
        if tok.kind == "word" and tok.text.startswith(_OTHER_TIME_STEMS):
            return "unreadable", None
        return None
    if kind == "amount":
        if tok.kind == "symbol":
            if tok.text == _TL_SYMBOL:
                return "read", "TL"
            return ("unreadable", None) if tok.text in _FOREIGN_SYMBOLS else None
        if tok.kind == "word":
            if tok.text in _TL_WORDS or tok.text.startswith("lira"):
                return "read", "TL"
            if tok.text.startswith(("tl", *_FOREIGN_STEMS)):
                return "unreadable", None
        return None
    # percent: the sign belongs before the number; after it ("15%") it cannot be read.
    if tok.kind == "symbol" and tok.text == "%":
        return "unreadable", None
    return None


def _crosses(tokens: Sequence[_Token], start: int, end: int) -> bool:
    """A sentence boundary stands before one of the tokens `start`..`end`."""
    return any(tokens[k].boundary_left for k in range(start, end + 1))


def _unit_after(
    tokens: Sequence[_Token], j: int, kind: Kind
) -> tuple[Literal["read", "unreadable"], CheckUnit | None, int] | None:
    """The unit that follows a number from index `j`: directly or one word later.

    Sentence boundaries are not looked at here; the callers drop a unit across one (`_crosses`).
    """
    while j < len(tokens) and tokens[j].kind == "symbol" and tokens[j].text == ")":
        j += 1
    if j >= len(tokens):
        return None
    first = _classify(tokens, j, kind)
    if first is not None:
        # Across punctuation a prefix belongs to the number after it ("Ek-2, TL 187.203").
        if not tokens[j].joined_left and _is_prefix(tokens, j):
            return None
        return first[0], first[1], j
    # The one word between a number and its unit is a word; a number written in digits is not.
    if j + 1 >= len(tokens) or (tokens[j].kind == "number" and not tokens[j].spelled):
        return None
    # One word later, a sign directly followed by a number is that number's ("500 ila ₺1.000").
    if _is_prefix(tokens, j + 1):
        return None
    second = _classify(tokens, j + 1, kind)
    if second is None:
        return None
    bridge = tokens[j].kind == "word" and tokens[j].text in _BRIDGES[kind]
    if bridge and second[0] == "read":
        return "read", second[1], j + 1
    return "unreadable", None, j + 1


def _group_close(tokens: Sequence[_Token], j: int) -> int | None:
    """Index of the ")" closing a "(" at `j`, if the group is short and not nested."""
    if j >= len(tokens) or tokens[j].kind != "symbol" or tokens[j].text != "(":
        return None
    for k in range(j + 1, min(len(tokens), j + _MAX_GROUP + 2)):
        if tokens[k].kind == "symbol" and tokens[k].text == "(":
            return None
        if tokens[k].kind == "symbol" and tokens[k].text == ")":
            return k
    return None


def _readable(tok: _Token, kind: Kind) -> bool:
    """The one gate a number passes to become a value (`_Reader._record`).

    A read number is the whole number written: one that continues into a multiplier, a fraction
    or another digit group is never read. Amounts are never read from words (agent_design.md §5).
    """
    if tok.value is None or tok.continued:
        return False
    return not (kind == "amount" and tok.spelled)


class _Reader:
    def __init__(self, text: str, kind: Kind) -> None:
        self.norm, self.tokens = _tokenize(text)
        self.kind = kind
        self.read: dict[int, Quantity] = {}
        self.unreadable: dict[int, str] = {}
        self.done: set[int] = set()
        self.covered: set[int] = set()  # every token inside a read or unreadable expression
        self.spans: list[tuple[int, int]] = []  # every expression, in the normalized text
        # Unit of each read expression, by the index of its first and of its last token.
        self.unit_from: dict[int, CheckUnit] = {}
        self.unit_until: dict[int, CheckUnit] = {}

    def _record(
        self, members: Sequence[int], unit: CheckUnit | None, first: int, last: int
    ) -> None:
        """Read every member with `unit`, or record the whole span as one unreadable expression."""
        self.done.update(members)
        self.covered.update(range(first, last + 1))
        toks = self.tokens
        # A separator ending the last token is sentence punctuation ("%15."), not the expression.
        self.spans.append((toks[first].start, toks[last].end - toks[last].trailing))
        if unit is not None and all(_readable(toks[m], self.kind) for m in members):
            for m in members:
                value = toks[m].value
                assert value is not None
                self.read[m] = Quantity(value, unit)
            self.unit_from[first] = unit
            self.unit_until[last] = unit
        else:
            self.unreadable[first] = self.norm[toks[first].start : toks[last].end]

    def _drop(self, members: Sequence[int], first: int, last: int) -> None:
        """An expression whose unit word lies across a sentence boundary ("Fiyat 5. Ayrıca"): no
        value, no unreadable expression, no span. The members read nothing of their own: a number
        inside the group stays unread ("30 (bkz. Madde 5) gün") and a connector gives no member a
        unit. Only the tokens before the boundary are taken: the new sentence is read on its own,
        so an expression that starts it is not swallowed ("Madde 4. Doksanıncı günde",
        "Süre 30. Beşer gün", "Süre 30. Beş gün içinde")."""
        self.done.update(members)
        stop = next(k for k in range(first + 1, last + 1) if self.tokens[k].boundary_left)
        self.covered.update(range(first, stop))

    def _prefixed(self, i: int, unit: CheckUnit | None) -> None:
        """A prefixed number (`%15`, `₺1.000`) and its parenthetical restatement, if any.

        "%15 (20)" and "₺1.000 (5.000)" are two values with the prefix's unit; an inflected number
        word in the parentheses ("yüzde 15 (yirmibeş)") is an unreadable expression of its own.
        """
        toks = self.tokens
        members, last, inflected = [i], i, None
        if _group_close(toks, i + 1) == i + 3:
            if toks[i + 2].kind == "number":
                members, last = [i, i + 2], i + 3
            elif toks[i + 2].inflected:
                inflected = i + 2
        self._record(members, unit, i - 1, last)
        if inflected is not None:
            self._record([inflected], None, inflected, inflected)

    def _percent(self, i: int) -> None:
        if _prefix(self.tokens, i) == "%":
            self._prefixed(i, "%")
            return
        match = _unit_after(self.tokens, i + 1, "percent")
        if match is None:
            return
        if _crosses(self.tokens, i + 1, match[2]):
            self._drop([i], i, match[2])
            return
        self._record([i], None, i, match[2])

    def _suffixed(self, i: int) -> None:
        toks = self.tokens
        if self.kind == "amount":
            prefix = _prefix(toks, i)
            if prefix == "TL":
                self._prefixed(i, "TL")
                return
            if prefix == "foreign":
                self._prefixed(i, None)
                return
        # Candidates: skip a parenthesized group ("otuz (30) gün"), then look right after.
        candidates: list[tuple[int, list[int], bool]] = []
        close = _group_close(toks, i + 1)
        if close is not None:
            inner = range(i + 2, close)
            numbers = [k for k in inner if toks[k].kind == "number"]
            clean = len(inner) == 1 and len(numbers) == 1
            candidates.append((close + 1, [i, *numbers], clean))
        candidates.append((i + 1, [i], True))
        for start, members, clean in candidates:
            match = _unit_after(toks, start, self.kind)
            if match is None:
                continue
            status, unit, last = match
            if _crosses(toks, i + 1, last):
                self._drop(members, i, last)
                return
            fits = clean and not any(toks[m].trailing for m in members)
            readable_unit = unit if status == "read" and fits else None
            self._record(members, readable_unit, i, last)
            return

    def _has_unit(self, i: int) -> bool:
        """Number `i` carries a unit of any kind: a prefix before it or a unit word after it.

        A unit word across a sentence boundary counts: such a number is not unit-less either, so
        a connector gives it no unit ("30 gün veya 15. Ayrıca").
        """
        toks = self.tokens
        if _prefix(toks, i) is not None:
            return True
        return any(_unit_after(toks, i + 1, kind) is not None for kind in get_args(Kind))

    def _link(self, numbers: Sequence[int]) -> None:
        """A unit-less number joined by ila / ile / veya / - to a read value takes its unit.

        The read value may be on either side: "30 ila 60 gün", "yüzde 15 ila 20", "15 ila %20",
        "30 gün veya 15". A number with a unit of another kind ("%2 ile 30 gün") is not unit-less.
        Parentheses do not break the link: the number's own parenthetical restatement is skipped
        ("30 (otuz) ila 60 gün"), and so is a parenthesis around the connector or the other side
        ("30 gün (veya 90)", "30 gün veya (90)").
        """
        toks = self.tokens

        def paren(k: int) -> bool:
            return 0 <= k < len(toks) and toks[k].kind == "symbol" and toks[k].text in "()"

        def connector(k: int) -> bool:
            return 0 <= k < len(toks) and toks[k].text in _CONNECTORS

        def linked(k: int, step: int, ends: dict[int, CheckUnit]) -> CheckUnit | None:
            """From `k`, past parentheses, a connector; past parentheses, a read value's end."""
            while paren(k):
                k += step
            if not connector(k):
                return None
            k += step
            while k not in ends and paren(k):
                k += step
            return ends.get(k)

        changed = True
        while changed:
            changed = False
            for i in numbers:
                if i in self.done or self._has_unit(i):
                    continue
                close = _group_close(toks, i + 1)
                right = close + 1 if close is not None else i + 1
                unit = linked(right, 1, self.unit_from) or linked(i - 1, -1, self.unit_until)
                if unit is not None:
                    self._record([i], unit, i, i)
                    changed = True

    def _inflected(self, i: int) -> None:
        """An inflected number word where the number would stand is an unreadable expression.

        That place is right after "yüzde" / "%", or right before a unit of the kind, or before the
        kind's bridge word and a unit ("beşer takvim günü"), with a parenthetical restatement
        allowed in between ("otuzbeş (30) gün"). A unit across a sentence boundary is not in that
        place ("beşer. Günlük").
        """
        toks = self.tokens
        if self.kind == "percent" and _prefix(toks, i) == "%":
            self._record([i], None, i - 1, i)
            return
        close = _group_close(toks, i + 1)
        k = close + 1 if close is not None else i + 1
        last = None
        if k < len(toks) and _classify(toks, k, self.kind) is not None:
            last = k
        elif (
            k + 1 < len(toks)
            and toks[k].text in _BRIDGES[self.kind]
            and _classify(toks, k + 1, self.kind) is not None
        ):
            last = k + 1
        if last is None:
            return
        if _crosses(toks, i + 1, last):
            self._drop([i], i, last)
        else:
            self._record([i], None, i, last)

    def run(self) -> Extraction:
        numbers = [i for i, t in enumerate(self.tokens) if t.kind == "number"]
        for i, tok in enumerate(self.tokens):
            if tok.inflected:
                # Already inside an expression ("5 beşer gün"): not a second one.
                if i not in self.covered:
                    self._inflected(i)
            elif tok.kind != "number" or i in self.done:
                continue
            elif self.kind == "percent":
                self._percent(i)
            else:
                self._suffixed(i)
        self._link(numbers)
        return Extraction(
            values=tuple(self.read[k] for k in sorted(self.read)),
            unreadable=tuple(self.unreadable[k] for k in sorted(self.unreadable)),
            spans=tuple(sorted(self.spans)),
        )


def extract(text: str, kind: Kind) -> Extraction:
    """Read the values of one kind (duration / percent / amount) from a quote or proposal text."""
    return _Reader(text, kind).run()


# --- Quote matching -------------------------------------------------------------------------------


def _cuts(text: str, pos: int, end: int) -> bool:
    """The match `text[pos:end]` cuts a word or a number of the text it was found in.

    It does when an end falls between two letters / digits, or anywhere inside a number: digits
    joined by "." or "," ("250.000" or ".250.000" inside "1.250.000", "%15," inside "%15,5"). A
    "." or "," after the last digit is punctuation, not part of the number ("30." in "30. Madde").
    """

    def joined(a: int, b: int) -> bool:  # characters a and b both exist and are letters / digits
        return a >= 0 and b < len(text) and text[a].isalnum() and text[b].isalnum()

    if joined(pos - 1, pos) or joined(end - 1, end):
        return True
    return any(
        m.start() < edge < m.end() for m in _NUMBER_SPAN_RE.finditer(text) for edge in (pos, end)
    )


def _match_quote(quote: str | None, clause: Clause | None) -> tuple[int, int] | None:
    """Where the quote first matches cleanly, as a range of the clause's own text, or None.

    A match must start and end on a source boundary and must not cut a word, a number or a value
    expression of any kind (read or unreadable): every value read from the quote is then the whole
    value written in the clause.
    """
    if quote is None or clause is None:
        return None
    needle = normalize(quote, ALL_STEPS).text.strip()
    if not needle:
        return None
    hay = normalize(clause.text, ALL_STEPS)
    # `extract` normalizes the same way, so its spans are positions in `hay.text`.
    spans = [span for kind in get_args(Kind) for span in extract(clause.text, kind).spans]
    pos = hay.text.find(needle)
    while pos != -1:
        end = pos + len(needle)
        if (
            hay.is_boundary(pos)
            and hay.is_boundary(end)
            and not _cuts(hay.text, pos, end)
            and not any(start < edge < stop for start, stop in spans for edge in (pos, end))
        ):
            return hay.span(pos, end)
        pos = hay.text.find(needle, pos + 1)
    return None


# --- Signals --------------------------------------------------------------------------------------


def _model_unit(raw: str | None, rule_unit: CheckUnit) -> CheckUnit | None:
    """The unit the model gave: the rule's when empty, None when unknown."""
    if raw is None or not raw.strip():
        return rule_unit
    wanted = _lower(raw.strip())
    for unit in _CHECK_UNITS:
        if _lower(unit) == wanted:
            return unit
    return None


def _numeric_reasons(finding: Finding, check: Check, quote: str) -> set[ReviewReason]:
    extraction = extract(quote, _KIND_OF_UNIT[check.unit])
    if not extraction.values:
        return {"numeric_mismatch"}
    if not _single(extraction):
        return {"ambiguous_value"}
    reasons: set[ReviewReason] = set()
    verdict = _evaluate(extraction.values, check)
    if verdict is None:
        reasons.add("unit_conversion_uncertain")
    elif verdict != (finding.status == "compliant"):
        reasons.add("numeric_mismatch")
    if finding.value is not None:
        unit = _model_unit(finding.unit, check.unit)
        if unit is None or _KIND_OF_UNIT[unit] != _KIND_OF_UNIT[check.unit]:
            reasons.add("numeric_mismatch")
        else:
            claimed = Quantity(finding.value, unit)
            if not all(_same(claimed, v) for v in extraction.values):
                reasons.add("numeric_mismatch")
    return reasons


def _outside_reasons(check: Check, clause_text: str) -> set[ReviewReason]:
    """Signals from the values of the rule's kind written in the clause outside the quote.

    Only for a `compliant` finding whose quote passed: a narrow quote must not hide a value that
    breaks the rule. Each value is compared with the check on its own; one that satisfies it
    changes nothing.

    The clause is read once, as a whole, never as the text before and the text after the match.
    A match cannot cut a value expression, but it can stand between a number and the neighbour
    that gives it its unit through a connector ("15-30 gün", "%10 ila 25"): read apart, that
    number has no unit and is not a value at all. The values inside the quote have just passed
    the check, so comparing every value of the clause adds only what the quote left out.
    """
    reasons: set[ReviewReason] = set()
    extraction = extract(clause_text, _KIND_OF_UNIT[check.unit])
    if extraction.unreadable:
        reasons.add("ambiguous_value")
    for value in extraction.values:
        verdict = _evaluate([value], check)
        if verdict is None:
            reasons.add("unit_conversion_uncertain")
        elif not verdict:
            reasons.add("numeric_mismatch")
    if "ambiguous_value" in reasons:
        reasons.discard("numeric_mismatch")
    return reasons


def _masked_number(clause_text: str) -> bool:
    """The masked clause holds a TCKN or TELEFON placeholder, which may stand for a number that is
    not personal data. The kind is read from the placeholder itself; the PII map is never seen."""
    return any(
        m.group().startswith(_NUMBER_PLACEHOLDERS) for m in PLACEHOLDER.finditer(clause_text)
    )


def _proposal_check(text: str, check: Check | None) -> CodeCheck:
    if check is None:
        return "unverifiable"
    extraction = extract(text, _KIND_OF_UNIT[check.unit])
    if not _single(extraction):
        return "failed"
    return "passed" if _evaluate(extraction.values, check) is True else "failed"


def _verify_one(
    finding: Finding,
    rule: Rule,
    clauses: dict[str, Clause],
    injection_suspected: bool,
    enabled: bool,
) -> Finding:
    reasons: set[ReviewReason] = set()
    quote = finding.quote
    proposal = finding.proposal
    if finding.escalation_reason is not None:
        reasons.add("agent_escalated")
    if finding.no_finding_reason is not None:
        reasons.add("no_finding")
    if finding.interrupted_by is not None:
        reasons.add("run_interrupted")

    if not enabled:
        if proposal is not None:
            proposal = proposal.model_copy(update={"code_check": None})
    else:
        if finding.status in ("compliant", "non_compliant"):
            clause = clauses.get(finding.clause_id) if finding.clause_id else None
            matched = _match_quote(finding.quote, clause)
            if matched is None:
                reasons.add("quote_not_found")
            else:
                start, end = matched
                quote = clause.text[start:end] if clause is not None else quote
                if rule.check is not None and quote is not None and clause is not None:
                    numeric = _numeric_reasons(finding, rule.check, quote)
                    if not numeric and finding.status == "compliant":
                        numeric = _outside_reasons(rule.check, clause.text)
                    reasons |= numeric
            # The whole clause, whatever the quote matched: a narrow quote cannot leave it out.
            if finding.status == "compliant" and clause is not None and _masked_number(clause.text):
                reasons.add("masked_value_in_clause")
        elif finding.no_finding_reason is None:
            reasons.add("unverified_not_found")
            if rule.severity == "critical":
                reasons.add("critical_not_found")
        if finding.status == "non_compliant" and proposal is None:
            reasons.add("missing_proposal")
        if proposal is not None:
            code_check = _proposal_check(proposal.proposed_text, rule.check)
            proposal = proposal.model_copy(update={"code_check": code_check})
            if code_check == "failed":
                reasons.add("proposal_noncompliant")
        if injection_suspected:
            reasons.add("injection_suspected")

    ordered = [r for r in _REASON_ORDER if r in reasons]
    return finding.model_copy(
        deep=True,
        update={
            "quote": quote,
            "proposal": proposal,
            "needs_review": bool(ordered),
            "review_reasons": ordered,
        },
    )


def verify(
    findings: Sequence[Finding],
    rules: Sequence[Rule],
    clauses: Sequence[Clause],
    injection_suspected: bool,
    enabled: bool = True,
) -> list[Finding]:
    """Return new findings, in the same order, with the lawyer-queue signals set by code.

    The input is not changed and the result does not depend on earlier calls. `enabled=False` is
    only for the eval's "before" run: then only `agent_escalated`, `no_finding` and
    `run_interrupted` are set, the quote is left as the model wrote it and `code_check` stays
    empty. `run_interrupted` (a submitted finding whose run did not end by itself) is set in both
    modes; the other signals are still computed for such a finding.
    """
    rules_by_id = {rule.id: rule for rule in rules}
    for finding in findings:
        if finding.rule_id not in rules_by_id:
            raise ValueError(f"Kural setinde olmayan rule_id: {finding.rule_id!r}.")
    clauses_by_id: dict[str, Clause] = {}
    for clause in clauses:
        clauses_by_id.setdefault(clause.clause_id, clause)
    return [
        _verify_one(f, rules_by_id[f.rule_id], clauses_by_id, injection_suspected, enabled)
        for f in findings
    ]
