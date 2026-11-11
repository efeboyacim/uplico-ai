"""Prompt-injection scan: suspicious phrases, the delimiter tag and invisible characters.

The scan only flags; it never edits or removes text. All offsets are absolute offsets into the
stored original text.
"""

import re
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Sequence

from src.models import InjectionFlag, RawClause
from src.text import NormalizedText, NormStep, is_invisible, normalize, strip_invisible

_SCAN_STEPS: tuple[NormStep, ...] = ("strip_invisible", "nfkc", "collapse_whitespace")

_FOLD_MAP: dict[str, str] = {
    "İ": "i",
    "I": "i",
    "ı": "i",
    "\u00e2": "a",  # circumflex vowels
    "\u00ee": "i",
    "\u00fb": "u",
    "\u00c2": "a",
    "\u00ce": "i",
    "\u00db": "u",
}

# Written in folded form: lower case, dotless/dotted i as "i", circumflex vowels without the hat.
# Whitespace is already collapsed to single spaces. Each pattern stays narrow on purpose.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("yapay_zeka", re.compile(r"\byapay zeka")),
    ("bu_metni_inceleyen", re.compile(r"\bbu metni inceleyen")),
    (
        "onceki_talimatlari_yok_say",
        re.compile(
            r"\b[oö]nceki (?:t[uü]m |b[uü]t[uü]n )?talimatlar\w* "
            r"(?:yok say|g[oö]rmezden gel|unut)"
        ),
    ),
    ("tum_maddeleri_uygun", re.compile(r"\bt[uü]m maddeleri uygun")),
    (
        "ignore_previous_instructions",
        re.compile(r"\bignore (?:all |any |the |all the )?(?:previous|prior|above) instructions"),
    ),
    (
        "disregard_instructions",
        re.compile(
            r"\bdisregard (?:all |any |the |all the )?(?:previous |prior |above )?instructions"
        ),
    ),
    (
        "delimiter_tag",
        re.compile(
            r"<\s*/?\s*"
            + r"\s*".join(
                {"o": "[oö]", "s": "[sş]", "_": "_"}.get(ch, re.escape(ch))
                for ch in "sozlesme_maddesi"
            )
        ),
    ),
)

_SENTENCE_END = re.compile(r"[.!?](?= |$)")
_BOM = "\ufeff"


def _fold(text: str) -> str:
    """Length-preserving case fold used only for pattern matching."""
    out: list[str] = []
    for ch in text:
        mapped = _FOLD_MAP.get(ch)
        if mapped is None:
            lowered = ch.lower()
            mapped = lowered if len(lowered) == 1 else ch
        out.append(mapped)
    return "".join(out)


def _sentence_breaks(norm: NormalizedText, source: str) -> list[int]:
    """Sorted indexes of `norm.text` where a sentence may start or end.

    A sentence ends at ``.``, ``!`` or ``?`` followed by a space, or at a line break of the
    original (a collapsed space whose source holds a line break), so a heading stays out.
    """
    text = norm.text
    breaks = {0, len(text)}
    breaks.update(m.end() for m in _SENTENCE_END.finditer(text))
    for i, ch in enumerate(text):
        if ch == " ":
            chunk = source[norm.starts[i] : norm.ends[i]]
            if "\n" in chunk or "\r" in chunk:
                breaks.add(i)
    return sorted(breaks)


def _pattern_flags(clause: RawClause) -> list[InjectionFlag]:
    norm = normalize(clause.text, _SCAN_STEPS)
    folded = _fold(norm.text)
    breaks = _sentence_breaks(norm, clause.text)
    flags: list[InjectionFlag] = []
    for name, pattern in _PATTERNS:
        for m in pattern.finditer(folded):
            src_start, src_end = norm.span(m.start(), m.end())
            sent_start = breaks[bisect_right(breaks, m.start()) - 1]
            sent_end = breaks[bisect_left(breaks, m.end())]
            phrase_start, phrase_end = norm.span(sent_start, sent_end)
            flags.append(
                InjectionFlag(
                    clause_id=clause.clause_id,
                    kind="pattern",
                    start=clause.start + src_start,
                    end=clause.start + src_end,
                    # The contract's own wording; only invisible characters are removed.
                    phrase=strip_invisible(clause.text[phrase_start:phrase_end]).strip(),
                    detail=name,
                )
            )
    return flags


def _hidden_flags(clause: RawClause) -> list[InjectionFlag]:
    flags: list[InjectionFlag] = []
    text = clause.text
    i = 0
    while i < len(text):
        if not is_invisible(text[i]):
            i += 1
            continue
        j = i + 1
        while j < len(text) and is_invisible(text[j]):
            j += 1
        run = text[i:j]
        abs_start = clause.start + i
        if not (abs_start == 0 and run == _BOM):
            counts = Counter(run)
            detail = ", ".join(f"U+{ord(ch):04X} x{n}" for ch, n in counts.items())
            flags.append(
                InjectionFlag(
                    clause_id=clause.clause_id,
                    kind="hidden_characters",
                    start=abs_start,
                    end=clause.start + j,
                    phrase="",
                    detail=detail,
                )
            )
        i = j
    return flags


def scan_injection(text: str, clauses: Sequence[RawClause]) -> list[InjectionFlag]:
    """Flag suspicious phrases and invisible-character runs in every clause, sorted by start."""
    flags: list[InjectionFlag] = []
    for clause in clauses:
        if text[clause.start : clause.end] != clause.text:
            raise ValueError(
                f"{clause.clause_id} maddesinin metni asıl metindeki konumuyla uyuşmuyor."
            )
        flags.extend(_pattern_flags(clause))
        flags.extend(_hidden_flags(clause))
    flags.sort(key=lambda f: (f.start, f.end, f.kind, f.detail or ""))
    return flags
