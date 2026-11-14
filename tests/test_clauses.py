import pytest

from src.models import Check, Clause, Finding, MaskedText, RawClause, Rule
from src.prepare.clauses import split_clauses
from src.verify import verify


def _split(text: str) -> list[RawClause]:
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


# --- A byte order mark at the start of the text ---------------------------------------------------

BOM = chr(0xFEFF)


def test_split_clauses_bom_before_first_heading_heading_is_recognized() -> None:
    text = BOM + "Madde 1 - Konu\nBirinci metin.\n\nMadde 2 - Süre\nİkinci metin."
    clauses = _split(text)
    assert [(c.clause_id, c.clause_no) for c in clauses] == [("c1", "1"), ("c2", "2")]
    # The mark stays in the first clause: every non-whitespace character is in a clause.
    assert (clauses[0].start, clauses[0].text[0]) == (0, BOM)
    assert clauses[0].text.endswith("Birinci metin.")


def test_split_clauses_bom_same_clauses_as_without_it_offsets_shifted() -> None:
    body = "## Madde 1: Konu\r\nA\r\n**MADDE 2.** Süre\r\nB\r\nİmzalar\r\nX A.Ş."
    plain = _split(body)
    marked = _split(BOM + body)
    assert [(c.clause_id, c.clause_no) for c in marked] == [
        (c.clause_id, c.clause_no) for c in plain
    ]
    assert marked[0].text == BOM + plain[0].text
    assert [(c.start, c.end, c.text) for c in marked[1:]] == [
        (c.start + 1, c.end + 1, c.text) for c in plain[1:]
    ]


def test_split_clauses_bom_then_spaces_before_heading_is_recognized() -> None:
    clauses = _split(BOM + "  \tMadde 1 - Konu\nMetin")
    assert [c.clause_no for c in clauses] == ["1"]


def test_split_clauses_bom_before_preamble_changes_nothing() -> None:
    clauses = _split(BOM + "Taraflar şunlardır.\n\nMadde 1 - Konu\nMetin")
    assert [c.clause_no for c in clauses] == [None, "1"]
    assert clauses[0].text == BOM + "Taraflar şunlardır."


def test_split_clauses_bom_elsewhere_or_doubled_is_not_a_line_start() -> None:
    # Only one mark at the very start of the text counts; any other invisible character before
    # "Madde" keeps the line from being a heading (the injection scan flags it).
    assert [c.clause_no for c in _split(BOM + BOM + "Madde 1 - Konu\nMetin")] == [None]
    assert [c.clause_no for c in _split("Madde 1 - Konu\nA\n" + BOM + "Madde 2 - Süre\nB")] == ["1"]
    zero_width = chr(0x200B)
    assert [c.clause_no for c in _split(zero_width + "Madde 1 - Konu\nMetin")] == [None]


def test_split_clauses_bom_without_headings_paragraphs_unchanged() -> None:
    clauses = _split(BOM + "Birinci paragraf.\n\nİkinci paragraf.")
    assert [c.text for c in clauses] == [BOM + "Birinci paragraf.", "İkinci paragraf."]


# --- Numbered Markdown headings ("## 4. Fesih"), only when there is no "Madde N" heading ---------


def test_split_clauses_numbered_markdown_headings_keep_sub_items_inside() -> None:
    text = "## 1. Taraflar\n1.1. A\n\n1.2. B\n\n## 2. Konu\n2.1. C\n\n## 3. Fesih\n3.1. D\n\n3.2. E"
    clauses = _split(text)
    assert [c.clause_no for c in clauses] == ["1", "2", "3"]
    assert clauses[0].text == "## 1. Taraflar\n1.1. A\n\n1.2. B"
    assert clauses[2].text.endswith("3.2. E")


def test_split_clauses_numbered_markdown_sub_item_heading_does_not_split() -> None:
    clauses = _split("## 4. Fesih\nx\n## 4.1. Bildirim\ny\n## 4.2. Süre\nz")
    assert [c.clause_no for c in clauses] == ["4"]


def test_split_clauses_numbered_markdown_heading_with_bold_number() -> None:
    clauses = _split("## **1.** Taraflar\nA\n\n## **2.** Konu\nB")
    assert [c.clause_no for c in clauses] == ["1", "2"]


def test_split_clauses_numbered_markdown_heading_alone_on_line() -> None:
    clauses = _split("## 1.\nA\n\n## 2.  \nB")
    assert [c.clause_no for c in clauses] == ["1", "2"]


def test_split_clauses_numbered_markdown_heading_number_as_written() -> None:
    clauses = _split("## 01. A\nx\n\n## 02. B\ny")
    assert [c.clause_no for c in clauses] == ["01", "02"]


def test_split_clauses_numbered_markdown_preamble_and_signature() -> None:
    text = "# SÖZLEŞME\n\nGiriş metni.\n\n## 1. A\nx\n\n## 2. B\ny\n\nİmzalar\nX A.Ş.   Y Ltd."
    clauses = _split(text)
    assert [c.clause_no for c in clauses] == [None, "1", "2", None]
    assert clauses[0].text == "# SÖZLEŞME\n\nGiriş metni."
    assert clauses[2].text == "## 2. B\ny"
    assert clauses[3].text.startswith("İmzalar")


def test_split_clauses_numbered_markdown_annex_stays_in_last_clause() -> None:
    clauses = _split("## 1. A\nx\n\n## 2. B\ny\n\n## Ek-1\nek metni")
    assert [c.clause_no for c in clauses] == ["1", "2"]
    assert clauses[1].text.endswith("## Ek-1\nek metni")


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_split_clauses_numbered_markdown_crlf_and_cr(newline: str) -> None:
    text = newline.join(["## 1. A", "x", "", "## 2. B", "y"])
    clauses = _split(text)
    assert [(c.clause_no, c.text) for c in clauses] == [
        ("1", newline.join(["## 1. A", "x"])),
        ("2", newline.join(["## 2. B", "y"])),
    ]


