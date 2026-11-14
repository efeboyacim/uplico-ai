"""PII masking: TCKN, IBAN, phone, e-mail and person names become `[KIND_N]` placeholders.

Amounts, durations, dates, percentages, clause numbers and company names are never masked; the
rules need them. Masking is pure: no I/O, no logging, and no raw value in an exception message.
"""

import bisect
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from itertools import pairwise

from src.models import MaskedText
from src.prepare.clauses import _any_case
from src.text import normalize

PiiMapping = dict[str, str]  # placeholder -> raw value, e.g. {"[KISI_1]": "Ahmet Yılmaz"}

KINDS: tuple[str, ...] = ("KISI", "TCKN", "IBAN", "TELEFON", "EPOSTA")

_KIND_ALT = "|".join(KINDS)
PLACEHOLDER = re.compile(r"\[(?:" + _KIND_ALT + r")_[0-9]+\]")
_MAPPING_KEY = re.compile(r"\[(" + _KIND_ALT + r")_([1-9][0-9]*)\]")
# Placeholders already in the text are blanked with this character before any search, so nothing
# inside them is matched and the name scanner treats them as punctuation.
_FILLER = "\x00"

# Boundaries, shared by discovery and by the search for every occurrence of a known value, so a
# discovered value is always found again.
_ALNUM_BEFORE = r"(?<![^\W_])"  # not preceded by a letter or digit (Unicode)
_ALNUM_AFTER = r"(?![^\W_])"
# Combining marks (e.g. the dot of a decomposed "İ") belong to the letter before them.
_MARKS = "\u0300-\u036f"
_NAME_AFTER = r"(?![^\W_]|[" + _MARKS + r"])"
_EMAIL_BEFORE = r"(?<![A-Za-z0-9._%+\-])"
_EMAIL_AFTER = r"(?![A-Za-z0-9\-]|\.[A-Za-z0-9])"
_BOUNDARIES: dict[str, tuple[str, str]] = {
    "KISI": (_ALNUM_BEFORE, _NAME_AFTER),
    "TCKN": (r"(?<![0-9])", r"(?![0-9])"),
    "IBAN": (_ALNUM_BEFORE, _ALNUM_AFTER),
    "TELEFON": (r"(?<![0-9+])", r"(?![0-9])"),
    "EPOSTA": (_EMAIL_BEFORE, _EMAIL_AFTER),
}

_EMAIL = re.compile(
    _EMAIL_BEFORE + r"[A-Za-z0-9._%+\-]+@(?:[A-Za-z0-9\-]+\.)+[A-Za-z]{2,}" + _EMAIL_AFTER
)
# At most one space or no-break space between two characters of a TCKN or an IBAN. Tabs, line
# breaks and other Unicode spaces are not gaps: an IBAN split over two lines is a documented limit.
_GAP_CHAR = "[ \u00a0]"
_GAP = _GAP_CHAR + "?"
# "TR" and 24 digits in any grouping: compact, the standard print grouping
# ("TR33 0006 1005 1978 6457 8413 26") or irregular ("TR18 00010 00406 02833 88480 99"). Each step
# consumes exactly one digit, so the pattern cannot backtrack catastrophically.
_IBAN = re.compile(_ALNUM_BEFORE + r"TR(?:" + _GAP + r"[0-9]){24}" + _ALNUM_AFTER)
# Compact or the standard print grouping, the only IBAN spellings found before step 17b. Used only
# to tell a strict IBAN from a weak, freely grouped one (see `_is_weak`).
_IBAN_STANDARD = re.compile(r"TR[0-9]{2}(?:" + _GAP + r"[0-9]{4}){5}" + _GAP + r"[0-9]{2}")
# 11 digits in any grouping. Searched inside a lookahead so every start is tried and candidates
# may overlap: in "45 123456789 50" the bad-checksum "45 123456789" must not hide the valid
# "123456789 50". A spaced candidate is weak (see `_is_weak`), so "4437 0532 648" cannot split a
# phone.
_TCKN = re.compile(r"(?<![0-9])(?=([1-9](?:" + _GAP + r"[0-9]){10})(?![0-9]))")
# A run grouped like thousands ("55 000 000 000") is an amount, not a TCKN.
_THOUSANDS = re.compile(r"[1-9][0-9]{0,2}(?:" + _GAP_CHAR + r"[0-9]{3})+")
# A spaced candidate right after a digit and "." or "," continues a number ("Madde 5.1 5 400 000
# 000"), unless that digit ends a value already found.
_DOTTED_BEFORE = re.compile(r"[0-9][.,]")
# "+90" or "0", then 3-3-2-2 digits; at most one space, dot or hyphen between parts. No generic
# digit-group pattern, so amounts, durations and dates are never taken for a phone number.
_SEP = r"[ .\-]?"
_PHONE = re.compile(
    r"(?<![0-9+])(?:\+90|0)"
    + _SEP
    + r"[0-9]{3}"
    + _SEP
    + r"[0-9]{3}"
    + _SEP
    + r"[0-9]{2}"
    + _SEP
    + r"[0-9]{2}(?![0-9])"
)
# The same number with the area code in parentheses: after the prefix ("0 (532) 123 45 67",
# "+90 (533) 555 05 30") or with the "0" inside them ("(0212) 555 12 34", "(0 212) 555 12 34").
# The prefix is still required: "(532) 123 45 67" is not matched. Inside the parentheses only a
# space may follow the "0": "(0.125)" and "(0-100)" are a rate and a range, not an area code.
# A pattern of its own, searched by `mask` after everything else and placed last (see
# `_in_parentheses`): it only takes text that no other value covers, so nothing that was masked
# without it loses its place ("(0212)123 0532 123 45 67", "(0212) 214 73 87 39 60"). Searched
# inside a lookahead so candidates may overlap: in "... 45 0 (0212) 555 12 34" the candidate that
# starts at a "0" belonging to another value must not hide the one that starts at "(".
_PHONE_PARENS = re.compile(
    r"(?<![0-9+])(?=((?:(?:\+90|0)"
    + _SEP
    + r"\((?:0 ?)?[0-9]{3}\)|\(0 ?[0-9]{3}\))"
    + _SEP
    + r"[0-9]{3}"
    + _SEP
    + r"[0-9]{2}"
    + _SEP
    + r"[0-9]{2})(?![0-9]))"
)
# A mobile number without prefix: "5" and 3-3-2-2 digits with exactly one space, dot or hyphen
# between the groups ("533 555 05 30"). It has no checksum, so the pattern stays narrow: not inside
# a longer number (no digit, "+", or digit and one separator before or after it; this also keeps it
# out of "+90 532 ..." and "1.532.123.45.67") and not a clause number ("Madde 532 123 45 67"). A
# complete neighbouring "5xx xxx xx xx" group after one separator counts as a boundary, so two
# numbers written one after the other are both found; `_discover` also searches a copy in which
# the values already found are blanked, so a digit that belongs to one of them does not block.
# Known false positive, documented: any number of this shape, e.g. "500 000 00 00".
_BARE_SEP = r"[ .\-]"
_BARE_CORE = (
    r"5[0-9]{2}" + _BARE_SEP + r"[0-9]{3}" + _BARE_SEP + r"[0-9]{2}" + _BARE_SEP + r"[0-9]{2}"
)
_PHONE_BARE = re.compile(
    r"(?<![0-9+])(?:(?<![0-9+]"
    + _BARE_SEP
    + r")|(?<=(?<![0-9+])"
    + _BARE_CORE
    + _BARE_SEP
    + r"))(?<!"
    + _any_case("madde")
    + _GAP_CHAR
    + r")"
    + _BARE_CORE
    + r"(?![0-9])(?!"
    + _BARE_SEP
    + r"(?!"
    + _BARE_CORE
    + r"(?![0-9]))[0-9])"
)

