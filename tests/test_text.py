import random

import pytest

from src.text import ALL_STEPS, NormStep, is_invisible, normalize, strip_invisible

ALL: tuple[NormStep, ...] = (
    "strip_invisible",
    "nfkc",
    "collapse_whitespace",
    "unify_quotes_dashes",
)


def _slice_back(original: str, steps: tuple[NormStep, ...], start: int, end: int) -> str:
    norm = normalize(original, steps)
    s, e = norm.span(start, end)
    return original[s:e]


def test_is_invisible_cf_chars_true() -> None:
    for ch in ("\u200b", "\u200d", "\u202e", "\u00ad", "\ufeff", "\U000e0041", "\u2060"):
        assert is_invisible(ch)
    for ch in ("a", " ", "\n", "\u00a0", "\u0301", "ı"):
        assert not is_invisible(ch)


def test_strip_invisible_mixed_text_keeps_visible_identical() -> None:
    text = "Ödeme\u200b 30\u00a0gün\u202e içinde \u201cyapılır\u201d\U000e0041.\r\n\ufb01"
    expected = "Ödeme 30\u00a0gün içinde \u201cyapılır\u201d.\r\n\ufb01"
    assert strip_invisible(text) == expected
    assert strip_invisible(expected) == expected


def test_normalize_ligature_expands_both_chars_map_to_source() -> None:
    norm = normalize("a\ufb01b", ["nfkc"])
    assert norm.text == "afib"
    assert norm.span(1, 2) == (1, 2)
    assert norm.span(2, 3) == (1, 2)
    assert norm.span(1, 3) == (1, 2)
    assert norm.span(3, 4) == (2, 3)


def test_normalize_combining_accent_composes_and_maps_to_cluster() -> None:
    original = "cafe\u0301 x"
    norm = normalize(original, ["nfkc"])
    assert norm.text == "caf\u00e9 x"
    assert norm.span(3, 4) == (3, 5)
    assert original[slice(*norm.span(0, 4))] == "cafe\u0301"


def test_normalize_invisible_between_base_and_mark_composes() -> None:
    original = "e\u200b\u0301"
    norm = normalize(original, ["strip_invisible", "nfkc"])
    assert norm.text == "\u00e9"
    assert norm.span(0, 1) == (0, 3)


def test_normalize_whitespace_runs_collapse_to_one_space_mapped_to_run() -> None:
    original = "a  \t\n b\r\n\nc"
    norm = normalize(original, ["collapse_whitespace"])
    assert norm.text == "a b c"
    assert norm.span(1, 2) == (1, 6)
    assert norm.span(3, 4) == (7, 10)
    assert original[slice(*norm.span(0, 5))] == original


def test_normalize_run_split_by_invisible_is_one_run() -> None:
    norm = normalize("a \u200b b", ["strip_invisible", "collapse_whitespace"])
    assert norm.text == "a b"
    assert norm.span(1, 2) == (1, 4)
    without_strip = normalize("a \u200b b", ["collapse_whitespace"])
    assert without_strip.text == "a \u200b b"


def test_normalize_invisible_mid_word_removed_span_includes_it() -> None:
    original = "tal\u200bimat"
    norm = normalize(original, ["strip_invisible"])
    assert norm.text == "talimat"
    assert original[slice(*norm.span(0, 7))] == original
    assert norm.span(2, 4) == (2, 5)


def test_normalize_quotes_and_dashes_unified() -> None:
    text = (
        "\u201ca\u201d \u2018b\u2019 \u00abc\u00bb \u201ed\u201c 1\u20132 3\u20144 \u2212 5\u20116"
    )
    norm = normalize(text, ["unify_quotes_dashes"])
    assert norm.text == '"a" \'b\' "c" "d" 1-2 3-4 - 5-6'
    assert len(norm.text) == len(text)


def test_normalize_step_order_given_is_irrelevant() -> None:
    text = "x\u00a0\u200b \ufb01 \u201cq\u201d"
    a = normalize(text, ["unify_quotes_dashes", "collapse_whitespace", "nfkc", "strip_invisible"])
    b = normalize(text, ALL)
    assert a == b
    assert a.text == 'x fi "q"'


def test_normalize_no_casefold_case_preserved() -> None:
    assert normalize("İSTANBUL Iı", ALL).text == "İSTANBUL Iı"


def test_normalize_unknown_step_raises() -> None:
    with pytest.raises(ValueError):
        normalize("a", ["lowercase"])  # type: ignore[list-item]