def test_split_clauses_numbered_markdown_heading_after_bom() -> None:
    clauses = _split(BOM + "## 1. Taraflar\nA\n\n## 2. Konu\nB")
    assert [c.clause_no for c in clauses] == ["1", "2"]
    assert clauses[0].text[0] == BOM


@pytest.mark.parametrize(
    "text",
    [
        "## 4.1. Alt\nmetin\n\n## 4.2. Alt\nmetin",
        "## 14.11.2026 Tarihli Ek\nmetin\n\n## 1.200 TL\nx",
        "## 2026. Yılı Fiyatları\nmetin\n\n## 1234. Uzun\nx",
        "## 4 - Fesih\nx\n\n## 5 - Son\ny",
        "## 4.Fesih\nx\n\n## 5.Son\ny",
        "#4. Fesih\nx\n\n#5. Son\ny",
        "1. Taraflar\n1.1. A\n\n2. Konu\n2.1. C",
        "**1.** Taraflar\nA\n\n**2.** Konu\nB",
        "Tedarikçi:\n1. Teslim eder.\n2. Kurar.\n\n3. Bakım yapar.",
        "## I. Giriş\nmetin\n\n## II. Konu\nmetin",
        "14.11.2026 tarihinde imzalandı.\n\n1.200.000 TL bedel.",
    ],
)
def test_split_clauses_not_numbered_heading_falls_back_to_paragraphs(text: str) -> None:
    clauses = _split(text)
    assert all(c.clause_no is None for c in clauses)
    assert [c.text for c in clauses] == [p.strip() for p in text.split("\n\n") if p.strip()]


def test_split_clauses_numbered_markdown_ignored_when_madde_heading_exists() -> None:
    clauses = _split("## Madde 1 - A\nx\n\n## 2. B\ny")
    assert [c.clause_no for c in clauses] == ["1"]
    assert clauses[0].text == "## Madde 1 - A\nx\n\n## 2. B\ny"


def test_split_clauses_numbered_list_one_level_below_does_not_split() -> None:
    text = "## 1. Konu\nx\n\n## 2. Ekler\n### 1. Teknik şartname\n### 2. Fiyat listesi\n"
    clauses = _split(text)
    assert [c.clause_no for c in clauses] == ["1", "2"]
    assert clauses[1].text.endswith("### 2. Fiyat listesi")


def test_split_clauses_numbered_markdown_level_is_that_of_first_heading() -> None:
    text = "### 1. Konu\nx\n## 2. Ara başlık\ny\n### 3. Fesih\nz"
    clauses = _split(text)
    assert [c.clause_no for c in clauses] == ["1", "3"]
    assert clauses[0].text == "### 1. Konu\nx\n## 2. Ara başlık\ny"


def test_split_clauses_numbered_markdown_every_character_covered_once() -> None:
    text = "Giriş\r\n\r\n## 1. A\r\n1.1. metin\r\n## **2.** B\r\nmetin\r\nİmzalar\r\nX A.Ş.\r\n"
    clauses = _split(text)
    assert [c.clause_id for c in clauses] == ["c1", "c2", "c3", "c4"]
    covered = [i for c in clauses for i in range(c.start, c.end)]
    assert len(covered) == len(set(covered))
    assert {i for i, ch in enumerate(text) if not ch.isspace()} <= set(covered)


@pytest.mark.parametrize(
    ("rule", "text", "quote"),
    [
        (
            Rule(
                id="termination_notice",
                text="Fesih bildirim süresi en az 30 gün olmalıdır.",
                check=Check(op=">=", value=30, unit="gün"),
            ),
            "## 4. Fesih\n\n4.1. Taraflar 45 gün önceden yazılı bildirimle feshedebilir.\n\n"
            "4.2. Ancak Tedarikçi 10 gün önceden bildirimle feshedebilir.",
            "Taraflar 45 gün önceden yazılı bildirimle feshedebilir.",
        ),
        (
            Rule(
                id="insurance_minimum",
                text="Tedarikçi en az 1.000.000 TL teminatlı sigorta yaptırmalıdır.",
                check=Check(op=">=", value=1000000, unit="TL"),
            ),
            "## 6. Sigorta\n\n6.1. Tedarikçi 2.000.000 TL teminatlı mesleki sorumluluk "
            "sigortası yaptırır.\n\n6.2. İlk yıl için teminat 500.000 TL olarak uygulanır.",
            "Tedarikçi 2.000.000 TL teminatlı mesleki sorumluluk sigortası yaptırır.",
        ),
    ],
)
def test_split_clauses_numbered_heading_lets_verify_see_breaking_sub_item(
    rule: Rule, text: str, quote: str
) -> None:
    # A "compliant" quote on one sub-item while the next sub-item breaks the rule: the whole
    # top-level clause is one clause, so verify sees the breaking value outside the quote.
    (clause,) = [c for c in _split(text) if quote in c.text]
    assert clause.clause_no == text[3]
    clauses = [
        Clause(
            clause_id=c.clause_id,
            clause_no=c.clause_no,
            start=c.start,
            end=c.end,
            text=MaskedText(c.text),
        )
        for c in _split(text)
    ]
    finding = Finding(rule_id=rule.id, status="compliant", clause_id=clause.clause_id, quote=quote)
    (result,) = verify([finding], [rule], clauses, False, enabled=True)
    assert result.needs_review
    assert "numeric_mismatch" in result.review_reasons