# --- Person names -------------------------------------------------------------------------------

# Horizontal spaces inside a line: space, tab, no-break and the other Unicode spaces that text
# extracted from DOCX/PDF carries. Line breaks are not among them.
_HSPACE_CHARS = " \t\u00a0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
_HSPACE_CHARS += "\u202f\u205f\u3000"
_HSPACE = "[" + _HSPACE_CHARS + "]"


def _fold(word: str) -> str:
    """Compare form of a word: Turkish i variants and diacritics dropped, lower case.

    `İmza`, `IMZA`, `Imza` and `imza` all fold to `imza`; `Kaşe` and `Kase` to `kase`. So a closed
    list matches text typed on a non-Turkish keyboard or upper-cased with `str.upper()`.
    """
    decomposed = unicodedata.normalize("NFD", word)
    bare = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return bare.replace("ı", "i").lower()


def _letter_variants() -> dict[str, str]:
    """Folded letter -> every letter that folds to it, e.g. "i" -> "iıIİîÎ"."""
    alphabet = "abcçdefgğhıijklmnoöpqrsştuüvwxyzâîû"
    variants: dict[str, str] = {}
    for ch in alphabet + alphabet.upper() + "İ":
        variants[_fold(ch)] = variants.get(_fold(ch), "") + ch
    return variants


_VARIANTS = _letter_variants()


def _loose(word: str) -> str:
    """Regex matching `word` in any case and with or without Turkish diacritics (see `_fold`)."""
    parts: list[str] = []
    for ch in word:
        if ch == " ":
            parts.append(_HSPACE + "+")
        elif ch.isalpha():
            parts.append("[" + _VARIANTS.get(_fold(ch), ch) + "][" + _MARKS + "]*")
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


_LABEL_GAP = "[" + _HSPACE_CHARS + "*_]*"
# Label words that need a ":" after them; without it, a signature line made of one is a field
# caption, not a name (see `_names_from_signature_lines`).
_COLON_LABEL_WORDS = ("Adı Soyadı", "Ad Soyad", "Yetkili")
_LABEL = re.compile(
    r"(?<![^\W_])(?:(?:"
    + "|".join(_loose(w) for w in _COLON_LABEL_WORDS)
    + ")"
    + _LABEL_GAP
    + ":|"
    + _loose("temsilen")
    + r"(?![^\W\d_])(?:"
    + _LABEL_GAP
    + ":)?)"
    + _LABEL_GAP
)
_LETTERS = re.compile(r"[^\W\d_](?:[^\W\d_]|[" + _MARKS + r"])*")
_NO_LETTER_AFTER = r"(?![^\W\d_])"


# --- Labelled identity numbers (step 19f) -------------------------------------------------------


def _dotted(letters: str) -> str:
    """Regex for an initialism in any case, each letter optionally followed by "." and one space
    ("TC", "T.C.", "T. C.")."""
    return (r"\.?" + _GAP).join(_loose(ch) for ch in letters) + r"\.?"


_NO = "(?:" + _loose("Numarası") + "|" + _loose("No") + _NO_LETTER_AFTER + r"\.?)"
# A label that names the number after it as a person's identity number: "TCKN", "T.C.K.N.",
# "TCKNo", "TCKN No", "T.C. Kimlik No", "TC Kimlik Numarası", "Kimlik No", "TC No" (any case,
# with or without Turkish characters and dots).
_TCKN_LABEL = (
    r"(?<![^\W_])(?:"
    + _dotted("TCKN")
    + "(?:[oO]"
    + _NO_LETTER_AFTER
    + r"\.?|"
    + _HSPACE
    + "*"
    + _NO
    + ")?|(?:"
    + _dotted("TC")
    + _HSPACE
    + "*)?"
    + _loose("Kimlik")
    + _HSPACE
    + "+"
    + _NO
    + "|"
    + _dotted("TC")
    + _HSPACE
    + "*"
    + _NO
    + ")"
    + _NO_LETTER_AFTER
)
# A compact 11-digit number right after such a label (an optional suffix such as "No'su", spaces,
# then an optional ":", ";", "=", "-", en or em dash) is a TCKN whatever its checksum: a mistyped
# identity number is still someone's. A spaced number after a label keeps the checksum rule, and
# an unlabelled number is unchanged.
_TCKN_LABELLED = re.compile(
    _TCKN_LABEL
    + "(?:['\u2019][^\\W\\d_]+)?"
    + _HSPACE
    + "*(?:[:;=\\-\u2013\u2014]"
    + _HSPACE
    + "*)?([0-9]{11})(?![0-9])"
)


def _word_alternation(words: tuple[str, ...]) -> str:
    ordered = sorted(words, key=len, reverse=True)
    return "(?:" + "|".join(_loose(w) for w in ordered) + ")"


_FORM_WORDS = (
    "İmza",
    "Tarih",
    "Kaşe",
    "Mühür",
    "Unvan",
    "Ünvan",
    "Görev",
    "Görevi",
    "Adres",
    "Telefon",
    "Tel",
    "Faks",
    "E-posta",
    "Eposta",
    "TCKN",
    "TC",
    "Kimlik",
    "No",
    "IBAN",
    "Vergi",
)
_FORM_WORD = re.compile(_word_alternation(_FORM_WORDS) + _NO_LETTER_AFTER)

_COMPANY_MARKERS = (
    "A.Ş.",
    "A.S.",
    "AŞ",
    "Ltd",
    "Şti",
    "Limited",
    "Anonim",
    "Şirketi",
    "Holding",
    "Tic.",
    "San.",
)
# Company markers keep the exact Turkish spelling (any case): a loose "AS" or "as" would stop
# names from being extracted.
_COMPANY_MARKER = re.compile(
    r"(?<![^\W\d_])"
    + "(?:"
    + "|".join(_any_case(w) for w in sorted(_COMPANY_MARKERS, key=len, reverse=True))
    + ")"
    + _NO_LETTER_AFTER
)