def test_span_empty_range_maps_after_previous_char() -> None:
    norm = normalize("\u200ba  b\u200b", ALL)
    assert norm.text == "a b"
    assert norm.span(0, 0) == (0, 0)
    assert norm.span(1, 1) == (2, 2)
    assert norm.span(3, 3) == (5, 5)
    empty = normalize("\u200b", ALL)
    assert empty.text == ""
    assert empty.span(0, 0) == (0, 0)


def test_span_out_of_range_raises() -> None:
    norm = normalize("abc", ALL)
    with pytest.raises(ValueError):
        norm.span(2, 1)
    with pytest.raises(ValueError):
        norm.span(0, 4)


def test_span_mixed_length_changes_maps_to_right_original_slice() -> None:
    original = "Te\u200bslim  \ufb01yatı: cafe\u0301\t\n\u201cnet\u201d"
    norm = normalize(original, ALL)
    assert norm.text == 'Teslim fiyatı: caf\u00e9 "net"'
    idx = norm.text.index("fiyatı")
    assert original[slice(*norm.span(idx, idx + len("fiyatı")))] == "\ufb01yatı"
    idx = norm.text.index('caf\u00e9 "net"')
    assert original[slice(*norm.span(idx, len(norm.text)))] == "cafe\u0301\t\n\u201cnet\u201d"
    assert original[slice(*norm.span(0, 6))] == "Te\u200bslim"


_ALPHABET = [
    "a", "b", "e", "I", "ı", "İ", "ş", " ", " ", "\t", "\n", "\r", "\u00a0", "\u2003",
    "\u200b", "\u200d", "\ufeff", "\u00ad", "\U000e0041", "\u0301", "\u0308", "\u0327",
    "\ufb01", "\ufb02", "\u2460", "\uff21", "\u201c", "\u201d", "\u2019", "\u00ab",
    "\u2013", "\u2014", "\u2011", "\u2212", ".", "5",
]  # fmt: skip

_STEP_SETS: list[tuple[NormStep, ...]] = [
    ALL,
    ("strip_invisible",),
    ("nfkc",),
    ("collapse_whitespace",),
    ("strip_invisible", "nfkc", "collapse_whitespace"),
    ("nfkc", "unify_quotes_dashes"),
]


@pytest.mark.parametrize("steps", _STEP_SETS)
def test_span_round_trip_random_boundary_ranges_renormalize_equal(
    steps: tuple[NormStep, ...],
) -> None:
    rng = random.Random(4)
    for _ in range(300):
        original = "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 25)))
        norm = normalize(original, steps)
        bounds = [k for k in range(len(norm.text) + 1) if norm.is_boundary(k)]
        for a in bounds:
            for b in bounds:
                if b < a:
                    continue
                s, e = norm.span(a, b)
                assert 0 <= s <= e <= len(original)
                assert normalize(original[s:e], steps).text == norm.text[a:b], (original, a, b)


def test_span_sources_monotonic_random_inputs() -> None:
    rng = random.Random(7)
    for _ in range(300):
        original = "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 25)))
        norm = normalize(original, ALL)
        assert list(norm.starts) == sorted(norm.starts)
        assert list(norm.ends) == sorted(norm.ends)
        assert all(s < e for s, e in zip(norm.starts, norm.ends, strict=True))


def test_normalize_steps_constant_matches_all_steps() -> None:
    assert frozenset(ALL) == ALL_STEPS


def test_quote_find_different_whitespace_and_quotes_cuts_exact_original() -> None:
    clause = (
        "Madde 7 \u2013 Ödeme\n"
        "Alıcı, faturayı   \u201cteslim\u201d tarihinden\u200b itibaren\n\totuz (30)\u00a0gün "
        "içinde öder."
    )
    quote = 'Alıcı, faturayı "teslim" tarihinden itibaren otuz (30) gün içinde öder.'
    norm_clause = normalize(clause, ALL)
    norm_quote = normalize(quote, ALL)
    idx = norm_clause.text.find(norm_quote.text)
    assert idx >= 0
    s, e = norm_clause.span(idx, idx + len(norm_quote.text))
    cut = clause[s:e]
    assert cut == clause[clause.index("Alıcı") :]
    assert cut in clause
    assert normalize(cut, ALL).text == norm_quote.text


def test_quote_find_dash_variants_cuts_exact_original() -> None:
    clause = "Süre 1\u20133 yıl\u2014uzatılabilir."
    quote = "1-3 yıl-uzatılabilir"
    norm_clause = normalize(clause, ALL)
    target = normalize(quote, ALL).text
    idx = norm_clause.text.find(target)
    s, e = norm_clause.span(idx, idx + len(target))
    assert clause[s:e] == "1\u20133 yıl\u2014uzatılabilir"
