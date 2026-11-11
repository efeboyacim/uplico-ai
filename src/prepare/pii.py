"""PII masking: TCKN, IBAN, phone, e-mail and person names become `[KIND_N]` placeholders.

Amounts, durations, dates, percentages, clause numbers and company names are never masked; the
rules need them. Masking is pure: no I/O, no logging, and no raw value in an exception message.
"""

import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass

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
# Compact or in the standard print grouping ("TR33 0006 1005 1978 6457 8413 26"): at most one
# space or no-break space at each group boundary. Other groupings are a documented limit.
_IBAN_GAP = "[ \u00a0]?"
_IBAN = re.compile(
    _ALNUM_BEFORE
    + r"TR[0-9]{2}(?:"
    + _IBAN_GAP
    + r"[0-9]{4}){5}"
    + _IBAN_GAP
    + r"[0-9]{2}"
    + _ALNUM_AFTER
)
_TCKN = re.compile(r"(?<![0-9])[1-9][0-9]{10}(?![0-9])")
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
_LABEL = re.compile(
    r"(?<![^\W_])(?:(?:"
    + "|".join(_loose(w) for w in ("Adı Soyadı", "Ad Soyad", "Yetkili"))
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


def _joined_end(text: str, end: int) -> int:
    """End of the name word that starts before `end`, following joiners (see `_JOINERS`).

    The joiner separates two things, and the word ends before it, when the part after it is a
    title word, a form word or a label, or starts a company name ("Yılmaz-ABC A.Ş.").
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
        rest = _scan_run(text, end + 1, join=False)
        if not rest:
            break  # a form word, a label or a word followed by ":"
        if _COMPANY_MARKER.search(text[end + 1 : _guard_end(text, rest[-1].end, join=False)]):
            break
        end = part.end()
    return end


def _scan_run(text: str, pos: int, join: bool = True) -> list[_Token]:
    """Consecutive capitalised name words from `pos` on the same line (see the name rule)."""
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
            end = _joined_end(text, end)
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


def _guard_end(text: str, end: int, join: bool = True) -> int:
    """End of the text checked for a company marker: the run, any "ve ..." continuation of it
    ("X Sanayi ve Ticaret A.Ş.") and the token directly after."""
    while True:
        joined = _AND.match(text, end)
        more = _scan_run(text, joined.end(), join) if joined else []
        if not more:
            return _next_chunk_end(text, end)
        end = more[-1].end


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


def _discover_names(text: str) -> Iterator[str]:
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


def _discover(text: str) -> Iterator[tuple[str, str]]:
    """(kind, value) pairs found in `text`, in no particular order."""
    for m in _EMAIL.finditer(text):
        yield "EPOSTA", m.group()
    for m in _IBAN.finditer(text):
        if _iban_valid(m.group()):
            yield "IBAN", m.group()
    for m in _TCKN.finditer(text):
        if _tckn_valid(m.group()):
            yield "TCKN", m.group()
    for m in _PHONE.finditer(text):
        yield "TELEFON", m.group()
    for name in _discover_names(text):
        yield "KISI", name


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


def _resolve(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Earliest start wins; on equal start the longer span wins."""
    kept: list[tuple[int, int, str]] = []
    last_end = 0
    for start, end, value in sorted(spans, key=lambda s: (s[0], s[0] - s[1])):
        if start >= last_end:
            kept.append((start, end, value))
            last_end = end
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


def mask(text: str, mapping: Mapping[str, str]) -> tuple[MaskedText, dict[str, str]]:
    """Replace PII in `text` with placeholders.

    Returns the masked text and a new mapping: every entry of `mapping` plus one per new value,
    numbered per kind after the highest existing number, in order of first appearance. The same
    value (exact spelling) always gets the same placeholder. Placeholders already in `text` are
    left untouched, so masking masked text with the returned mapping changes nothing.
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
    for kind, value in _discover(scan):
        kind_of.setdefault(value, kind)

    spans: list[tuple[int, int, str]] = []
    exact = [value for value, kind in kind_of.items() if kind != "KISI"]
    for start, end, value in _occurrences(scan, exact):
        before, after = _BOUNDARY_PATTERNS[kind_of[value]]
        if before.match(scan, start) and after.match(scan, end):
            spans.append((start, end, value))

    # Known names are searched whatever their letter case and the whitespace between their words;
    # every distinct spelling found is its own value, so unmasking restores the text exactly.
    name_keys = {_name_key(value) for value, kind in kind_of.items() if kind == "KISI"}
    name_keys.discard("")
    if name_keys:
        collapsed = normalize(scan, ("collapse_whitespace",))
        before, after = _BOUNDARY_PATTERNS["KISI"]
        for key_start, key_end, _ in _occurrences(_fold_case(collapsed.text), name_keys):
            start, end = collapsed.span(key_start, key_end)
            if before.match(scan, start) and after.match(scan, end):
                spelling = text[start:end]
                kind_of.setdefault(spelling, "KISI")
                spans.append((start, end, spelling))

    parts: list[str] = []
    position = 0
    for start, end, value in _resolve(spans):
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