_ABBREVIATIONS = frozenset(_fold(w) for w in ("Av.", "Dr.", "Prof.", "Doç.", "Sn."))
_TITLE_HEADS = frozenset(
    _fold(w)
    for w in (
        "Müdür",
        "Müdürü",
        "Başkan",
        "Başkanı",
        "Üye",
        "Üyesi",
        "Direktör",
        "Direktörü",
        "Yetkilisi",
        "Vekili",
        "Temsilcisi",
        "Yardımcısı",
        "Sorumlusu",
        "Yöneticisi",
        "Koordinatörü",
        "Avukat",
        "Av.",
        "Dr.",
        "Prof.",
        "Doç.",
        "Sn.",
        "Sayın",
        "CEO",
        "CFO",
        "CTO",
        "COO",
    )
)
_TITLE_MODIFIERS = frozenset(
    _fold(w)
    for w in (
        "Genel",
        "Yönetim",
        "Kurulu",
        "İcra",
        "Mali",
        "İşler",
        "İdari",
        "Satın",
        "Alma",
        "Hukuk",
        "Baş",
        "Kıdemli",
        "Bölge",
        "Şube",
        "Proje",
        "Satış",
        "Pazarlama",
        "Finans",
        "Operasyon",
    )
)


@dataclass(frozen=True)
class _Token:
    start: int
    end: int
    word: str


# A hyphen or apostrophe between two letters joins the parts of one name word ("Kaya-Demir",
# "O'Brien") when the part after it starts with a capital letter.
_JOINERS = "-'\u2019\u2010\u2011"
_MAX_JOINS = 3


def _joined_end(text: str, end: int, limit: int | None = None) -> int:
    """End of the name word that starts before `end`, following joiners (see `_JOINERS`).

    The joiner separates two things, and the word ends before it, when the part after it is a
    title word, a form word or a label, or starts a company name ("Yılmaz-ABC A.Ş."). With
    `limit`, the company check after a joiner looks at most `limit` words ahead (see
    `_scan_run`).
    """
    n = len(text)
    for _ in range(_MAX_JOINS):
        if end + 1 >= n or text[end] not in _JOINERS:
            break
        part = _LETTERS.match(text, end + 1)
        if part is None or not part.group()[0].isupper():
            break
        folded = _fold(part.group())
        if folded in _TITLE_HEADS or folded in _TITLE_MODIFIERS:
            break
        rest = _scan_run(text, end + 1, join=False, limit=limit)
        if not rest:
            break  # a form word, a label or a word followed by ":"
        guard_end = _guard_end(text, rest[-1].end, join=False, limit=limit)
        if _COMPANY_MARKER.search(text[end + 1 : guard_end]):
            break
        end = part.end()
    return end


def _scan_run(text: str, pos: int, join: bool = True, limit: int | None = None) -> list[_Token]:
    """Consecutive capitalised name words from `pos` on the same line (see the name rule).

    With `limit`, at most `limit` words are read, and a joined word looks at most `limit` words
    ahead; the step 19c discovery paths use it so that a long line is not walked again for
    every candidate on it. The label path reads without a limit.
    """
    tokens: list[_Token] = []
    n = len(text)
    while True:
        start = pos
        while start < n and text[start] in _HSPACE_CHARS:
            start += 1
        if tokens and start == pos:
            return tokens  # a token must be followed by a space or tab to continue the run
        if _FORM_WORD.match(text, start) or _LABEL.match(text, start):
            return tokens  # a form word, or the next label ("Ad Soyad:", "Temsilen")
        match = _LETTERS.match(text, start)
        if match is None:
            return tokens  # end of line or text, punctuation, a digit
        end = match.end()
        word = match.group()
        if end < n and text[end] == ".":
            if _fold(word + ".") in _ABBREVIATIONS:
                end += 1
                word += "."
            elif end + 1 < n and text[end + 1].isalpha():
                return tokens  # a dotted abbreviation such as "T.C." is not a letters-only token
        elif join:
            end = _joined_end(text, end, limit)
            word = text[start:end]
        if end < n and text[end].isdigit():
            return tokens  # the token contains a digit
        colon = end
        while colon < n and text[colon] in _HSPACE_CHARS:
            colon += 1
        if colon < n and text[colon] == ":":
            return tokens  # the next label
        if not word[0].isupper():
            return tokens
        tokens.append(_Token(start, end, word))
        if end < n and text[end] not in _HSPACE_CHARS:
            return tokens  # punctuation or a line break right after the token
        if limit is not None and len(tokens) >= limit:
            return tokens
        pos = end


def _followed_by_placeholder(text: str, pos: int) -> bool:
    n = len(text)
    while pos < n and text[pos] in _HSPACE_CHARS:
        pos += 1
    return pos < n and text[pos] == _FILLER


def _next_chunk_end(text: str, pos: int) -> int:
    """End of the whitespace-delimited token directly after `pos` on the same line."""
    n = len(text)
    while pos < n and text[pos] in _HSPACE_CHARS:
        pos += 1
    while pos < n and not text[pos].isspace():
        pos += 1
    return pos


_AND = re.compile(_HSPACE + "+(?:" + _loose("ve") + _NO_LETTER_AFTER + "|&)" + _HSPACE + "+")


def _guard_end(text: str, end: int, join: bool = True, limit: int | None = None) -> int:
    """End of the text checked for a company marker: the run, any "ve ..." continuation of it
    ("X Sanayi ve Ticaret A.Ş.") and the token directly after. With `limit`, the continuations
    add at most `limit` words in all."""
    budget = limit
    while budget is None or budget > 0:
        joined = _AND.match(text, end)
        more = _scan_run(text, joined.end(), join, budget) if joined else []
        if not more:
            break
        end = more[-1].end
        if budget is not None:
            budget -= len(more)
    return _next_chunk_end(text, end)


def _is_head(token: _Token) -> bool:
    return _fold(token.word) in _TITLE_HEADS


def _name_tokens(run: list[_Token]) -> list[_Token]:
    """The tokens of `run` that form the name, or [] when no name is extracted."""
    heads = [i for i, token in enumerate(run) if _is_head(token)]
    if not heads:
        if all(_fold(token.word) in _TITLE_MODIFIERS for token in run):
            return []  # a body or unit such as "Yönetim Kurulu", not a person
        candidate = run
    elif heads[-1] < len(run) - 1:
        candidate = run[heads[-1] + 1 :]
    else:
        cut = len(run)
        while cut > 0 and (_is_head(run[cut - 1]) or _fold(run[cut - 1].word) in _TITLE_MODIFIERS):
            cut -= 1
        candidate = run[:cut]
    if 2 <= len(candidate) <= 4:
        return candidate
    # Fail closed: the whole run, unless nothing of a name is left or it is a single bare word.
    if not candidate or len(run) < 2:
        return []
    return run


# E-mail linkage: a local part of 2-4 letter-only parts of at least two letters, joined by ".",
# "_" or "-" ("elif.soylu"). A one-word box ("info") or an initial ("a.yilmaz") names no one.
_EMAIL_NAME_LOCAL = re.compile(r"[A-Za-z]{2,}(?:[._\-][A-Za-z]{2,}){1,3}")
_EMAIL_NAME_SPLIT = re.compile(r"[._\-]")
# A functional mailbox spells a unit, not a person ("satis.birimi@" -> "Satış Birimi").
_UNIT_WORDS = frozenset(_fold(w) for w in ("Birimi", "Ekibi", "Departmanı", "Müdürlüğü", "Servisi"))


