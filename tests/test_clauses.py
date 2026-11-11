from src.models import Clause
from src.prepare.clauses import split_clauses


def _split(text: str) -> list[Clause]:
    clauses = split_clauses(text)
    for c in clauses:
        assert text[c.start : c.end] == c.text
        assert c.text == c.text.strip()
    return clauses


def test_split_clauses_plain_headings_splits_each_madde() -> None:
    text = "Madde 1 - Konu\nBirinci metin.\n\nMadde 2 - Süre\nİkinci metin."
    clauses = _split(text)
    assert [c.clause_no for c in clauses] == ["1", "2"]
    assert clauses[0].text.startswith("Madde 1 - Konu")
    assert clauses[1].text.endswith("İkinci metin.")


def test_split_clauses_markdown_heading_is_recognized() -> None:
    clauses = _split("## Madde 1: Konu\nA\n## Madde 2: Süre\nB")
    assert [c.clause_no for c in clauses] == ["1", "2"]


def test_split_clauses_bold_heading_is_recognized() -> None:
    clauses = _split("**MADDE 3.** Ödeme\nMetin\n**Madde 4**\nBaşka metin")
    assert [c.clause_no for c in clauses] == ["3", "4"]


def test_split_clauses_end_of_line_heading_is_recognized() -> None:
    clauses = _split("MADDE 7\nMetin")
    assert [c.clause_no for c in clauses] == ["7"]


def test_split_clauses_preamble_becomes_clause_without_number() -> None:
    clauses = _split("Taraflar şunlardır.\n\nMadde 1 - Konu\nMetin")
    assert [c.clause_no for c in clauses] == [None, "1"]
    assert clauses[0].text == "Taraflar şunlardır."


def test_split_clauses_whitespace_only_preamble_is_dropped() -> None:
    clauses = _split("\n  \nMadde 1 - Konu\nMetin")
    assert [c.clause_no for c in clauses] == ["1"]


def test_split_clauses_signature_block_becomes_own_clause() -> None:
    text = "Madde 1 - Konu\nMetin\n\nMadde 2 - Süre\nSüre metni.\n\nİMZALAR\nA Ltd.   B A.Ş."
    clauses = _split(text)
    assert [c.clause_no for c in clauses] == ["1", "2", None]
    assert clauses[1].text.endswith("Süre metni.")
    assert clauses[2].text.startswith("İMZALAR")


def test_split_clauses_taraflar_adina_marker_starts_signature_block() -> None:
    text = "Madde 1 - Konu\nMetin\n\nTaraflar adına:\nA Ltd."
    clauses = _split(text)
    assert [c.clause_no for c in clauses] == ["1", None]
    assert clauses[1].text.startswith("Taraflar adına:")


def test_split_clauses_markdown_signature_marker_is_recognized() -> None:
    clauses = _split("Madde 1 - Konu\nMetin\n\n## İmza\nA Ltd.")
    assert [c.clause_no for c in clauses] == ["1", None]


def test_split_clauses_lowercase_dotted_signature_marker_is_recognized() -> None:
    clauses = _split("Madde 1 - Konu\nMetin\nimzalar\nA Ltd.")
    assert [c.clause_no for c in clauses] == ["1", None]


def test_split_clauses_signature_word_inside_sentence_is_not_a_marker() -> None:
    text = "Madde 1 - Konu\nİmza yetkisi bu sözleşmeyle verilir.\nDevam."
    clauses = _split(text)
    assert len(clauses) == 1


def test_split_clauses_no_signature_marker_last_clause_runs_to_end() -> None:
    text = "Madde 1 - Konu\nMetin\nMadde 2 - Süre\nSon satır.   \n\n"
    clauses = _split(text)
    assert len(clauses) == 2
    assert clauses[-1].end == len(text.rstrip())
    assert clauses[-1].text.endswith("Son satır.")


def test_split_clauses_no_heading_falls_back_to_paragraphs() -> None:
    text = "Birinci paragraf\ndevam eder.\n\nİkinci paragraf.\n  \nÜçüncü."
    clauses = _split(text)
    assert [c.text for c in clauses] == [
        "Birinci paragraf\ndevam eder.",
        "İkinci paragraf.",
        "Üçüncü.",
    ]
    assert all(c.clause_no is None for c in clauses)


