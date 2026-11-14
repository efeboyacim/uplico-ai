"""Split a contract into clauses with offsets into the original text."""

import re

from src.models import RawClause

_DASHES = "".join(chr(code) for code in (0x2013, 0x2014))  # en dash, em dash
# Line boundaries are spelled out because "^" and "$" only know LF; contracts uploaded from
# Windows use CRLF.
_LINE_START = r"(?:\A|(?<=[\r\n]))"
_LINE_END = r"(?=[\r\n]|\Z)"
_NEWLINE = r"(?:\r\n|\r(?!\n)|\n)"

# One byte order mark at the very start of the text (editors write it) is the start of the first
# line: a heading right after it is still a heading, and the mark stays in that clause's text.
_BOM = chr(0xFEFF)
_HEADING_START = r"(?:\A" + _BOM + r"?|(?<=[\r\n]))"

# "Madde 3.2 uyarınca" at the start of a line is a reference: "." must not be followed by a digit.
_HEADING = re.compile(
    _HEADING_START
    + r"[ \t]*(?:#+[ \t]+)?(?:\*\*)?(?:Madde|MADDE)[ \t]+(\d+)"
    + r"(?:[ \t]*(?:\.(?!\d)|[:\-"
    + _DASHES
    + r"])|[ \t]*\*\*|[ \t]*"
    + _LINE_END
    + r")"
)

# "## 4. Fesih" / "## **4.** Fesih": a Markdown heading whose title starts with a top-level number
# (1-3 digits, then a "." not followed by a digit, then a title or the end of the line). Used only
# when the text has no "Madde N" heading, and only at the "#" level of the first such heading.
# Not a heading: "## 4.1. Alt", "## 14.11.2026", "## 1.200 TL", "## 2026. Yıl", "## I. Giriş",
# an unmarked "4. Başlık" or a numbered list; a sub-item stays inside its top-level clause.
_NUMBERED_HEADING = re.compile(
    _HEADING_START
    + r"[ \t]*#+[ \t]+(?:\*\*)?(\d{1,3})\.(?!\d)(?:\*\*)?(?:[ \t]+(?=\S)|[ \t]*"
    + _LINE_END
    + r")"
)

# "7. SORUMLULUĞUN SINIRLANDIRILMASI": a plain line with a top-level number, a "." not followed by
# a digit, and a title. Used only when the text has neither a "Madde N" nor a numbered Markdown
# heading; see `_plain_headings` for which lines count. Not a heading: a numbered list, a
# sentence, "4.1. Alt", "14.11.2026", "1.200 TL", "4. Başlık" with a lowercase letter.
# The title runs greedily to the line end and trailing spaces / tabs are trimmed in Python: a lazy
# title followed by an optional space run backtracks quadratically on a long run of spaces.
_PLAIN_HEADING = re.compile(_HEADING_START + r"[ \t]*(\d{1,3})\.(?!\d)[ \t]+(\S[^\r\n]*)")
_PLAIN_TITLE_MAX = 100
# The section body starts on the next line: one line break, then a line that is not blank.
_BODY_NEXT_LINE = re.compile(_NEWLINE + r"[^\S\r\n]*[^\s]")

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


def _heading_level(heading: re.Match[str]) -> int:
    line = heading.group(0).lstrip(_BOM + " \t")
    return len(line) - len(line.lstrip("#"))


def _numbered_headings(text: str) -> list[re.Match[str]]:
    """Numbered Markdown headings at the "#" level of the first one ("### 1." under "## 2." is
    not a heading)."""
    headings = list(_NUMBERED_HEADING.finditer(text))
    if not headings:
        return []
    level = _heading_level(headings[0])
    return [heading for heading in headings if _heading_level(heading) == level]


def _starts_paragraph(text: str, start: int) -> bool:
    """True if the line at `start` is the first line of the text or follows a blank line."""
    if start == 0:
        return True
    position = start
    if text[position - 1] == "\n":
        position -= 1
    if position > 0 and text[position - 1] == "\r":
        position -= 1
    while position > 0 and text[position - 1] not in "\r\n" and text[position - 1].isspace():
        position -= 1
    return position == 0 or text[position - 1] in "\r\n"


def _is_plain_title(title: str) -> bool:
    return (
        len(title) <= _PLAIN_TITLE_MAX
        and any(ch.isalpha() for ch in title)
        and not any(ch.islower() for ch in title)
        and title[-1] not in ".,;:!?"
    )


def _first_sub_item(text: str, heading: re.Match[str]) -> bool:
    """The line right after the heading starts with the heading's own sub-item ("7.1")."""
    body = _BODY_NEXT_LINE.match(text, heading.end())
    if body is None:
        return False
    marker = heading.group(1) + "."
    start = body.end() - 1
    after = start + len(marker)
    return text.startswith(marker, start) and after < len(text) and text[after].isdigit()


def _plain_headings(text: str) -> list[re.Match[str]]:
    """Plain numbered headings: lines with an all-caps title without closing punctuation at the
    start of a paragraph, directly followed by a line that starts with the heading's own sub-item
    ("7. BAŞLIK" then "7.1. ..."). A numbered list item followed by a blank line, by the end of
    the text or by a plain detail line ("1. ALICI" then "Unvan: ...") is not a heading. All or
    nothing: at least two, numbered 1, 2, 3 ... in order."""
    headings = [
        heading
        for heading in _PLAIN_HEADING.finditer(text)
        if _is_plain_title(heading.group(2).rstrip(" \t"))
        and _starts_paragraph(text, heading.start())
        and _first_sub_item(text, heading)
    ]
    if len(headings) < 2:
        return []
    if [int(heading.group(1)) for heading in headings] != list(range(1, len(headings) + 1)):
        return []
    return headings


def split_clauses(text: str) -> list[RawClause]:
    """Split `text` into clauses; each clause satisfies `text == original[start:end]`."""
    headings = list(_HEADING.finditer(text)) or _numbered_headings(text) or _plain_headings(text)
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