def _names_from_emails(text: str) -> Iterator[str]:
    """Names spelled by an e-mail's local part on the e-mail's own line.

    The parts of "elif.soylu@..." are looked for as consecutive words, in order and separated
    only by horizontal spaces, on the line that holds the address, compared in their folded
    form (`_fold`: any case, with or without Turkish diacritics; "ucar" finds "Uçar", "tore"
    finds "Töre") and with the name boundaries of `_BOUNDARIES`. A hit is a name when every word
    starts with a capital letter, no word is a unit word or a title head, the words are not only
    title modifiers ("Satın Alma") and no company marker follows (`_company_after`). The name
    is then masked everywhere like any discovered name; a suffix after it ("Soylu'ya") stays
    outside. Each line with an e-mail is read once, whatever the number of addresses on it.
    """
    targets: dict[int, set[tuple[str, ...]]] = {}  # line start -> folded local parts
    breaks: list[int] | None = None
    for email in _EMAIL.finditer(text):
        local = email.group().split("@", 1)[0]
        if not _EMAIL_NAME_LOCAL.fullmatch(local):
            continue
        if breaks is None:
            breaks = [i for i, ch in enumerate(text) if ch == "\n"]
        line = bisect.bisect_left(breaks, email.start())
        line_start = breaks[line - 1] + 1 if line else 0
        targets.setdefault(line_start, set()).add(
            tuple(part.lower() for part in _EMAIL_NAME_SPLIT.split(local))
        )
    before, after = _BOUNDARY_PATTERNS["KISI"]
    found: set[str] = set()
    for line_start, wanted in targets.items():
        line_end = text.find("\n", line_start)
        if line_end < 0:
            line_end = len(text)
        sizes = {len(parts) for parts in wanted}
        words = list(_LETTERS.finditer(text, line_start, line_end))
        for i in range(len(words)):
            for size in sizes:
                group = words[i : i + size]
                if len(group) < size:
                    continue
                gaps = (text[a.end() : b.start()] for a, b in pairwise(group))
                if any(not gap or gap.strip(_HSPACE_CHARS) for gap in gaps):
                    continue  # not separated by horizontal spaces only
                if tuple(_fold(word.group()) for word in group) not in wanted:
                    continue
                start, end = group[0].start(), group[-1].end()
                if not (before.match(text, start) and after.match(text, end)):
                    continue
                if not all(word.group()[0].isupper() for word in group):
                    continue
                folded = [_fold(word.group()) for word in group]
                if any(w in _UNIT_WORDS or w in _TITLE_HEADS for w in folded):
                    continue  # a unit or a title mailbox ("hukuk.muduru@" -> "Hukuk Müdürü")
                if all(w in _TITLE_MODIFIERS for w in folded):
                    continue  # a body or unit such as "Satın Alma"
                name = text[start:end]
                if name in found:
                    continue  # already a name: masked everywhere, no need to check again
                if _company_after(text, start, end):
                    continue
                found.add(name)
                yield name


# How far the company check of the two step 19c paths looks past a candidate, in name words
# (the "ve ..." continuations included). The label path looks without a limit; a bound keeps a
# line with many candidates linear ("Ali Veli Ali Veli ..." next to "ali.veli@").
_GUARD_WORDS = 12


def _company_after(text: str, start: int, end: int) -> bool:
    """A company marker in `text[start:end]`, in the next `_GUARD_WORDS` capitalised words and
    "ve ..." continuations, or in the token directly after them ("Ahmet Yılmaz İnşaat A.Ş." is a
    company even when only "ahmet.yilmaz@" spells "Ahmet Yılmaz"). A marker further than
    `_GUARD_WORDS` words away is not seen."""
    more = _scan_run(text, end, limit=_GUARD_WORDS)
    stop = more[-1].end if more else end
    guard_end = _guard_end(text, stop, limit=_GUARD_WORDS - len(more))
    return _COMPANY_MARKER.search(text[start:guard_end]) is not None


# Unlabelled signature line: the next line starts, after any indent, with the form word "İmza"
# in any spelling ("İmza:", "IMZA", "İmza / Kaşe:").
_SIGNATURE_NEXT = re.compile(r"(?m)^" + _HSPACE + "*" + _loose("İmza") + _NO_LETTER_AFTER)
# Form words only on this path: "Tedarikçi Kaşesi\nİmza:" is a stamp field and "Alıcı Adına\nİmza:"
# a party caption, not a person. Not in `_FORM_WORDS`, so the label path ("Yetkili: Ahmet Yılmaz
# Kaşesi") is unchanged.
_SIGNATURE_FORM_WORDS = frozenset(_fold(w) for w in ("Kaşesi", "Mührü", "Adına"))
# A label word written without its ":" ("Ad Soyad\nİmza:") is a caption, not a name.
_SIGNATURE_CAPTIONS = frozenset(" ".join(_fold(w) for w in c.split()) for c in _COLON_LABEL_WORDS)
# Party roles: a signature caption such as "HİZMET SAĞLAYICI", "Tedarikçi Firma" or "Onaylayan
# Taraf" names a side of the contract, not a person. One such word anywhere in the line is
# enough. "Kiracı" is left out: it is also a surname, and with "Firma" / "Yetkili" next to it
# the caption is caught anyway.
_ROLE_WORDS = frozenset(
    _fold(w)
    for w in (
        "Alıcı",
        "Satıcı",
        "Tedarikçi",
        "Yüklenici",
        "İşveren",
        "İşçi",
        "Sahibi",
        "Sağlayıcı",
        "Kiralayan",
        "Müşteri",
        "Firma",
        "Şirket",
        "Taraf",
        "Unvanı",
        "Ünvanı",
        "Onaylayan",
        "Hazırlayan",
        "Yetkili",
        "Temsilci",
        "Vekil",
        "Kefil",
        "Bayi",
        "Distribütör",
        "Acente",
        "İdare",
        "Kurum",
    )
)
# Two-word roles: "Alan" and "Veren" alone are surnames, so only the pair counts.
_ROLE_PAIRS = frozenset(
    (_fold(a), _fold(b))
    for a, b in (("Hizmet", "Alan"), ("Hizmet", "Veren"), ("Kiraya", "Veren"), ("İş", "Veren"))
)
# A candidate written this many times elsewhere in the text is a defined term of the contract
# ("İş Sahibi ... İş Sahibi ..."), not a person.
_DEFINED_TERM_USES = 3


def _is_role(folded: list[str]) -> bool:
    return any(w in _ROLE_WORDS for w in folded) or any(
        pair in _ROLE_PAIRS for pair in pairwise(folded)
    )