def test_split_clauses_crlf_input_keeps_exact_slices() -> None:
    text = "Madde 1 - Konu\r\nMetin\r\n\r\nMadde 2 - Süre\r\nSon\r\n"
    clauses = _split(text)
    assert [c.clause_no for c in clauses] == ["1", "2"]
    assert clauses[1].text == "Madde 2 - Süre\r\nSon"


def test_split_clauses_crlf_paragraph_fallback_splits_on_blank_line() -> None:
    clauses = _split("Bir\r\n\r\nİki\r\n \r\nÜç")
    assert [c.text for c in clauses] == ["Bir", "İki", "Üç"]


def test_split_clauses_invisible_characters_stay_in_text() -> None:
    text = "Madde 1 - Konu\nGizli\u200b karakter\nMadde 2 - Süre\nMetin"
    clauses = _split(text)
    assert "\u200b" in clauses[0].text


def test_split_clauses_duplicate_madde_number_gets_distinct_ids() -> None:
    clauses = _split("Madde 2 - Bir\nA\nMadde 2 - İki\nB")
    assert [c.clause_no for c in clauses] == ["2", "2"]
    assert clauses[0].clause_id != clauses[1].clause_id


def test_split_clauses_line_start_reference_does_not_split() -> None:
    text = "Madde 1 - Konu\nMetin\nMadde 5'e göre işlem yapılır.\nMadde 5 uyarınca bildirim.\n"
    clauses = _split(text)
    assert len(clauses) == 1


def test_split_clauses_inline_reference_does_not_split() -> None:
    text = "Madde 1 - Konu\nBu durumda Madde 2 - uyarınca hareket edilir.\nMadde 3 - Son"
    clauses = _split(text)
    assert [c.clause_no for c in clauses] == ["1", "3"]


def test_split_clauses_ids_are_sequential_over_returned_clauses() -> None:
    clauses = _split("Giriş\n\nMadde 1 - A\nx\nMadde 2 - B\ny\nİmza\nz")
    assert [c.clause_id for c in clauses] == ["c1", "c2", "c3", "c4"]


def test_split_clauses_empty_input_returns_empty_list() -> None:
    assert split_clauses("") == []
    assert split_clauses(" \n\t\r\n ") == []


def test_split_clauses_crlf_single_line_break_is_not_paragraph_break() -> None:
    clauses = _split("Birinci satır\r\nikinci satır\r\n\r\nÜçüncü")
    assert [c.text for c in clauses] == ["Birinci satır\r\nikinci satır", "Üçüncü"]


def test_split_clauses_crlf_end_of_line_heading_is_recognized() -> None:
    clauses = _split("MADDE 1\r\nMetin\r\nMADDE 2\r\nMetin2")
    assert [c.clause_no for c in clauses] == ["1", "2"]


def test_split_clauses_crlf_signature_marker_is_recognized() -> None:
    clauses = _split("Madde 1 - Konu\r\nMetin\r\n\r\nİMZALAR\r\nA Ltd.")
    assert [(c.clause_no, c.text) for c in clauses] == [
        ("1", "Madde 1 - Konu\r\nMetin"),
        (None, "İMZALAR\r\nA Ltd."),
    ]


def test_split_clauses_cr_only_line_endings_split_on_headings() -> None:
    clauses = _split("Madde 1 - Konu\rMetin\rMadde 2 - Süre\rX")
    assert [c.clause_no for c in clauses] == ["1", "2"]


def test_split_clauses_line_start_subclause_reference_does_not_split() -> None:
    clauses = _split("Madde 1 - Konu\nMetin\nMadde 3.2 uyarınca bildirim yapılır.\n")
    assert [c.clause_no for c in clauses] == ["1"]


def test_split_clauses_every_non_whitespace_character_is_covered_once() -> None:
    text = "Giriş\r\n\r\nMadde 1. A\r\nmetin\r\nMadde 2: B\r\nmetin\r\nİmzalar\r\nX A.Ş.\r\n"
    clauses = _split(text)
    covered = [i for c in clauses for i in range(c.start, c.end)]
    assert len(covered) == len(set(covered))
    assert {i for i, ch in enumerate(text) if not ch.isspace()} <= set(covered)
