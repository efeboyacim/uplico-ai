"""The one place that normalizes text and maps normalized positions back to the original.

Every output character of `normalize` remembers the half-open range of the original text it came
from. Ranges are monotonic (non-decreasing in both ends), so a range of the normalized text maps to
one contiguous slice of the original.

Round-trip guarantee: call an index ``k`` a *boundary* when ``k == 0``, ``k == len(text)`` or the
sources of characters ``k - 1`` and ``k`` do not overlap (``ends[k - 1] <= starts[k]``). For any
range ``[a, b)`` whose ends are both boundaries, ``normalize(original[s:e], steps).text ==
normalized.text[a:b]`` where ``(s, e) = normalized.span(a, b)``. A range that cuts through the
output of one source unit (one half of an expanded ligature, say) still maps to the whole unit.

Known limit: NFKC runs per cluster (a character plus its combining marks), so compositions
whose second character is not a combining mark (Hangul jamo, some Indic vowel signs) are not
composed. Turkish and Latin text is unaffected.
"""

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

NormStep = Literal["strip_invisible", "nfkc", "collapse_whitespace", "unify_quotes_dashes"]

# Steps always run in this order, whatever order the caller gives.
CANONICAL_ORDER: tuple[NormStep, ...] = (
    "strip_invisible",
    "nfkc",
    "collapse_whitespace",
    "unify_quotes_dashes",
)
ALL_STEPS: frozenset[NormStep] = frozenset(CANONICAL_ORDER)

_SINGLE_QUOTES = "\u2018\u2019\u201a\u201b\u2039\u203a"
_DOUBLE_QUOTES = "\u201c\u201d\u201e\u201f\u00ab\u00bb"
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"
_QUOTE_DASH_MAP: dict[str, str] = {
    **dict.fromkeys(_SINGLE_QUOTES, "'"),
    **dict.fromkeys(_DOUBLE_QUOTES, '"'),
    **dict.fromkeys(_DASHES, "-"),
}

# (character, source start, source end) in the original text.
_Unit = tuple[str, int, int]


def is_invisible(ch: str) -> bool:
    """The single definition of the invisible set: Unicode general category Cf."""
    return unicodedata.category(ch) == "Cf"


def strip_invisible(text: str) -> str:
    """Remove every invisible character and nothing else."""
    return "".join(ch for ch in text if not is_invisible(ch))


@dataclass(frozen=True)
class NormalizedText:
    """Normalized text plus, per character, the source range in the original text."""

    text: str
    starts: tuple[int, ...]
    ends: tuple[int, ...]

    def span(self, start: int, end: int) -> tuple[int, int]:
        """Map the half-open range ``[start, end)`` of `text` to a range of the original.

        A non-empty range maps from the source start of its first character to the source end of
        its last character. An empty range at ``k`` maps to the empty range right after the
        source of character ``k - 1`` (``(0, 0)`` when ``k == 0``).
        """
        if not 0 <= start <= end <= len(self.text):
            raise ValueError(f"Geçersiz aralık: [{start}, {end}), metin uzunluğu {len(self.text)}.")
        if start == end:
            pos = self.ends[start - 1] if start > 0 else 0
            return pos, pos
        return self.starts[start], self.ends[end - 1]

    def is_boundary(self, index: int) -> bool:
        """True when a range may start or end at `index` without cutting a source unit."""
        if index in (0, len(self.text)):
            return True
        return self.ends[index - 1] <= self.starts[index]


def _strip(units: list[_Unit]) -> list[_Unit]:
    return [u for u in units if not is_invisible(u[0])]


def _nfkc(units: list[_Unit]) -> list[_Unit]:
    # A cluster is a character plus the combining marks that follow it; NFKC runs per cluster so
    # that e.g. "e" + U+0301 composes, and every output character maps to the whole cluster.
    out: list[_Unit] = []
    i = 0
    while i < len(units):
        j = i + 1
        while j < len(units) and unicodedata.combining(units[j][0]):
            j += 1
        cluster = "".join(u[0] for u in units[i:j])
        src_start, src_end = units[i][1], units[j - 1][2]
        out.extend((ch, src_start, src_end) for ch in unicodedata.normalize("NFKC", cluster))
        i = j
    return out


def _collapse_whitespace(units: list[_Unit]) -> list[_Unit]:
    out: list[_Unit] = []
    i = 0
    while i < len(units):
        if not units[i][0].isspace():
            out.append(units[i])
            i += 1
            continue
        j = i + 1
        while j < len(units) and units[j][0].isspace():
            j += 1
        out.append((" ", units[i][1], units[j - 1][2]))
        i = j
    return out


def _unify_quotes_dashes(units: list[_Unit]) -> list[_Unit]:
    return [(_QUOTE_DASH_MAP.get(ch, ch), s, e) for ch, s, e in units]


_STEP_FUNCS = {
    "strip_invisible": _strip,
    "nfkc": _nfkc,
    "collapse_whitespace": _collapse_whitespace,
    "unify_quotes_dashes": _unify_quotes_dashes,
}


def normalize(text: str, steps: Iterable[NormStep]) -> NormalizedText:
    """Apply the given steps (in canonical order) and keep each character's source range."""
    wanted = set(steps)
    unknown = wanted - ALL_STEPS
    if unknown:
        raise ValueError(f"Bilinmeyen normalleştirme adımı: {sorted(unknown)}")
    units: list[_Unit] = [(ch, i, i + 1) for i, ch in enumerate(text)]
    for step in CANONICAL_ORDER:
        if step in wanted:
            units = _STEP_FUNCS[step](units)
    return NormalizedText(
        text="".join(u[0] for u in units),
        starts=tuple(u[1] for u in units),
        ends=tuple(u[2] for u in units),
    )