def _names_from_signature_lines(text: str) -> Iterator[str]:
    """A line that is only a name, directly above an "İmza" line.

    The whole line, after any indent, is one name run (`_scan_run`) of 2-4 words with no title
    head, not only title modifiers, no signature form word ("Kaşesi", "Mührü", "Adına"), no unit
    word, no party role (`_ROLE_WORDS`, `_ROLE_PAIRS`), not a label written without its ":"
    ("Ad Soyad") and no company marker (`_company_after`). A candidate written at least
    `_DEFINED_TERM_USES` more times in `text` (any case, any whitespace between its words) is a
    defined term and not a name. A blank line between the name and "İmza" is not bridged.
    """
    candidates: list[str] = []
    for signature in _SIGNATURE_NEXT.finditer(text):
        line_end = signature.start() - 1  # the line break that ends the line above
        if line_end < 0:
            continue
        start = text.rfind("\n", 0, line_end) + 1
        while start < line_end and text[start] in _HSPACE_CHARS:
            start += 1
        run = _scan_run(text, start, limit=5)  # five words: the line is too long either way
        if not 2 <= len(run) <= 4:
            continue
        if text[run[-1].end : line_end].strip(_HSPACE_CHARS + "\r"):
            continue  # the line holds more than the run
        folded = [_fold(t.word) for t in run]
        if any(_is_head(t) for t in run):
            continue
        if any(w in _SIGNATURE_FORM_WORDS or w in _UNIT_WORDS for w in folded):
            continue  # a stamp field, a party caption or a unit ("Satış Birimi")
        if all(w in _TITLE_MODIFIERS for w in folded):
            continue  # a body or unit such as "Satın Alma"
        if " ".join(folded) in _SIGNATURE_CAPTIONS:
            continue  # "Ad Soyad" without its ":"
        if _is_role(folded):
            continue  # "HİZMET SAĞLAYICI", "Hizmet Alan"
        if _company_after(text, run[0].start, run[-1].end):
            continue
        candidates.append(text[run[0].start : run[-1].end])
    if not candidates:
        return
    uses = Counter(
        _name_key(value) for value, _, _ in _name_spans(text, map(_name_key, candidates))
    )
    for candidate in candidates:
        if uses[_name_key(candidate)] - 1 < _DEFINED_TERM_USES:  # its own line counts once
            yield candidate


def _names_from_labels(text: str) -> Iterator[str]:
    for label in _LABEL.finditer(text):
        run = _scan_run(text, label.end())
        if not run:
            continue
        guard = text[run[0].start : _guard_end(text, run[-1].end)]
        if _COMPANY_MARKER.search(guard):
            continue  # a company name is never masked
        if _is_head(run[-1]) and _followed_by_placeholder(text, run[-1].end):
            # A title left before an already masked name ("Genel Müdür [KISI_1]"): an earlier
            # pass took the name after the last title head, so there is nothing new here.
            continue
        tokens = _name_tokens(run)
        if tokens:
            yield text[tokens[0].start : tokens[-1].end]


# --- Names in running text (step 19f) -----------------------------------------------------------

# Words that end the name after a title: a title word, a party role, a unit, a signature form
# word or a connective ("Genel Müdür Ahmet Yılmaz ve ..." -> "Ahmet Yılmaz").
_CONNECTIVES = frozenset(_fold(w) for w in ("ve", "veya", "ile", "ya", "da", "de"))
_NOT_NAME_WORDS = (
    _TITLE_HEADS
    | _TITLE_MODIFIERS
    | _ROLE_WORDS
    | _UNIT_WORDS
    | _SIGNATURE_FORM_WORDS
    | _CONNECTIVES
)
# A name directly followed by a labelled identity number in parentheses:
# "Ahmet Yılmaz (TCKN: 12345678902)". The number itself is not needed ("(TCKN: [TCKN_1])").
_ID_IN_PARENS = re.compile(_HSPACE + r"*\(" + _HSPACE + "*" + _TCKN_LABEL)
# Words read before such an anchor, and after a title (one more than a name there may have).
_RUNNING_WORDS = 6
_AFTER_TITLE_WORDS = 4
# A lower-case suffix after an apostrophe, right before such an anchor ("Yılmaz'ın (TCKN").
_MAX_SUFFIX = 8
_SUFFIX_BEFORE = re.compile("['\u2019][^\\W\\d_]{1,6}$")
# One line break between two letters, not a paragraph break.
_LONE_BREAK = re.compile("(?<=[^\\W\\d_])\n(?=[^\\W\\d_])")


def _run_ending_at(text: str, end: int) -> list[_Token]:
    """The longest name run (`_scan_run`) of at most `_RUNNING_WORDS` words that ends exactly at
    `end`, on the line of `end`; [] when there is none."""
    line_start = text.rfind("\n", 0, end) + 1
    window = max(line_start, end - 64 * _RUNNING_WORDS)
    starts = [m.start() for m in _LETTERS.finditer(text, window, end)]
    for start in starts[-_RUNNING_WORDS:]:
        if start > line_start and (text[start - 1].isalnum() or text[start - 1] in _JOINERS + "."):
            continue  # not the start of a word
        run = _scan_run(text, start, limit=_RUNNING_WORDS)
        if run and run[-1].end == end:
            return run
    return []


def _sentence_start(text: str, pos: int) -> bool:
    """`pos` starts a sentence: nothing but spaces before it on its line, or a character that is
    not a letter, a digit or "," before the spaces ("1.2. Ahmet", ". Ahmet", "- Ahmet")."""
    line_start = text.rfind("\n", 0, pos) + 1
    before = text[line_start:pos].rstrip(_HSPACE_CHARS)
    return not before or not (before[-1].isalnum() or before[-1] in ",(" + _FILLER)


def _names_before_ids(text: str) -> Iterator[str]:
    """A name directly before a labelled identity number in parentheses (see `_ID_IN_PARENS`).

    The run of capitalised words before the parenthesis is cut after its last title head
    ("Proje Yöneticisi Ahmet Yılmaz" -> "Ahmet Yılmaz", "Av. Mehmet Demir" -> "Mehmet Demir");
    leading title modifiers, party roles and unit words are dropped ("Alıcı Ahmet Yılmaz"). One
    word is not a name. A run of three or more words that starts a sentence may start with a
    capitalised word that is not part of the name ("Taraflardan Ahmet Yılmaz"): both the run and
    the run without its first word are names, so neither spelling is left raw. A run of three or
    more words may start with a title that is not in the lists ("Kalite Uzmanı Ahmet Yılmaz"):
    its last two words are a name as well, so the bare name elsewhere is masked too.

    A suffix after the name ("Ahmet Yılmaz'ın (TCKN: ...)") and a single line break inside the
    run ("Proje Yöneticisi Ahmet\\nYılmaz (TCKN: ...)") still anchor.
    """
    # A copy in which a single line break between two words reads as a space (same length, so
    # positions hold). It is read only when the line holds one word of the run ("...Ahmet\nYılmaz
    # (TCKN"), so a name on its own line never takes the line above it.
    joined = _LONE_BREAK.sub(" ", text)
    for anchor in _ID_IN_PARENS.finditer(text):
        before = anchor.start()
        suffix = _SUFFIX_BEFORE.search(text, max(0, before - _MAX_SUFFIX), before)
        if suffix and not suffix.group()[1:].islower():
            suffix = None  # a joined name part ("O'Brien"), not a suffix
        end = suffix.start() if suffix else before
        run = _run_ending_at(text, end)
        if len(run) == 1 and run[0].start == text.rfind("\n", 0, end) + 1:
            run = _run_ending_at(joined, end)
            source = joined
        else:
            source = text
        heads = [i for i, token in enumerate(run) if _is_head(token)]
        tokens = run[heads[-1] + 1 :] if heads else run
        while tokens and _fold(tokens[0].word) in _NOT_NAME_WORDS:
            tokens = tokens[1:]
        if len(tokens) < 2:
            continue
        start, end = tokens[0].start, tokens[-1].end
        if _company_after(source, start, end):
            continue
        yield source[start:end]
        if len(tokens) >= 3 and not heads and _sentence_start(source, start):
            yield source[tokens[1].start : end]
        if len(tokens) >= 3:
            yield source[tokens[-2].start : end]


