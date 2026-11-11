"""Split a contract into clauses with offsets into the original text."""

import re

from src.models import RawClause

_DASHES = "".join(chr(code) for code in (0x2013, 0x2014))  # en dash, em dash
# Line boundaries are spelled out because "^" and "$" only know LF; contracts uploaded from
# Windows use CRLF.
_LINE_START = r"(?:\A|(?<=[\r\n]))"
_LINE_END = r"(?=[\r\n]|\Z)"
_NEWLINE = r"(?:\r\n|\r(?!\n)|\n)"

# "Madde 3.2 uyarınca" at the start of a line is a reference: "." must not be followed by a digit.
_HEADING = re.compile(
    _LINE_START
    + r"[ \t]*(?:#+[ \t]+)?(?:\*\*)?(?:Madde|MADDE)[ \t]+(\d+)"
    + r"(?:[ \t]*(?:\.(?!\d)|[:\-"
    + _DASHES
    + r"])|[ \t]*\*\*|[ \t]*"
    + _LINE_END
    + r")"
)

_PARAGRAPH_BREAK = re.compile(_NEWLINE + r"[^\S\r\n]*" + _NEWLINE)

_SPECIAL_CASE = {"i": "[iİ]", "İ": "[iİ]", "ı": "[ıI]", "I": "[ıI]"}


def _any_case(word: str) -> str:
    """Regex matching `word` in any case, with explicit Turkish dotted/dotless i handling."""
    parts: list[str] = []
    for ch in word:
        if ch == " ":
            parts.append(r"[ \t]+")
        elif ch in _SPECIAL_CASE:
            parts.append(_SPECIAL_CASE[ch])
        else:
            parts.append(f"[{ch.lower()}{ch.upper()}]")
    return "".join(parts)


_SIGNATURE = re.compile(
    _LINE_START
    + r"[ \t]*(?:#+[ \t]*)?(?:\*\*)?(?:"
    + "|".join(_any_case(w) for w in ("İmzalar", "İmza", "Taraflar adına"))
    + r")[ \t]*(?:"
    + _LINE_END
    + r"|:|\*\*)"
)


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _split_by_headings(
    text: str, headings: list[re.Match[str]]
) -> list[tuple[int, int, str | None]]:
    spans: list[tuple[int, int, str | None]] = [(0, headings[0].start(), None)]
    tail_start = len(text)
    signature = _SIGNATURE.search(text, headings[-1].end())
    if signature is not None:
        tail_start = signature.start()
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else tail_start
        spans.append((heading.start(), end, heading.group(1)))
    if signature is not None:
        spans.append((tail_start, len(text), None))
    return spans


def _split_paragraphs(text: str) -> list[tuple[int, int, str | None]]:
    spans: list[tuple[int, int, str | None]] = []
    position = 0
    for match in _PARAGRAPH_BREAK.finditer(text):
        spans.append((position, match.start(), None))
        position = match.end()
    spans.append((position, len(text), None))
    return spans


def split_clauses(text: str) -> list[RawClause]:
    """Split `text` into clauses; each clause satisfies `text == original[start:end]`."""
    headings = list(_HEADING.finditer(text))
    spans = _split_by_headings(text, headings) if headings else _split_paragraphs(text)
    clauses: list[RawClause] = []
    for raw_start, raw_end, clause_no in spans:
        start, end = _trim(text, raw_start, raw_end)
        if start >= end:
            continue
        clauses.append(
            RawClause(
                clause_id=f"c{len(clauses) + 1}",
                clause_no=clause_no,
                start=start,
                end=end,
                text=text[start:end],
            )
        )
    return clauses