def _names_after_titles(text: str) -> Iterator[str]:
    """Two or three capitalised words after a title head in running text ("Teknik Direktör Zeynep
    Kaya", "Av. Mehmet Demir").

    The head is any word of `_TITLE_HEADS` (any case) followed by a space. The words after it are
    one name run (`_scan_run`) cut at the first word of `_NOT_NAME_WORDS` or a two-word role; the
    name is the part before the cut. Not a name: a first word that is itself such a word ("Genel
    Müdür Yardımcısı"), one word, four or more words (a heading such as "GENEL MÜDÜR ONAY YETKİ
    DEVRİ"), a company (`_company_after`), a part whose first word is written at least
    `_DEFINED_TERM_USES` more times in `text` than the part itself (a defined term such as
    "Sözleşme" or "Hizmet").
    """
    n = len(text)
    candidates: list[tuple[str, str]] = []  # (name, folded first word)
    for word in _LETTERS.finditer(text):
        end = word.end()
        folded = _fold(word.group())
        if end < n and text[end] == "." and folded + "." in _ABBREVIATIONS:
            end += 1
            folded += "."
        if folded not in _TITLE_HEADS or end >= n or text[end] not in _HSPACE_CHARS:
            continue
        run = _scan_run(text, end, limit=_AFTER_TITLE_WORDS)
        cut = 0
        while cut < len(run) and _fold(run[cut].word) not in _NOT_NAME_WORDS:
            if cut and (_fold(run[cut - 1].word), _fold(run[cut].word)) in _ROLE_PAIRS:
                cut -= 1
                break
            cut += 1
        if not 2 <= cut < _AFTER_TITLE_WORDS:
            continue
        start, stop = run[0].start, run[cut - 1].end
        if _company_after(text, start, stop):
            continue
        candidates.append((text[start:stop], _fold_case(run[0].word)))
    if not candidates:
        return
    # Counted once for all candidates, in one pass each (`_name_spans` reads every key at once).
    # Words are folded like `_name_spans` folds names.
    words = Counter(_fold_case(w.group()) for w in _LETTERS.finditer(text))
    # The uses of every candidate with the same first word are subtracted, so several people who
    # share a first name ("Av. Mehmet Kaya", "Genel Müdür Mehmet Demir") are not a defined term.
    by_first: dict[str, set[str]] = {}
    for name, first in candidates:
        by_first.setdefault(first, set()).add(_name_key(name))
    keys = set().union(*by_first.values())
    uses = Counter(_name_key(value) for value, _, _ in _name_spans(text, keys))
    for name, first in candidates:
        if words[first] - sum(uses[key] for key in by_first[first]) < _DEFINED_TERM_USES:
            yield name


def _names_in_running_text(text: str, values: Iterable[tuple[str, str]]) -> Iterator[str]:
    """The names of the two step 19f paths. They read a copy of `text` in which the (kind, value)
    pairs found without them are blanked, i.e. what re-masking sees: "Ahmet Yılmaz TR 18 0001 ..."
    gives "Ahmet Yılmaz", not "Ahmet Yılmaz TR" (which the IBAN would cut)."""
    values = [(kind, value) for kind, value in values if not _in_parentheses(kind, value)]
    if values:
        text = _blank_values(text, values)
    found: set[str] = set()
    for name in (*_names_before_ids(text), *_names_after_titles(text)):
        if name not in found:
            found.add(name)
            yield name


# --- Checksums ----------------------------------------------------------------------------------


def _tckn_valid(digits: str) -> bool:
    d = [int(ch) for ch in digits]
    if len(d) != 11 or d[0] == 0:
        return False
    d10 = ((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10
    return d[9] == d10 and d[10] == sum(d[:10]) % 10


def _iban_valid(iban: str) -> bool:
    compact = "".join(iban.split())
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(numeric) % 97 == 1


# --- Masking ------------------------------------------------------------------------------------


def _is_weak(kind: str, value: str) -> bool:
    """A spelling first found in step 17b: a spaced TCKN, a freely grouped IBAN or a mobile number
    without prefix. It only takes text that no strict value covers (see `_resolve`), so a value the
    older patterns find (e-mail, compact or standard-grouped IBAN, prefixed phone, compact TCKN,
    name) never loses its place to one."""
    if kind == "TCKN":
        return not value.isdigit()
    if kind == "IBAN":
        return _IBAN_STANDARD.fullmatch(value) is None
    return kind == "TELEFON" and value.startswith("5")


def _in_parentheses(kind: str, value: str) -> bool:
    """A phone number with its area code in parentheses, the spelling added last. It is placed
    after every other value, strict or weak (see `_resolve`): it only takes text none of them
    covers, so the text masked without this spelling is masked exactly the same with it."""
    return kind == "TELEFON" and "(" in value


def _discover(text: str) -> Iterator[tuple[str, str]]:
    """(kind, value) pairs found in `text`, in no particular order."""
    found: list[tuple[str, str]] = []
    for pattern, kind in (
        (_EMAIL, "EPOSTA"),
        (_IBAN, "IBAN"),
        (_PHONE, "TELEFON"),
        (_PHONE_BARE, "TELEFON"),
    ):
        for m in pattern.finditer(text):
            if kind != "IBAN" or _iban_valid(m.group()):
                found.append((kind, m.group()))
    spaced: list[tuple[int, str]] = []
    for m in _TCKN.finditer(text):
        value = m.group(1)
        if _THOUSANDS.fullmatch(value) or not _tckn_valid("".join(value.split())):
            continue
        if value.isdigit():
            found.append(("TCKN", value))
        else:
            spaced.append((m.start(), value))
    # The bare-phone searches and the dotted-number check below look at a copy in which the values
    # already found are blanked, i.e. at what re-masking sees: a digit that belongs to a found
    # value does not block ("21473873960 533 555 05 30", "21473873960,214 738 739 60"). Blanking
    # resolves overlaps like `mask` does (strict first), so a weak value that loses there is not
    # blanked here either.
    if found:
        for m in _PHONE_BARE.finditer(_blank_values(text, found)):
            found.append(("TELEFON", m.group()))
    spaced_ends: set[int] = set()
    if spaced:
        blanked = _blank_values(text, found)
        for start, value in spaced:
            dotted = _DOTTED_BEFORE.match(blanked, max(start - 2, 0), start)
            if dotted is None or start - 1 in spaced_ends:
                found.append(("TCKN", value))
                spaced_ends.add(start + len(value))
    if spaced_ends:  # a bare mobile number right after a spaced TCKN
        for m in _PHONE_BARE.finditer(_blank_values(text, found)):
            found.append(("TELEFON", m.group()))
    # Step 19f, after every search above, so what they find and blank is unchanged: a compact
    # number labelled as an identity number, whatever its checksum (see `_TCKN_LABELLED`).
    labelled = [("TCKN", m.group(1)) for m in _TCKN_LABELLED.finditer(text)]
    if labelled:  # a bare mobile number right after one, as after any found value
        found.extend(labelled)
        for m in _PHONE_BARE.finditer(_blank_values(text, found)):
            found.append(("TELEFON", m.group()))
    yield from found
    for name in _names_from_labels(text):
        yield "KISI", name


def _blank_values(text: str, found: Iterable[tuple[str, str]]) -> str:
    """`text` with every masked occurrence of the found values blanked by `_FILLER`, same length."""
    kind_of: dict[str, str] = {}
    for kind, value in found:
        kind_of.setdefault(value, kind)
    spans: list[tuple[int, int, str]] = []
    for start, end, value in _occurrences(text, kind_of):
        before, after = _BOUNDARY_PATTERNS[kind_of[value]]
        if before.match(text, start) and after.match(text, end):
            spans.append((start, end, value))
    weak = {value for value, kind in kind_of.items() if _is_weak(kind, value)}
    parts: list[str] = []
    position = 0
    for start, end, _ in _resolve(spans, weak):
        parts.append(text[position:start])
        parts.append(_FILLER * (end - start))
        position = end
    parts.append(text[position:])
    return "".join(parts)


def _validate(mapping: Mapping[str, str]) -> dict[str, int]:
    """Check every entry and return the highest N per kind."""
    highest = dict.fromkeys(KINDS, 0)
    for key, value in mapping.items():
        parsed = _MAPPING_KEY.fullmatch(key) if isinstance(key, str) else None
        if parsed is None:
            raise ValueError("PII eşlemesinde geçersiz bir yer tutucu var.")
        if not isinstance(value, str) or not value:
            raise ValueError("PII eşlemesinde boş ya da geçersiz bir değer var.")
        kind, number = parsed.group(1), int(parsed.group(2))
        highest[kind] = max(highest[kind], number)
    return highest


def _blank_placeholders(text: str) -> str:
    return PLACEHOLDER.sub(lambda m: _FILLER * len(m.group()), text)


_Span = tuple[int, int, str]


def _greedy(spans: Iterable[_Span], blocked: list[_Span]) -> list[_Span]:
    """Earliest start wins, the longer span on equal start; a span overlapping `blocked` (sorted,
    non-overlapping) is skipped."""
    kept: list[_Span] = []
    last_end = 0
    starts = [start for start, _, _ in blocked]
    for start, end, value in sorted(spans, key=lambda s: (s[0], s[0] - s[1])):
        if start < last_end:
            continue
        i = bisect.bisect_left(starts, end)
        if i and blocked[i - 1][1] > start:
            continue
        kept.append((start, end, value))
        last_end = end
    return kept


def _resolve(
    spans: list[_Span],
    weak: set[str],
    last: frozenset[str] = frozenset(),
    names: frozenset[str] = frozenset(),
    unlabelled: frozenset[str] = frozenset(),
) -> list[_Span]:
    """Overlapping spans are resolved in five rounds; each round only takes text that no span
    kept in an earlier round covers, and within a round the earliest start wins (the longer span
    on equal start).

    1. Strict values that are not names (e-mail, compact or standard-grouped IBAN, prefixed
       phone, compact TCKN).
    2. Names (`names`, every name spelling) other than those of `unlabelled`: a name glued to
       an e-mail ("Elif Soylu-elif@ornek.com") gives way, and the e-mail is masked as if no name
       were known (step 19d).
    3. Weak values (see `_is_weak`): they still give way to names and to strict values.
    4. Spans of `last` (see `_in_parentheses`).
    5. Names of `unlabelled` (one only the e-mail or signature path found, see `mask`)."""
    later = last | names
    strict = _greedy((s for s in spans if s[2] not in weak and s[2] not in later), [])
    labelled = (s for s in spans if s[2] in names and s[2] not in unlabelled)
    strong = sorted(strict + _greedy(labelled, strict))
    kept = sorted(strong + _greedy((s for s in spans if s[2] in weak), strong))
    if last:
        kept = sorted(kept + _greedy((s for s in spans if s[2] in last), kept))
    if unlabelled:
        kept = sorted(kept + _greedy((s for s in spans if s[2] in unlabelled), kept))
    return kept


_BOUNDARY_PATTERNS = {
    kind: (re.compile(before), re.compile(after)) for kind, (before, after) in _BOUNDARIES.items()
}


def _occurrences(text: str, values: Iterable[str]) -> Iterator[tuple[int, int, str]]:
    """Every (start, end, value) occurrence of every value, overlaps included (Aho-Corasick).

    One pass over `text` whatever the number of values, so an input with thousands of distinct
    numbers or names stays linear instead of one full-text search per value.
    """
    goto: list[dict[str, int]] = [{}]
    term: list[str | None] = [None]
    for value in values:
        node = 0
        for ch in value:
            child = goto[node].get(ch)
            if child is None:
                child = len(goto)
                goto[node][ch] = child
                goto.append({})
                term.append(None)
            node = child
        term[node] = value
    fail = [0] * len(goto)
    out = [0] * len(goto)  # the nearest proper suffix node that ends a value
    queue = list(goto[0].values())
    for node in queue:
        for ch, child in goto[node].items():
            f = fail[node]
            while f and ch not in goto[f]:
                f = fail[f]
            target = goto[f].get(ch, 0)
            fail[child] = target if target != child else 0
            out[child] = fail[child] if term[fail[child]] is not None else out[fail[child]]
            queue.append(child)

    state = 0
    for i, ch in enumerate(text):
        while state and ch not in goto[state]:
            state = fail[state]
        state = goto[state].get(ch, 0)
        node = state if term[state] is not None else out[state]
        while node:
            found = term[node]
            if found is not None:
                yield i + 1 - len(found), i + 1, found
            node = out[node]


def _fold_case(text: str) -> str:
    """Length-preserving case fold: `İ`, `I`, `ı` and `i` are one letter; diacritics are kept."""
    out: list[str] = []
    for ch in text:
        if ch in "İIı":
            out.append("i")
        else:
            lowered = ch.lower()
            out.append(lowered if len(lowered) == 1 else ch)
    return "".join(out)


def _name_key(value: str) -> str:
    return _fold_case(" ".join(value.split()))


def _name_spans(scan: str, keys: Iterable[str]) -> Iterator[tuple[str, int, int]]:
    """(spelling, start, end) of every occurrence in `scan` of a name key (see `_name_key`):
    any letter case, any whitespace between the words, with the name boundaries."""
    wanted = set(keys)
    wanted.discard("")
    if not wanted:
        return
    collapsed = normalize(scan, ("collapse_whitespace",))
    before, after = _BOUNDARY_PATTERNS["KISI"]
    for key_start, key_end, _ in _occurrences(_fold_case(collapsed.text), wanted):
        start, end = collapsed.span(key_start, key_end)
        if before.match(scan, start) and after.match(scan, end):
            yield scan[start:end], start, end


def unlabelled_names(text: str) -> frozenset[str]:
    """Names that only the e-mail, signature and running-text paths find in `text`, the label
    path not.

    `prepare_contract` takes them from the whole contract and passes them to `mask` for every
    clause, where the clause alone may not show how a name was found.
    """
    scan = _blank_placeholders(text)
    labelled = {_name_key(name) for name in _names_from_labels(scan)}
    found = [
        *_names_from_emails(scan),
        *_names_from_signature_lines(scan),
        *_names_in_running_text(scan, (v for v in _discover(scan) if v[0] != "KISI")),
    ]
    return frozenset(name for name in found if _name_key(name) not in labelled)


def mask(
    text: str, mapping: Mapping[str, str], unlabelled: Iterable[str] | None = None
) -> tuple[MaskedText, dict[str, str]]:
    """Replace PII in `text` with placeholders.

    Returns the masked text and a new mapping: every entry of `mapping` plus one per new value,
    numbered per kind after the highest existing number, in order of first appearance. The same
    value (exact spelling) always gets the same placeholder. Placeholders already in `text` are
    left untouched, so masking masked text with the returned mapping changes nothing.

    A name gives way to a strict value that is not a name (step 19d; see `_resolve`): where one
    of its occurrences overlaps an e-mail, a compact or standard-grouped IBAN, a prefixed phone
    number or a compact TCKN ("Elif Soylu-elif@ornek.com", where the e-mail is
    "Soylu-elif@ornek.com"), that value is masked exactly as if no name were known and the
    occurrence of the name is left as it is ("Elif [EPOSTA_1]"); its other occurrences are
    masked. This holds for every name: labelled, found by the e-mail or signature path, or given
    in `mapping`. The step 17b spellings and the phone with its area code in parentheses still
    give way to a name, as before. Between two names, a name only the e-mail or
    signature path (step 19c) or a running-text path (step 19f) finds gives way to the other one:
    such a name is one these paths find in `text` and the label path does not, unless `mapping`
    already holds it, or one listed in `unlabelled` (see `unlabelled_names`). Such names are placed
    last, so they only take text that no other value covers.

    The running-text paths decide with evidence from the whole text (a defined term is one used
    often), so a call given `unlabelled` (even empty: one clause of a text whose names
    `unlabelled_names` already took) does not run them; their names reach it through `mapping`.

    A compact 11-digit number after an identity label ("TCKN:", "T.C. Kimlik No") is a TCKN
    whatever its checksum (step 19f); an unlabelled one keeps the checksum rule.
    """
    counters = {kind: n + 1 for kind, n in _validate(mapping).items()}
    result: dict[str, str] = dict(mapping)
    placeholder_of: dict[str, str] = {}
    kind_of: dict[str, str] = {}
    for placeholder, value in mapping.items():
        if value not in placeholder_of:
            placeholder_of[value] = placeholder
            kind_of[value] = placeholder[1 : placeholder.rindex("_")]

    scan = _blank_placeholders(text)
    known_keys = {_name_key(value) for value, kind in kind_of.items() if kind == "KISI"}
    labelled_keys: set[str] = set()
    for kind, value in _discover(scan):
        kind_of.setdefault(value, kind)
        if kind == "KISI":
            labelled_keys.add(_name_key(value))
    # Outside `_discover`: what it finds, and what it blanks for its later passes, stays exactly
    # what it was before this spelling existed.
    for m in _PHONE_PARENS.finditer(scan):
        kind_of.setdefault(m.group(1), "TELEFON")
    # The two step 19c paths and the running-text paths of step 19f: their names are placed
    # last (see `_resolve`), so they only take text that nothing found without them covers.
    yielding_keys = {_name_key(name) for name in unlabelled or ()}
    running = (
        _names_in_running_text(scan, ((k, v) for v, k in kind_of.items() if k != "KISI"))
        if unlabelled is None
        else ()
    )
    for name in (*_names_from_emails(scan), *_names_from_signature_lines(scan), *running):
        kind_of.setdefault(name, "KISI")
        if _name_key(name) not in known_keys:
            yielding_keys.add(_name_key(name))
    yielding_keys -= labelled_keys

    spans: list[tuple[int, int, str]] = []
    exact = [value for value, kind in kind_of.items() if kind != "KISI"]
    for start, end, value in _occurrences(scan, exact):
        before, after = _BOUNDARY_PATTERNS[kind_of[value]]
        if before.match(scan, start) and after.match(scan, end):
            spans.append((start, end, value))

    # Known names are searched whatever their letter case and the whitespace between their words;
    # every distinct spelling found is its own value, so unmasking restores the text exactly.
    name_keys = {_name_key(value) for value, kind in kind_of.items() if kind == "KISI"}
    yielding: set[str] = set()
    for _, start, end in _name_spans(scan, name_keys):
        spelling = text[start:end]
        kind_of.setdefault(spelling, "KISI")
        spans.append((start, end, spelling))
        if _name_key(spelling) in yielding_keys:
            yielding.add(spelling)

    weak = {value for value, kind in kind_of.items() if _is_weak(kind, value)}
    # A value with "(" is told apart by its spelling, in this call and in every later call that
    # gets the mapping (per clause, per edited draft): it is placed last each time. Nothing else
    # is discovered because of it, so the values of the two earlier rounds are exactly the ones
    # found without this spelling.
    last = frozenset(value for value, kind in kind_of.items() if _in_parentheses(kind, value))
    parts: list[str] = []
    position = 0
    names = frozenset(value for value, kind in kind_of.items() if kind == "KISI")
    for start, end, value in _resolve(spans, weak, last, names, frozenset(yielding)):
        assigned = placeholder_of.get(value)
        if assigned is None:
            kind = kind_of[value]
            assigned = f"[{kind}_{counters[kind]}]"
            counters[kind] += 1
            placeholder_of[value] = assigned
            result[assigned] = value
        parts.append(text[position:start])
        parts.append(assigned)
        position = end
    parts.append(text[position:])
    return MaskedText("".join(parts)), result


def unmask(text: str, mapping: Mapping[str, str]) -> str:
    """Put the raw values back; placeholders missing from `mapping` stay as they are."""
    return PLACEHOLDER.sub(lambda m: mapping.get(m.group(), m.group()), text)
