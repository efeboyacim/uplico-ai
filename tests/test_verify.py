import random
import re
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

import src.verify as verify_module
from src.models import (
    Check,
    CheckOp,
    CheckUnit,
    Clause,
    Finding,
    MaskedText,
    Proposal,
    Rule,
    Severity,
)
from src.prepare.pii import mask
from src.text import ALL_STEPS, normalize
from src.verify import Extraction, Kind, Quantity, align, extract, verify

# --- Helpers --------------------------------------------------------------------------------------


def _rule(
    op: CheckOp | None = None,
    value: float = 0,
    unit: CheckUnit = "gün",
    severity: Severity = "normal",
    rule_id: str = "r1",
) -> Rule:
    check = Check(op=op, value=value, unit=unit) if op is not None else None
    return Rule(id=rule_id, text="Kural metni.", severity=severity, check=check)


def _clause(text: str, clause_id: str = "c1") -> Clause:
    return Clause(clause_id=clause_id, clause_no="1", start=0, end=len(text), text=MaskedText(text))


def _finding(
    quote: str | None = None,
    status: str = "compliant",
    clause_id: str | None = "c1",
    rule_id: str = "r1",
    **kwargs: object,
) -> Finding:
    return Finding.model_validate(
        {"rule_id": rule_id, "status": status, "clause_id": clause_id, "quote": quote, **kwargs}
    )


def _proposal(text: str, code_check: str | None = None) -> Proposal:
    return Proposal.model_validate(
        {
            "clause_id": "c1",
            "proposed_text": text,
            "rationale": "Gerekçe.",
            "code_check": code_check,
        }
    )


def _run(
    finding: Finding,
    rule: Rule,
    clause_text: str | None = None,
    injection: bool = False,
    enabled: bool = True,
) -> Finding:
    text = clause_text if clause_text is not None else (finding.quote or "")
    (result,) = verify([finding], [rule], [_clause(text)], injection, enabled=enabled)
    return result


def _reasons(finding: Finding, rule: Rule, clause_text: str | None = None) -> list[str]:
    return list(_run(finding, rule, clause_text).review_reasons)


AT_LEAST_30_DAYS = _rule(">=", 30, "gün")
AT_MOST_60_DAYS = _rule("<=", 60, "gün")
AT_LEAST_1_YEAR = _rule(">=", 1, "yıl")
EXACTLY_30_DAYS = _rule("==", 30, "gün")
NO_CHECK = _rule()


def _days(n: float) -> Quantity:
    return Quantity(n, "gün")


# --- Quote verification ---------------------------------------------------------------------------


def test_verify_exact_quote_passes_without_review() -> None:
    clause = "Madde 3 - Taraflar sözleşmeyi yazılı bildirimle feshedebilir."
    result = _run(_finding("sözleşmeyi yazılı bildirimle feshedebilir"), NO_CHECK, clause)
    assert result.review_reasons == []
    assert result.needs_review is False
    assert result.quote == "sözleşmeyi yazılı bildirimle feshedebilir"


def test_verify_fabricated_quote_needs_review() -> None:
    clause = "Taraflar sözleşmeyi yazılı bildirimle feshedebilir."
    finding = _finding("Taraflar sözleşmeyi sözlü bildirimle feshedebilir.")
    result = _run(finding, NO_CHECK, clause)
    assert result.review_reasons == ["quote_not_found"]
    assert result.needs_review is True
    assert result.quote == finding.quote


def test_verify_quote_whitespace_and_quote_marks_differ_replaced_with_contract_slice() -> None:
    clause = "Alıcı, \u201cfesih\u201d bildirimini\u00a0 yazılı\n  olarak yapar."
    model_quote = 'Alıcı, "fesih" bildirimini yazılı olarak yapar.'
    result = _run(_finding(model_quote), NO_CHECK, clause)
    assert result.review_reasons == []
    assert result.quote == clause
    assert result.quote != model_quote


def test_verify_quote_matched_inside_clause_is_exact_substring() -> None:
    clause = "Giriş. Ödeme  \u2018derhal\u2019 yapılır;\tgecikme faizi uygulanır. Son."
    result = _run(_finding("Ödeme 'derhal' yapılır; gecikme"), NO_CHECK, clause)
    assert result.quote is not None
    assert result.quote in clause
    assert result.quote == "Ödeme  \u2018derhal\u2019 yapılır;\tgecikme"


def test_verify_quote_invisible_and_compatibility_chars_differ_match() -> None:
    clause = "Bildirim \uff13\uff10 g\u200bün önceden yapılır."
    result = _run(_finding("Bildirim 30 gün önceden yapılır."), AT_LEAST_30_DAYS, clause)
    assert result.review_reasons == []
    assert result.quote == clause


def test_verify_value_read_from_contract_slice_not_model_text() -> None:
    # The model wrote the quote in plain ASCII digits; the contract has full-width digits and an
    # invisible character inside the number. The code reads the value from the contract slice.
    clause = "Fesih bildirimi \uff14\u200b\uff15 gün önceden yapılır."
    finding = _finding("Fesih bildirimi 45 gün önceden yapılır.", value=45, unit="gün")
    result = _run(finding, AT_LEAST_30_DAYS, clause)
    assert result.review_reasons == []
    assert result.quote == clause


def test_verify_letter_case_punctuation_and_turkish_letters_not_ignored() -> None:
    clause = "Fesih bildirimi yazılı yapılır."
    for quote in (
        "fesih bildirimi yazılı yapılır.",
        "Fesih bildirimi yazili yapılır.",
        "Fesih, bildirimi",
    ):
        assert _reasons(_finding(quote), NO_CHECK, clause) == ["quote_not_found"]


def test_verify_turkish_dotted_capital_not_casefolded() -> None:
    clause = "İşbu sözleşme"
    assert _reasons(_finding("işbu sözleşme"), NO_CHECK, clause) == ["quote_not_found"]


def test_verify_unknown_clause_id_quote_not_found() -> None:
    finding = _finding("30 gün", clause_id="c9")
    assert _reasons(finding, AT_LEAST_30_DAYS, "30 gün") == ["quote_not_found"]


def test_verify_quote_empty_after_normalization_quote_not_found() -> None:
    finding = _finding("\u200b\u200d")
    assert _reasons(finding, NO_CHECK, "Metin \u200b\u200d burada.") == ["quote_not_found"]


def test_verify_quote_occurring_twice_first_occurrence_taken() -> None:
    clause = 'Önce A "x" B, sonra A \u201cx\u201d B.'
    result = _run(_finding("A \u201cx\u201d B"), NO_CHECK, clause)
    assert result.quote == 'A "x" B'


@pytest.mark.parametrize(
    ("quote", "clause"),
    [
        ("30 gün içinde", "Ödeme 130 gün içinde yapılır."),
        ("250.000 TL", "Ceza bedeli 1.250.000 TL olarak uygulanır."),
        ("5 ay", "Teslimat 5 ayrı partide yapılır."),
        ("45 gün", "Bildirim süresi 45 gündür."),
        ("0 gün önceden", "Bildirim 90 gün önceden yapılır."),
        ("5 yıl", "Süre 1,5 yıl olarak belirlenir."),
        ("Bildirim 30", "Bildirim 30,5 gün önceden yapılır."),
        ("gün içinde", "Ödeme 30gün içinde yapılır."),
        ("Ödeme 30 gün iç", "Ödeme 30 gün içinde yapılır."),
        (".250.000 TL", "Ceza bedeli 1.250.000 TL olarak uygulanır."),
        ("Ceza bedeli 1.250.", "Ceza bedeli 1.250.000 TL olarak uygulanır."),
        (",5 yıl", "Süre 1,5 yıl olarak belirlenir."),
        ("Bildirim 30,", "Bildirim 30,5 gün önceden yapılır."),
        ("faizi %30,", "Gecikme faizi %30,5 olarak uygulanır."),
    ],
)
def test_verify_quote_cutting_word_or_number_quote_not_found(quote: str, clause: str) -> None:
    assert _reasons(_finding(quote), AT_LEAST_30_DAYS, clause) == ["quote_not_found"]


def test_verify_quote_cut_first_occurrence_skipped_clean_one_taken() -> None:
    # The two occurrences differ in spacing, so the returned slice shows which one was taken.
    clause = "Satıcı 130 gün  içinde, Alıcı 30 gün\tiçinde öder."
    result = _run(_finding("30 gün içinde", value=30, unit="gün"), AT_LEAST_30_DAYS, clause)
    assert result.review_reasons == []
    assert result.quote == "30 gün\tiçinde"


def test_verify_quote_ending_before_sentence_punctuation_matches() -> None:
    clause = "Bildirim süresi 30. Madde 2 saklıdır."
    assert _reasons(_finding("Bildirim süresi 30"), NO_CHECK, clause) == []


def test_verify_quote_not_found_skips_numeric_signals() -> None:
    finding = _finding("15 gün önceden bildirilir", value=99)
    assert _reasons(finding, AT_LEAST_30_DAYS, "45 gün önceden bildirilir") == ["quote_not_found"]


def test_verify_not_found_status_quote_not_checked() -> None:
    finding = _finding("uydurma", status="not_found")
    result = _run(finding, NO_CHECK, "Başka metin.")
    assert result.review_reasons == ["unverified_not_found"]
    assert result.quote == "uydurma"


# --- The code reads the number --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        ("30 gün", [_days(30)]),
        ("otuz (30) gün", [_days(30), _days(30)]),
        ("30 (otuz) gün", [_days(30), _days(30)]),
        ("bir ay", [Quantity(1, "ay")]),
        ("30 takvim günü", [_days(30)]),
        ("1,5 yıl", [Quantity(1.5, "yıl")]),
        ("üç hafta", [Quantity(3, "hafta")]),
        ("30gün", [_days(30)]),
    ],
)
def test_extract_duration_read_from_text(quote: str, expected: list[Quantity]) -> None:
    assert extract(quote, "duration") == Extraction(tuple(expected), ())


@pytest.mark.parametrize(
    ("words", "value"),
    [
        ("bir", 1),
        ("dokuz", 9),
        ("on", 10),
        ("on iki", 12),
        ("doksan dokuz", 99),
        ("yüz", 100),
        ("yüz yirmi", 120),
        ("iki yüz elli beş", 255),
        ("dokuz yüz doksan dokuz", 999),
        ("OTUZ", 30),
        ("ALTI", 6),
        ("İKİ", 2),
        ("Kırk Beş", 45),
    ],
)
def test_extract_number_words_composed(words: str, value: float) -> None:
    assert extract(f"{words} gün", "duration").values == (_days(value),)


def test_extract_dotless_capital_i_lowered_turkish_not_read_as_number() -> None:
    # "IKI" lowers to "ıkı" under Turkish rules, which is not a number word.
    assert extract("IKI gün", "duration") == Extraction((), ())


def test_verify_thirty_days_compliant_no_review() -> None:
    assert _reasons(_finding("30 gün önceden"), AT_LEAST_30_DAYS) == []


def test_verify_number_word_with_digits_single_value() -> None:
    assert _reasons(_finding("otuz (30) gün önceden"), AT_LEAST_30_DAYS) == []


def test_verify_number_word_and_digits_differ_ambiguous() -> None:
    reasons = _reasons(_finding("otuz (31) gün önceden"), AT_LEAST_30_DAYS)
    assert reasons == ["ambiguous_value"]


def test_verify_model_value_missing_code_still_decides() -> None:
    finding = _finding("15 gün önceden bildirim")
    assert _reasons(finding, AT_LEAST_30_DAYS) == ["numeric_mismatch"]
    finding = _finding(
        "15 gün önceden bildirim", status="non_compliant", proposal=_proposal("45 gün")
    )
    assert _reasons(finding, AT_LEAST_30_DAYS) == []


def test_verify_model_value_differs_numeric_mismatch() -> None:
    finding = _finding("30 gün önceden", value=45, unit="gün")
    assert _reasons(finding, AT_LEAST_30_DAYS) == ["numeric_mismatch"]


def test_verify_model_value_equal_no_review() -> None:
    assert _reasons(_finding("30 gün önceden", value=30, unit="gün"), AT_LEAST_30_DAYS) == []


def test_verify_model_value_same_after_conversion_no_review() -> None:
    finding = _finding(
        "14 gün içinde", value=2, unit="hafta", status="non_compliant", proposal=_proposal("30 gün")
    )
    assert _reasons(finding, AT_LEAST_30_DAYS) == []
    # 30 days lies inside one month (28-31), so "1 ay" agrees with the code's 30 days.
    assert _reasons(_finding("30 gün önceden", value=1, unit="ay"), AT_LEAST_30_DAYS) == []


def test_verify_model_unit_missing_rule_unit_assumed() -> None:
    assert _reasons(_finding("30 gün önceden", value=30), AT_LEAST_30_DAYS) == []
    assert _reasons(_finding("30 gün önceden", value=30, unit="  "), AT_LEAST_30_DAYS) == []
    assert _reasons(_finding("30 gün önceden", value=1), AT_LEAST_30_DAYS) == ["numeric_mismatch"]


@pytest.mark.parametrize("unit", ["saat", "days", "TL", "%", "günlük"])
def test_verify_model_unit_unknown_or_other_kind_numeric_mismatch(unit: str) -> None:
    finding = _finding("30 gün önceden", value=30, unit=unit)
    assert _reasons(finding, AT_LEAST_30_DAYS) == ["numeric_mismatch"]


def test_verify_model_unit_case_insensitive_turkish() -> None:
    assert _reasons(_finding("30 GÜN önceden", value=30, unit="GÜN"), AT_LEAST_30_DAYS) == []
    rule = _rule("<=", 1000, "TL")
    assert _reasons(_finding("500 TL ceza", value=500, unit="tl"), rule) == []


def test_verify_code_verdict_contradicts_status_numeric_mismatch() -> None:
    finding = _finding("45 gün önceden", status="non_compliant", proposal=_proposal("60 gün"))
    assert _reasons(finding, AT_LEAST_30_DAYS) == ["numeric_mismatch"]


def test_verify_no_value_in_quote_numeric_mismatch() -> None:
    assert _reasons(_finding("makul bir süre önceden"), AT_LEAST_30_DAYS) == ["numeric_mismatch"]


def test_verify_rule_without_check_no_numeric_signals() -> None:
    assert _reasons(_finding("15 gün ve 90 gün"), NO_CHECK) == []


# --- Duration conversion --------------------------------------------------------------------------


def test_align_same_family_exact() -> None:
    assert align(Quantity(2, "hafta"), _days(14)) == ((14, 14), (14, 14))
    assert align(Quantity(1, "yıl"), Quantity(12, "ay")) == ((12, 12), (12, 12))


def test_align_across_families_interval() -> None:
    assert align(Quantity(1, "ay"), _days(30)) == ((28, 31), (30, 30))
    assert align(Quantity(2, "yıl"), Quantity(1, "hafta")) == ((730, 732), (7, 7))
    assert align(_days(60), Quantity(2, "ay")) == ((60, 60), (56, 62))


def test_align_other_kinds_only_same_unit() -> None:
    assert align(Quantity(15, "%"), Quantity(20, "%")) == ((15, 15), (20, 20))
    with pytest.raises(ValueError):
        align(Quantity(15, "%"), _days(15))


def test_verify_twelve_months_against_one_year_no_review() -> None:
    assert _reasons(_finding("12 ay süreyle"), AT_LEAST_1_YEAR) == []


def test_verify_twelve_months_spelled_and_one_year_single_value() -> None:
    quote = "on iki (12) ay, yani bir yıl süreyle"
    assert extract(quote, "duration").values == (
        Quantity(12, "ay"),
        Quantity(12, "ay"),
        Quantity(1, "yıl"),
    )
    assert _reasons(_finding(quote), AT_LEAST_1_YEAR) == []


def test_verify_one_month_against_thirty_days_unit_conversion_uncertain() -> None:
    assert _reasons(_finding("bir ay önceden"), AT_LEAST_30_DAYS) == ["unit_conversion_uncertain"]
    assert _reasons(_finding("1 ay önceden"), AT_LEAST_30_DAYS) == ["unit_conversion_uncertain"]


def test_verify_six_months_against_thirty_days_no_review() -> None:
    assert _reasons(_finding("6 ay önceden"), AT_LEAST_30_DAYS) == []


def test_verify_thirty_days_and_one_month_single_value_but_uncertain() -> None:
    quote = "30 gün, yani bir ay önceden"
    assert _reasons(_finding(quote), AT_LEAST_30_DAYS) == ["unit_conversion_uncertain"]


def test_verify_sixty_days_and_two_months_no_review() -> None:
    assert _reasons(_finding("60 gün, yani iki ay önceden"), AT_LEAST_30_DAYS) == []


def test_verify_two_weeks_converted_exactly() -> None:
    finding = _finding("2 hafta önceden", status="non_compliant", proposal=_proposal("5 hafta"))
    assert _reasons(finding, AT_LEAST_30_DAYS) == []


@pytest.mark.parametrize(
    ("quote", "status", "expected"),
    [
        ("30 gün", "compliant", []),
        ("1 ay", "compliant", ["unit_conversion_uncertain"]),  # 30 lies inside 28-31
        ("2 ay", "non_compliant", []),  # 56-62 does not meet 30
        ("29 gün", "non_compliant", []),
        ("29 gün", "compliant", ["numeric_mismatch"]),
    ],
)
def test_verify_equality_check_on_intervals(quote: str, status: str, expected: list[str]) -> None:
    proposal = _proposal("30 gün") if status == "non_compliant" else None
    finding = _finding(quote, status=status, proposal=proposal)
    assert _reasons(finding, EXACTLY_30_DAYS) == expected


@pytest.mark.parametrize(
    ("op", "quote", "expected"),
    [
        (">", "31 gün", []),
        (">", "30 gün", ["numeric_mismatch"]),
        ("<", "29 gün", []),
        ("<", "1 ay", ["unit_conversion_uncertain"]),
        ("<=", "4 hafta", []),
        ("<", "2 ay", ["numeric_mismatch"]),
    ],
)
def test_verify_strict_and_upper_bounds(op: CheckOp, quote: str, expected: list[str]) -> None:
    assert _reasons(_finding(quote), _rule(op, 30, "gün")) == expected


@pytest.mark.parametrize(
    ("op", "quote", "expected"),
    [
        (">=", "32 gün", []),  # 32 >= 31: every point of 28-31
        (">=", "30 gün", ["unit_conversion_uncertain"]),  # 30 lies inside 28-31
        (">=", "27 gün", ["numeric_mismatch"]),  # 27 < 28
        (">", "29 gün", ["unit_conversion_uncertain"]),
        (">", "4 hafta", ["numeric_mismatch"]),  # 28 days is not above any point of 28-31
        ("<=", "28 gün", []),  # 28 <= 28
        ("<=", "30 gün", ["unit_conversion_uncertain"]),
        ("<", "27 gün", []),
        ("<", "31 gün", ["numeric_mismatch"]),  # 31 >= 31
    ],
)
def test_verify_threshold_in_months_is_a_day_interval(
    op: CheckOp, quote: str, expected: list[str]
) -> None:
    assert _reasons(_finding(quote), _rule(op, 1, "ay")) == expected


# --- Ambiguous values -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "quote",
    [
        "Satıcı 30 gün, Alıcı 15 gün önceden bildirir",
        "30 ila 60 gün önceden",
        "otuz ila altmış gün önceden",
        "30-60 gün önceden",
        "30 veya 45 gün önceden",
        "30 ile 60 gün önceden",
        "30 gün veya 15 içinde",
        "30 gün ila 60 önceden",
    ],
)
def test_verify_several_different_values_ambiguous(quote: str) -> None:
    assert _reasons(_finding(quote, value=30), AT_LEAST_30_DAYS) == ["ambiguous_value"]


@pytest.mark.parametrize(
    ("text", "kind", "expected"),
    [
        ("30 ila 60 gün", "duration", [_days(30), _days(60)]),
        ("30 ile 60 gün", "duration", [_days(30), _days(60)]),
        ("30 gün veya 15", "duration", [_days(30), _days(15)]),
        ("30 gün ila 60", "duration", [_days(30), _days(60)]),
        ("yüzde 15 ila 20", "percent", [Quantity(15, "%"), Quantity(20, "%")]),
        ("15 ila %20", "percent", [Quantity(15, "%"), Quantity(20, "%")]),
        ("500 ila ₺1.000", "amount", [Quantity(500, "TL"), Quantity(1000, "TL")]),
    ],
)
def test_extract_range_bare_number_takes_unit_either_side(
    text: str, kind: Kind, expected: list[Quantity]
) -> None:
    assert extract(text, kind) == Extraction(tuple(expected), ())


def test_verify_bare_number_left_of_percent_ambiguous() -> None:
    quote = "Gecikme cezası 15 ila %20 arasında belirlenir."
    assert _reasons(_finding(quote), _rule(">=", 20, "%")) == ["ambiguous_value"]


@pytest.mark.parametrize(
    ("text", "kind", "expected"),
    [
        ("Gecikme faizi %2 ile 30 gün içinde ödenir", "duration", [_days(30)]),
        ("Gecikme faizi yüzde 2 ile 30 gün içinde ödenir", "percent", [Quantity(2, "%")]),
        ("yüzde 15 veya 1.000 TL", "percent", [Quantity(15, "%")]),
        ("₺500 ile 30 gün", "duration", [_days(30)]),
        ("%5 ila 2.000 TL", "amount", [Quantity(2000, "TL")]),
    ],
)
def test_extract_number_with_other_kind_unit_not_linked(
    text: str, kind: Kind, expected: list[Quantity]
) -> None:
    assert extract(text, kind) == Extraction(tuple(expected), ())


def test_verify_other_kind_value_across_connector_not_ambiguous() -> None:
    quote = "Gecikme faizi %2 ile 30 gün içinde ödenir."
    assert _reasons(_finding(quote), AT_LEAST_30_DAYS) == []
    finding = _finding(
        "Gecikme faizi %2 ile 15 gün içinde ödenir.",
        status="non_compliant",
        proposal=_proposal("Gecikme faizi %2 ile 45 gün içinde ödenir."),
    )
    result = _run(finding, AT_LEAST_30_DAYS)
    assert result.proposal is not None
    assert result.proposal.code_check == "passed"
    assert result.review_reasons == []


def test_verify_thirty_days_and_one_month_not_ambiguous() -> None:
    reasons = _reasons(_finding("30 gün … bir ay"), AT_LEAST_30_DAYS)
    assert "ambiguous_value" not in reasons


# --- Unreadable value expressions are never dropped -----------------------------------------------


def test_verify_work_days_next_to_read_value_ambiguous() -> None:
    quote = "Satıcı 30 gün önceden, Alıcı ise 5 iş günü önceden bildirir"
    assert extract(quote, "duration") == Extraction((_days(30),), ("5 iş günü",))
    finding = _finding(quote, value=30, unit="gün")
    assert _reasons(finding, AT_LEAST_30_DAYS) == ["ambiguous_value"]


def test_verify_dative_form_second_value_ambiguous() -> None:
    quote = "30 gün içinde, gecikme halinde 90 güne kadar"
    assert extract(quote, "duration").values == (_days(30), _days(90))
    assert _reasons(_finding(quote), AT_MOST_60_DAYS) == ["ambiguous_value"]


def test_verify_work_days_alone_numeric_mismatch() -> None:
    assert _reasons(_finding("5 iş günü önceden"), AT_LEAST_30_DAYS) == ["numeric_mismatch"]


def test_verify_calendar_days_read_as_days() -> None:
    assert _reasons(_finding("30 takvim günü önceden"), AT_LEAST_30_DAYS) == []


@pytest.mark.parametrize(
    ("text", "expression"),
    [
        ("5 ayrı teslimat", "5 ayrı"),
        ("5 iş günü", "5 iş günü"),
        ("bir buçuk ay", "bir buçuk ay"),
        ("1.5 yıl", "1.5 yıl"),
        ("12.5 gün", "12.5 gün"),
        ("30 günlüğüne", "30 günlüğüne"),
        ("iki üç gün", "iki üç gün"),
        ("30 (otuz, yani) gün", "30 (otuz, yani) gün"),
        ("30 (gün)", "30 (gün"),
        ("2 sene", "2 sene"),
        ("48 saat içinde", "48 saat"),
        ("90 dakikalık", "90 dakikalık"),
    ],
)
def test_extract_duration_unreadable_expression(text: str, expression: str) -> None:
    assert expression in extract(text, "duration").unreadable


@pytest.mark.parametrize(
    ("text", "kind", "expression"),
    [
        ("yüzde on beşi", "percent", "yüzde on beşi"),
        ("yüzde yirmi beşinden fazla", "percent", "yüzde yirmi beşinden"),
        ("yüzde on beşten fazla", "percent", "yüzde on beşten"),
        ("yüzde otuz ikisi", "percent", "yüzde otuz ikisi"),
        ("yüzde yüz yirmisi", "percent", "yüzde yüz yirmisi"),
        ("on birinci gün", "duration", "on birinci gün"),
    ],
)
def test_extract_inflected_number_word_unreadable_not_cut_short(
    text: str, kind: Kind, expression: str
) -> None:
    assert extract(text, kind) == Extraction((), (expression,))


def test_verify_inflected_percent_word_numeric_mismatch() -> None:
    quote = "Cezai şart, sözleşme bedelinin yüzde on beşi oranındadır."
    rule = _rule("<=", 10, "%")
    assert _reasons(_finding(quote), rule) == ["numeric_mismatch"]
    assert _reasons(_finding(quote, value=10, unit="%"), rule) == ["numeric_mismatch"]


@pytest.mark.parametrize(
    ("text", "expression"),
    [("her ayın 15. günü", "15. günü"), ("30. gün", "30. gün"), ("30, gün", "30, gün")],
)
def test_extract_digit_run_ending_in_separator_before_unit_unreadable(
    text: str, expression: str
) -> None:
    assert extract(text, "duration") == Extraction((), (expression,))


def test_verify_ordinal_day_numeric_mismatch() -> None:
    quote = "Ödeme, faturayı izleyen ayın 15. günü yapılır."
    assert _reasons(_finding(quote), AT_MOST_60_DAYS) == ["numeric_mismatch"]


@pytest.mark.parametrize(
    "quote", ["[KISI_1] 30 gün içinde ödeme yapar.", "Madde 5.2 30 gün içinde ödeme yapılır."]
)
def test_verify_digit_number_before_value_not_gap_word(quote: str) -> None:
    assert extract(quote, "duration") == Extraction((_days(30),), ())
    assert _reasons(_finding(quote, value=30, unit="gün"), AT_LEAST_30_DAYS) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[TELEFON_2] gün içinde", Extraction((), ())),
        ("[TCKN_1] günü", Extraction((), ())),
        ("[KISI_12] ay", Extraction((), ())),
        ("[TCKN_1] 30 gün", Extraction((_days(30),), ())),
        ("30 [KISI_1] gün", Extraction((), ("30 [KISI_1] gün",))),
    ],
)
def test_extract_placeholder_one_word_not_a_number(text: str, expected: Extraction) -> None:
    assert extract(text, "duration") == expected


def test_verify_placeholder_digit_not_read_as_value() -> None:
    finding = _finding("[TELEFON_2] gün içinde bildirilir")
    # The placeholder is not read; the compliant finding's clause holds a TELEFON placeholder.
    assert _reasons(finding, AT_MOST_60_DAYS) == ["numeric_mismatch", "masked_value_in_clause"]
    assert extract("%[TCKN_1] faiz", "percent") == Extraction((), ())
    assert extract("₺[IBAN_3] ceza", "amount") == Extraction((), ())


@pytest.mark.parametrize(
    ("text", "kind", "expression"),
    [
        ("yüzde beşi oranında", "percent", "yüzde beşi"),
        ("% beşi oranında", "percent", "% beşi"),
        ("beşer gün içinde", "duration", "beşer gün"),
        ("birkaç gün içinde", "duration", "birkaç gün"),
        ("üçüncü ay sonunda", "duration", "üçüncü ay"),
        ("BİRKAÇ GÜN içinde", "duration", "BİRKAÇ GÜN"),
        ("beşer takvim günü", "duration", "beşer takvim günü"),
        ("onar TL ceza", "amount", "onar TL"),
    ],
)
def test_extract_inflected_number_word_in_number_place_unreadable(
    text: str, kind: Kind, expression: str
) -> None:
    assert extract(text, kind) == Extraction((), (expression,))


def test_extract_inflected_number_word_elsewhere_not_an_expression() -> None:
    assert extract("Taraflar birlikte 30 gün içinde öder.", "duration") == Extraction(
        (_days(30),), ()
    )
    assert extract("5 beşer gün", "duration") == Extraction((), ("5 beşer gün",))


def test_verify_inflected_number_word_next_to_value_ambiguous() -> None:
    assert _reasons(_finding("30 gün, ara ödemeler beşer gün içinde"), AT_LEAST_30_DAYS) == [
        "ambiguous_value"
    ]


def test_verify_copula_duration_read() -> None:
    assert extract("Bildirim süresi 45 gündür.", "duration") == Extraction((_days(45),), ())
    assert _reasons(_finding("Bildirim süresi 45 gündür."), AT_LEAST_30_DAYS) == []


def test_extract_sentence_final_separator_read_ordinal_unreadable() -> None:
    assert extract("Gecikme faizi %15.", "percent") == Extraction((Quantity(15, "%"),), ())
    assert extract("Gecikme faizi yüzde 15,", "percent") == Extraction((Quantity(15, "%"),), ())
    assert extract("ayın 15. günü", "duration") == Extraction((), ("15. günü",))


def test_verify_unreadable_suffix_alone_numeric_mismatch() -> None:
    assert _reasons(_finding("5 ayrı teslimat"), AT_LEAST_30_DAYS) == ["numeric_mismatch"]


def test_verify_malformed_number_next_to_value_ambiguous() -> None:
    assert _reasons(_finding("30 gün veya 1.5 yıl"), AT_LEAST_30_DAYS) == ["ambiguous_value"]


_ALL_FORMS: dict[CheckUnit, tuple[str, ...]] = {
    "gün": ("gün", "günlük", "günü", "güne", "günde", "günden", "günün", "günler", "gününe",
            "gününde", "gününden", "gündür"),
    "hafta": ("hafta", "haftalık", "haftası", "haftaya", "haftada", "haftadan", "haftanın",
              "haftalar", "haftasına", "haftasında", "haftasından", "haftadır"),
    "ay": ("ay", "aylık", "ayı", "aya", "ayda", "aydan", "ayın", "aylar", "ayına", "ayında",
           "ayından", "aydır"),
    "yıl": ("yıl", "yıllık", "yılı", "yıla", "yılda", "yıldan", "yılın", "yıllar", "yılına",
            "yılında", "yılından", "yıldır"),
}  # fmt: skip


@pytest.mark.parametrize(
    ("unit", "form"), [(unit, form) for unit, forms in _ALL_FORMS.items() for form in forms]
)
def test_extract_closed_suffix_list_read(unit: CheckUnit, form: str) -> None:
    assert extract(f"7 {form}", "duration") == Extraction((Quantity(7, unit),), ())
    assert extract(f"yedi {form.upper()}", "duration").values == (Quantity(7, unit),)


_ARBITRARY_SUFFIXES = (
    "", "x", "lar", "ler", "lerde", "lık", "lığına", "da", "ımız", "ce", "sal", "ı", "rı", "nı",
    "dır", "ki", "lıkta", "dan", "abc", "q", "lük", "ğ", "ünü", "ında", "ımızdan", "sel", "ce'ye",
    "-", ".", "'den", "lik", "zzz", "ları", "lerimiz",
)  # fmt: skip
_NUMBERS = ("5", "otuz", "1,5", "1.5", "1.250", "on iki", "07")


def _turkish_upper(text: str) -> str:
    return text.replace("i", "İ").replace("ı", "I").upper()


@pytest.mark.parametrize("stem", ["gün", "hafta", "ay", "yıl"])
def test_extract_number_stem_any_suffix_never_dropped(stem: str) -> None:
    for number in _NUMBERS:
        for suffix in _ARBITRARY_SUFFIXES:
            for between in ("", "iş ", "takvim ", "zz "):
                text = f"Metin {number} {between}{stem}{suffix} son."
                result = extract(text, "duration")
                assert result.values or result.unreadable, text
                upper_text = _turkish_upper(text)
                upper = extract(upper_text, "duration")
                assert upper.values or upper.unreadable, upper_text


# --- Percent and amount ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["%15", "% 15", "yüzde 15", "yüzde on beş", "YÜZDE 15", "\uff0515"]
)
def test_extract_percent_read(text: str) -> None:
    assert extract(f"gecikme faizi {text} olarak", "percent") == Extraction(
        (Quantity(15, "%"),), ()
    )


def test_extract_percent_sign_after_number_unreadable() -> None:
    assert extract("faiz 15% olarak", "percent") == Extraction((), ("15%",))
    assert _reasons(_finding("faiz 15% olarak"), _rule("<=", 20, "%")) == ["numeric_mismatch"]


def test_extract_sign_belongs_only_to_adjacent_number() -> None:
    # Across punctuation a sign belongs to no number: "1.000 ₺, 30 gün"de 30 tutar değildir.
    amount = "Ceza bedeli 1.000 ₺, 30 gün içinde ödenir."
    assert extract(amount, "amount") == Extraction((Quantity(1000, "TL"),), ())
    assert _reasons(_finding(amount), _rule("<=", 500, "TL")) == ["numeric_mismatch"]
    assert extract("Ceza bedeli 1.000 TL, 30 gün içinde", "amount") == Extraction(
        (Quantity(1000, "TL"),), ()
    )
    assert extract("faiz yüzde, 15 oranında", "percent") == Extraction((), ())
    assert extract("Gecikme faizi 15%, 30 gün içinde", "percent") == Extraction((), ("15%",))
    # With only a space between, the sign is next to both numbers.
    percent = "Gecikme faizi 15% 30 gün içinde işler."
    assert extract(percent, "percent") == Extraction((Quantity(30, "%"),), ("15%",))
    assert _reasons(_finding(percent), _rule("<=", 50, "%")) == ["ambiguous_value"]


def test_extract_percent_range_takes_unit() -> None:
    assert extract("yüzde 15 ila 20", "percent").values == (Quantity(15, "%"), Quantity(20, "%"))


def test_extract_percent_ignores_other_kinds() -> None:
    assert extract("30 gün içinde %2 faiz", "percent").values == (Quantity(2, "%"),)
    assert extract("30 gün içinde %2 faiz", "duration").values == (_days(30),)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1.250.000 TL", 1_250_000),
        ("5.000.000,00 TL", 5_000_000),
        ("1.000 Türk Lirası", 1000),
        ("250 lira", 250),
        ("₺5.000", 5000),
        ("5.000 ₺", 5000),
        ("2.500 TL'dir", 2500),
        ("1250,5 TL", 1250.5),
        ("TL 500.000", 500_000),
        ("TL500.000", 500_000),
        ("₺ 1.000", 1000),
        ("500.000 TRY", 500_000),
        ("TRY 500.000", 500_000),
    ],
)
def test_extract_amount_read(text: str, value: float) -> None:
    assert extract(f"Ceza bedeli {text} olarak", "amount") == Extraction(
        (Quantity(value, "TL"),), ()
    )


@pytest.mark.parametrize(
    "text",
    [
        "1 milyon TL",
        "beş yüz TL",
        "50.000 USD",
        "$50.000",
        "50.000 $",
        "€50.000",
        "50.000 EUR",
        "50.000 euro",
        "50.000 dolar",
        "50.000 avro",
        "50.000 £",
        "50.000 GBP",
        "50.000 sterlin",
        "1.5 TL",
        "USD 500.000",
        "$500",
        "$ 500",
        "EUR 1.000",
        "dolar 500",
        "GBP 300",
        "500.000 CHF",
        "CHF 500.000",
        "500.000 İsviçre Frangı",
        "1.000 JPY",
        "¥1.000",
    ],
)
def test_extract_amount_unreadable(text: str) -> None:
    result = extract(f"Ceza bedeli {text} olarak", "amount")
    assert result.values == ()
    assert len(result.unreadable) == 1


def test_verify_foreign_currency_next_to_tl_ambiguous() -> None:
    rule = _rule("<=", 1_000_000, "TL")
    assert _reasons(_finding("500.000 TL veya 50.000 USD"), rule) == ["ambiguous_value"]
    assert _reasons(_finding("50.000 USD"), rule) == ["numeric_mismatch"]


def test_verify_prefixed_tl_read_prefixed_foreign_currency_ambiguous() -> None:
    rule = _rule("<=", 1_000_000, "TL")
    assert _reasons(_finding("Ceza bedeli TL 500.000 olarak uygulanır."), rule) == []
    quote = "Ceza bedeli 500.000 TL veya USD 300.000 olarak uygulanır."
    assert extract(quote, "amount") == Extraction((Quantity(500_000, "TL"),), ("USD 300.000",))
    assert _reasons(_finding(quote), rule) == ["ambiguous_value"]


# --- What continues a number makes the expression unreadable --------------------------------------


@pytest.mark.parametrize(
    ("text", "kind", "expression"),
    [
        # Multiplier or fraction word right after the number, in every reading form.
        ("Ceza ₺1,5 milyon olarak", "amount", "₺1,5 milyon"),
        ("Ceza TL 1,5 milyon olarak", "amount", "TL 1,5 milyon"),
        ("Ceza 1,5 milyon TL olarak", "amount", "1,5 milyon TL"),
        ("Ceza ₺5 milyonluk olarak", "amount", "₺5 milyonluk"),
        ("Ceza 2 milyar TL olarak", "amount", "2 milyar TL"),
        ("Ceza 1 milyon 500 bin TL olarak", "amount", "1 milyon 500 bin TL"),
        ("faiz yüzde bir buçuk olarak", "percent", "yüzde bir buçuk"),
        ("faiz %1 buçuk olarak", "percent", "%1 buçuk"),
        ("faiz % 1 çeyrek olarak", "percent", "% 1 çeyrek"),
        ("faiz yüzde iki virgül beş olarak", "percent", "yüzde iki virgül beş"),
        ("süre 2 yarım yıl olarak", "duration", "2 yarım yıl"),
        ("Ceza ₺5 trilyon olarak", "amount", "₺5 trilyon"),
        ("Ceza ₺5 mn olarak", "amount", "₺5 mn"),
        ("Ceza 5 mln TL olarak", "amount", "5 mln TL"),
        ("faiz yüzde iki nokta beş olarak", "percent", "yüzde iki nokta beş"),
        ("otuz (30 bin) gün", "duration", "otuz (30 bin) gün"),
        # Digits joined by "/" or the fraction slash; NFKC turns "½" into 1, U+2044, 2.
        ("süre 1/2 yıl olarak", "duration", "1/2 yıl"),
        ("süre 1 / 2 yıl olarak", "duration", "1 / 2 yıl"),
        ("süre ½ yıl olarak", "duration", "1\u20442 yıl"),
        ("faiz %1/2 olarak", "percent", "%1/2"),
        # Space-grouped digits: later groups of exactly three digits.
        ("Ceza 1 000 000 TL olarak", "amount", "1 000 000 TL"),
        ("Ceza 1\u00a0000\u00a0000 TL olarak", "amount", "1 000 000 TL"),
        ("Ceza ₺1 000 olarak", "amount", "₺1 000"),
        ("Teslim 1 000 gün içinde", "duration", "1 000 gün"),
    ],
)
def test_extract_number_continued_by_neighbour_unreadable(
    text: str, kind: Kind, expression: str
) -> None:
    assert extract(text, kind) == Extraction((), (expression,))


def test_extract_continued_bare_number_linked_by_connector_unreadable() -> None:
    assert extract("30 gün veya 15 bin", "duration") == Extraction((_days(30),), ("15 bin",))


def test_extract_word_starting_with_multiplier_does_not_take_next_number() -> None:
    text = "Madde 5 binaenaleyh 30 gün içinde"
    assert extract(text, "duration") == Extraction((_days(30),), ())


def test_extract_space_grouping_needs_three_digit_groups() -> None:
    assert extract("Madde 5.2 30 gün", "duration") == Extraction((_days(30),), ())
    assert extract("Madde 5 3000 gün", "duration") == Extraction((Quantity(3000, "gün"),), ())


@pytest.mark.parametrize(
    ("quote", "rule"),
    [
        ("Cezai şart ₺1,5 milyon olarak uygulanır.", _rule("<=", 1_000_000, "TL")),
        ("Cezai şart TL 2 milyon olarak uygulanır.", _rule("<=", 1_000_000, "TL")),
        ("Cezai şart 1 000 000 TL olarak uygulanır.", _rule("<=", 1000, "TL")),
        ("Gecikme faizi %1 buçuk olarak uygulanır.", _rule("<=", 1, "%")),
        ("Gecikme faizi yüzde bir buçuk olarak uygulanır.", _rule("<=", 1, "%")),
        ("Süre 1/2 yıl olarak belirlenir.", _rule("<=", 2, "yıl")),
    ],
)
def test_verify_part_of_number_not_read_numeric_mismatch(quote: str, rule: Rule) -> None:
    assert _reasons(_finding(quote), rule) == ["numeric_mismatch"]


@pytest.mark.parametrize(
    ("text", "kind", "expression"),
    [
        # A multiplier or fraction word with no number before it is a number of its own; the
        # number after it is part of it, never a value.
        ("Kayıtlar bin beş yüz gün saklanır", "duration", "bin beş yüz gün"),
        ("Kayıtlar bin 500 gün saklanır", "duration", "bin 500 gün"),
        ("gecikmede yarım yıldır", "duration", "yarım yıldır"),
        ("süre çeyrek yıl olarak", "duration", "çeyrek yıl"),
        ("tekrarında bin TL'dir", "amount", "bin TL"),
    ],
)
def test_extract_leading_multiplier_word_unreadable(text: str, kind: Kind, expression: str) -> None:
    assert extract(text, kind) == Extraction((), (expression,))


def test_verify_leading_fraction_word_next_to_value_ambiguous() -> None:
    quote = "Teslim süresi 30 gün, gecikmede yarım yıldır."
    assert _reasons(_finding(quote), AT_MOST_60_DAYS) == ["ambiguous_value"]


def test_verify_quote_reading_part_after_leading_multiplier_quote_not_found() -> None:
    clause = "Kayıtlar bin beş yüz gün saklanır."
    assert _reasons(_finding("beş yüz gün"), _rule("<=", 730, "gün"), clause) == ["quote_not_found"]


@pytest.mark.parametrize(
    ("text", "value"),
    [("Madde Kırk. Beş gün önceden", 5), ("Nüsha sayısı on; üç gün içinde", 3)],
)
def test_extract_number_words_not_joined_across_punctuation(text: str, value: float) -> None:
    assert extract(text, "duration").values == (_days(value),)


def test_verify_number_words_across_punctuation_not_cleared() -> None:
    quote = "Madde Kırk. Beş gün önceden bildirim yapılır."
    assert _run(_finding(quote), _rule(">=", 10, "gün")).needs_review is True


def test_extract_superscript_glued_to_digits_unreadable() -> None:
    # NFKC turns the footnote mark into a digit: "30¹" would read as 301.
    assert extract("Ödeme 30¹ gün içinde", "duration") == Extraction((), ("301 gün",))


@pytest.mark.parametrize(
    ("quote", "rule"),
    [
        ("Ek-2, 225 takvim günü içinde bildirilir.", AT_LEAST_30_DAYS),
        ("Madde 3, 447.000 TL ödenir.", _rule("<=", 500_000, "TL")),
        ("Ek-2, %24 faiz uygulanır.", _rule("<=", 30, "%")),
        ("Ek-2, TL 187.203 ödenir.", _rule("<=", 200_000, "TL")),
    ],
)
def test_verify_punctuation_after_number_ends_it(quote: str, rule: Rule) -> None:
    assert _reasons(_finding(quote), rule) == []


# --- Parenthetical restatements -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind", "values"),
    [
        (
            "Ödeme 30 (otuz) ila 60 (altmış) gün",
            "duration",
            [_days(30), _days(30)] + [_days(60)] * 2,
        ),
        ("Ödeme otuz (30) ila altmış (60) gün", "duration", [_days(30)] * 2 + [_days(60)] * 2),
        ("Ödeme 30 (otuz) ila 60 gün", "duration", [_days(30), _days(30), _days(60)]),
        ("Ödeme 30 gün veya (90).", "duration", [_days(30), _days(90)]),
        ("Ödeme 30 gün (veya 90).", "duration", [_days(30), _days(90)]),
        ("Faiz 10 (on) ila yüzde 15", "percent", [Quantity(10, "%")] * 2 + [Quantity(15, "%")]),
        ("Sorumluluk ₺1.000 (5.000) ile", "amount", [Quantity(1000, "TL"), Quantity(5000, "TL")]),
        ("Sorumluluk TL 1.000 (5.000) ile", "amount", [Quantity(1000, "TL"), Quantity(5000, "TL")]),
    ],
)
def test_extract_restatement_does_not_hide_a_value(
    text: str, kind: Kind, values: list[Quantity]
) -> None:
    assert extract(text, kind) == Extraction(tuple(values), ())


@pytest.mark.parametrize(
    ("text", "kind", "value", "expression"),
    [
        ("Ödeme otuzbeş (30) gün", "duration", _days(30), "otuzbeş (30) gün"),
        ("Bedel beşyüzbin (500) TL", "amount", Quantity(500, "TL"), "beşyüzbin (500) TL"),
        ("faizi yüzde 15 (yirmibeş) oranında", "percent", Quantity(15, "%"), "yirmibeş"),
        ("Bedel ₺500 (beşyüzbin) olarak", "amount", Quantity(500, "TL"), "beşyüzbin"),
    ],
)
def test_extract_inflected_word_next_to_restatement_unreadable(
    text: str, kind: Kind, value: Quantity, expression: str
) -> None:
    assert extract(text, kind) == Extraction((value,), (expression,))


@pytest.mark.parametrize(
    ("quote", "rule"),
    [
        ("Ödeme 30 (otuz) ila 60 (altmış) gün içinde yapılır.", _rule(">=", 60, "gün")),
        ("Ödeme otuzbeş (30) gün içinde yapılır.", AT_MOST_60_DAYS),
        ("Sorumluluk ₺1.000 (5.000) ile sınırlıdır.", _rule("<=", 1000, "TL")),
        ("Gecikme faizi yüzde 15 (yirmibeş) oranındadır.", _rule("<=", 15, "%")),
    ],
)
def test_verify_restatement_conflict_ambiguous(quote: str, rule: Rule) -> None:
    assert _reasons(_finding(quote), rule) == ["ambiguous_value"]


# --- A quote match may not cut a value expression -------------------------------------------------


@pytest.mark.parametrize(
    ("quote", "clause", "rule"),
    [
        ("15 oranında uygulanır", "Gecikme faizi %15 oranında uygulanır.", _rule("<=", 20, "%")),
        ("Ödeme 30", "Ödeme 30 gün içinde yapılır.", AT_LEAST_30_DAYS),
        ("Bildirim 5 iş", "Bildirim 5 iş günü önceden yapılır.", AT_LEAST_30_DAYS),
        ("Ceza 1,5", "Ceza 1,5 milyon TL olarak uygulanır.", _rule("<=", 1_000_000, "TL")),
        ("000 gün içinde", "Teslim 1 000 gün içinde yapılır.", AT_MOST_60_DAYS),
        ("2 yıl olarak", "Süre 1/2 yıl olarak belirlenir.", _rule("<=", 3, "yıl")),
        ("(30) gün önceden", "Bildirim otuz (30) gün önceden yapılır.", AT_LEAST_30_DAYS),
        ("500.000 olarak", "Ceza USD 500.000 olarak uygulanır.", _rule("<=", 1_000_000, "TL")),
        # An expression of another kind than the rule's is not cut either.
        ("30 gün başına", "Gecikme faizi %30 gün başına işler.", AT_LEAST_30_DAYS),
    ],
)
def test_verify_quote_cutting_value_expression_quote_not_found(
    quote: str, clause: str, rule: Rule
) -> None:
    assert _reasons(_finding(quote), rule, clause) == ["quote_not_found"]


def test_verify_quote_cutting_expression_skipped_clean_occurrence_taken() -> None:
    clause = "Gecikme faizi %30 gün başına işler; bildirim 30 gün önceden yapılır."
    result = _run(_finding("30 gün", value=30, unit="gün"), AT_LEAST_30_DAYS, clause)
    assert result.review_reasons == []
    assert result.quote == "30 gün"


def test_verify_quote_after_prefix_across_punctuation_matches() -> None:
    clause = "Ceza bedeli 1.000 ₺, 30 gün içinde ödenir."
    assert _reasons(_finding("30 gün içinde"), AT_LEAST_30_DAYS, clause) == []


def test_extract_spans_cover_every_expression() -> None:
    text = "Faiz %15, ceza 5 iş günü içinde"
    assert extract(text, "percent").spans == ((5, 8),)
    assert extract(text, "duration").spans == ((15, 24),)
    assert text[15:24] == "5 iş günü"


def test_verify_quote_ending_before_punctuation_after_prefixed_value_matches() -> None:
    clause = "Gecikme faizi %15. Ceza bedeli TL 500.000, ödeme 30 gün."
    assert _reasons(_finding("Gecikme faizi %15"), _rule("<=", 20, "%"), clause) == []
    rule = _rule("<=", 1_000_000, "TL")
    assert _reasons(_finding("Ceza bedeli TL 500.000"), rule, clause) == []


def test_extract_kinds_kept_apart() -> None:
    text = "30 gün içinde %15 faizle 1.000 TL"
    assert extract(text, "duration").values == (_days(30),)
    assert extract(text, "percent").values == (Quantity(15, "%"),)
    assert extract(text, "amount").values == (Quantity(1000, "TL"),)


# --- Critical rule, not_found, no_finding ---------------------------------------------------------


def test_verify_not_found_normal_and_critical_both_queued() -> None:
    normal = _rule(severity="normal", rule_id="normal")
    critical = _rule(severity="critical", rule_id="critical")
    findings = [
        _finding(status="not_found", clause_id=None, rule_id="normal"),
        _finding(status="not_found", clause_id=None, rule_id="critical"),
    ]
    out = verify(findings, [normal, critical], [], False)
    assert [f.needs_review for f in out] == [True, True]
    assert out[0].review_reasons == ["unverified_not_found"]
    assert out[1].review_reasons == ["unverified_not_found", "critical_not_found"]
    off = verify(findings, [normal, critical], [], False, enabled=False)
    assert [f.needs_review for f in off] == [False, False]
    assert [f.review_reasons for f in off] == [[], []]


@pytest.mark.parametrize("reason", ["no_submit", "step_limit", "rate_limited", "llm_error"])
def test_verify_no_finding_only_no_finding_reason(reason: str) -> None:
    finding = _finding(status="not_found", clause_id=None, no_finding_reason=reason)
    result = _run(finding, _rule(">=", 30, "gün", severity="critical"))
    assert result.review_reasons == ["no_finding"]
    assert result.needs_review is True
    assert result.no_finding_reason == reason


def test_verify_no_finding_with_injection_both_reasons() -> None:
    finding = _finding(status="not_found", clause_id=None, no_finding_reason="llm_error")
    assert _run(finding, NO_CHECK, injection=True).review_reasons == [
        "no_finding",
        "injection_suspected",
    ]


# --- Proposal check -------------------------------------------------------------------------------


def _non_compliant_with(text: str) -> Finding:
    return _finding("15 gün önceden", status="non_compliant", proposal=_proposal(text))


def test_verify_proposal_below_threshold_failed() -> None:
    result = _run(_non_compliant_with("Bildirim 15 gün önceden yapılır."), AT_LEAST_30_DAYS)
    assert result.proposal is not None
    assert result.proposal.code_check == "failed"
    assert result.review_reasons == ["proposal_noncompliant"]
    assert result.needs_review is True


def test_verify_proposal_meeting_threshold_passed() -> None:
    result = _run(_non_compliant_with("Bildirim 45 gün önceden yapılır."), AT_LEAST_30_DAYS)
    assert result.proposal is not None
    assert result.proposal.code_check == "passed"
    assert result.review_reasons == []
    assert result.needs_review is False


@pytest.mark.parametrize(
    "text",
    [
        "Bildirim süresi 1 ay olacaktır.",
        "Bildirim süresi bir ay olacaktır.",
        "Bildirim 30 ila 60 gün önceden yapılır.",
        "Bildirim makul bir süre önceden yapılır.",
        "Bildirim 45 iş günü önceden yapılır.",
        "Satıcı 45 gün, Alıcı 5 iş günü önceden bildirir.",
        "Bildirim süresi birkaç gündür.",
    ],
)
def test_verify_proposal_uncertain_or_unreadable_failed(text: str) -> None:
    result = _run(_non_compliant_with(text), AT_LEAST_30_DAYS)
    assert result.proposal is not None
    assert result.proposal.code_check == "failed"
    assert result.review_reasons == ["proposal_noncompliant"]


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("Bildirim, Madde Kırk. Beş gün önceden yapılır.", _rule(">=", 10, "gün")),
        ("Bildirim süresi bin beş yüz gündür.", _rule("<=", 600, "gün")),
        ("Ödeme 30 (otuz) ila 60 (altmış) gün içinde yapılır.", _rule(">=", 60, "gün")),
        ("Ödeme 30¹ gün içinde yapılır.", _rule(">=", 60, "gün")),
    ],
)
def test_verify_proposal_with_hidden_or_partial_value_failed(text: str, rule: Rule) -> None:
    result = _run(_non_compliant_with(text), rule)
    assert result.proposal is not None
    assert result.proposal.code_check == "failed"


@pytest.mark.parametrize("text", ["kırk beş (45) gün", "Bildirim süresi 45 gündür."])
def test_verify_proposal_spelled_and_digits_or_copula_passed(text: str) -> None:
    result = _run(_non_compliant_with(text), AT_LEAST_30_DAYS)
    assert result.proposal is not None
    assert result.proposal.code_check == "passed"


def test_verify_proposal_rule_without_check_unverifiable_not_queued() -> None:
    proposal = _proposal("Sorumluluk sözleşme bedeli ile sınırlıdır.")
    finding = _finding("Satıcı sorumluluğu sınırsızdır.", status="non_compliant", proposal=proposal)
    result = _run(finding, NO_CHECK)
    assert result.proposal is not None
    assert result.proposal.code_check == "unverifiable"
    assert result.review_reasons == []
    assert result.needs_review is False


def test_verify_non_compliant_without_proposal_missing_proposal() -> None:
    result = _run(_finding("15 gün önceden", status="non_compliant"), AT_LEAST_30_DAYS)
    assert result.review_reasons == ["missing_proposal"]


# --- Escalation, injection, ordering --------------------------------------------------------------


def test_verify_agent_escalated_queued() -> None:
    finding = _finding("30 gün önceden", escalation_reason="Madde çelişkili.")
    assert _reasons(finding, AT_LEAST_30_DAYS) == ["agent_escalated"]


def test_verify_injection_suspected_all_findings() -> None:
    rule2 = _rule(rule_id="r2")
    findings = [_finding("30 gün önceden"), _finding("30 gün önceden", rule_id="r2")]
    out = verify(findings, [AT_LEAST_30_DAYS, rule2], [_clause("30 gün önceden")], True)
    assert [f.review_reasons for f in out] == [["injection_suspected"], ["injection_suspected"]]
    assert all(f.needs_review for f in out)


def test_verify_reasons_in_signal_table_order() -> None:
    finding = _finding("uydurma alıntı", status="non_compliant", escalation_reason="Emin değilim.")
    result = _run(finding, AT_LEAST_30_DAYS, "Başka metin.", injection=True)
    assert result.review_reasons == [
        "quote_not_found",
        "missing_proposal",
        "agent_escalated",
        "injection_suspected",
    ]


# --- enabled=False, purity, errors ----------------------------------------------------------------


def test_verify_disabled_only_escalation_and_no_finding() -> None:
    clause = "Bildirim  \u201c15 gün\u201d önceden yapılır."
    rule2 = _rule(rule_id="r2")
    rule3 = _rule(rule_id="r3")
    stale = _proposal("10 gün", code_check="passed")
    findings = [
        _finding('Bildirim "15 gün" önceden yapılır.', status="non_compliant", proposal=stale),
        _finding("uydurma", rule_id="r2", escalation_reason="Belirsiz."),
        _finding(status="not_found", clause_id=None, rule_id="r3", no_finding_reason="step_limit"),
        _finding("15 gün önceden", status="non_compliant", rule_id="r4"),
    ]
    rules = [AT_LEAST_30_DAYS, rule2, rule3, _rule(">=", 30, "gün", rule_id="r4")]
    out = verify(findings, rules, [_clause(clause)], True, enabled=False)
    assert [f.review_reasons for f in out] == [[], ["agent_escalated"], ["no_finding"], []]
    assert [f.needs_review for f in out] == [False, True, True, False]
    assert out[3].proposal is None
    assert out[0].quote == 'Bildirim "15 gün" önceden yapılır.'
    assert out[0].proposal is not None
    assert out[0].proposal.code_check is None


def test_verify_interrupted_run_verified_quote_needs_review() -> None:
    finding = _finding("30 gün önceden", value=30, unit="gün", interrupted_by="step_limit")
    result = _run(finding, AT_LEAST_30_DAYS)
    assert result.needs_review is True
    assert result.review_reasons == ["run_interrupted"]
    assert result.quote == "30 gün önceden"
    # Without the mark the same finding passes: the mark alone queues it.
    assert _reasons(_finding("30 gün önceden", value=30, unit="gün"), AT_LEAST_30_DAYS) == []


@pytest.mark.parametrize("reason", ["step_limit", "rate_limited", "llm_error"])
def test_verify_disabled_interrupted_run_still_queued(reason: str) -> None:
    finding = _finding("uydurma alıntı", status="non_compliant", interrupted_by=reason)
    result = _run(finding, AT_LEAST_30_DAYS, "Başka metin.", enabled=False)
    assert result.review_reasons == ["run_interrupted"]
    assert result.needs_review is True


def test_verify_interrupted_run_other_signals_still_computed() -> None:
    finding = _finding(
        "uydurma alıntı", escalation_reason="Emin değilim.", interrupted_by="llm_error"
    )
    result = _run(finding, AT_LEAST_30_DAYS, "Başka metin.", injection=True)
    assert result.review_reasons == [
        "quote_not_found",
        "agent_escalated",
        "run_interrupted",
        "injection_suspected",
    ]


def test_finding_interrupted_with_no_finding_reason_rejected() -> None:
    with pytest.raises(ValidationError, match="interrupted_by ve no_finding_reason"):
        _finding(
            status="not_found",
            clause_id=None,
            no_finding_reason="step_limit",
            interrupted_by="step_limit",
        )


def test_verify_input_not_mutated() -> None:
    clause = "Bildirim  \u201c15 gün\u201d önceden yapılır."
    proposal = _proposal("10 gün")
    finding = _finding(
        'Bildirim "15 gün" önceden yapılır.', status="non_compliant", proposal=proposal
    )
    before = finding.model_dump()
    out = verify([finding], [AT_LEAST_30_DAYS], [_clause(clause)], True)
    assert finding.model_dump() == before
    assert out[0] is not finding
    assert out[0].proposal is not finding.proposal
    assert out[0].quote == clause


def test_verify_called_twice_same_result() -> None:
    clause = "Bildirim  \u201c1 ay\u201d önceden yapılır; ceza %5."
    first = _finding(
        'Bildirim "1 ay" önceden yapılır;',
        status="non_compliant",
        proposal=_proposal("1 ay"),
        escalation_reason="Kontrol.",
    )
    findings = [
        first,
        _finding(status="not_found", clause_id=None, rule_id="r2"),
        _finding("uydurma", rule_id="r3"),
    ]
    rules = [AT_LEAST_30_DAYS, _rule(severity="critical", rule_id="r2"), _rule(rule_id="r3")]
    once = verify(findings, rules, [_clause(clause)], True)
    twice = verify(once, rules, [_clause(clause)], True)
    assert twice == once
    assert once[0].review_reasons == [
        "unit_conversion_uncertain",
        "proposal_noncompliant",
        "agent_escalated",
        "injection_suspected",
    ]


def test_verify_on_own_output_same_result_with_expressions_and_prefixes() -> None:
    clause = "Ceza TL 500.000; faiz %30 gün başına; bildirim 30 gün önceden; ek ₺1,5 milyon."
    rules = [
        _rule("<=", 1_000_000, "TL", rule_id="r1"),
        _rule(">=", 30, "gün", rule_id="r2"),
        _rule("<=", 1_000_000, "TL", rule_id="r3"),
        _rule("<=", 1_000_000, "TL", rule_id="r4"),
    ]
    findings = [
        _finding("Ceza TL 500.000", rule_id="r1"),
        _finding("30 gün", rule_id="r2", value=30, unit="gün"),
        _finding("₺1,5 milyon", rule_id="r3"),
        _finding(
            "500.000",
            rule_id="r4",
            status="non_compliant",
            proposal=_proposal("Ceza ₺2 milyon olarak uygulanır."),
        ),
    ]
    once = verify(findings, rules, [_clause(clause)], False)
    assert [f.review_reasons for f in once] == [
        ["ambiguous_value"],  # "₺1,5 milyon" outside the quote cannot be read
        [],
        ["numeric_mismatch"],
        ["quote_not_found", "proposal_noncompliant"],
    ]
    assert verify(once, rules, [_clause(clause)], False) == once


def test_verify_previous_reasons_rebuilt_from_scratch() -> None:
    finding = _finding("30 gün önceden", needs_review=True, review_reasons=["numeric_mismatch"])
    result = _run(finding, AT_LEAST_30_DAYS)
    assert result.review_reasons == []
    assert result.needs_review is False


def test_verify_unknown_rule_id_raises() -> None:
    with pytest.raises(ValueError, match="olmayan rule_id"):
        verify([_finding("x", rule_id="missing")], [NO_CHECK], [_clause("x")], False)
    with pytest.raises(ValueError):
        verify([_finding("x", rule_id="missing")], [NO_CHECK], [_clause("x")], False, enabled=False)


def test_verify_findings_order_and_length_kept() -> None:
    rules = [_rule(rule_id=f"r{i}") for i in range(5)]
    findings = [_finding(f"metin {i}", rule_id=f"r{i}") for i in range(5)]
    clause = " ".join(f"metin {i}" for i in range(5))
    out = verify(findings, rules, [_clause(clause)], False)
    assert [f.rule_id for f in out] == [f"r{i}" for i in range(5)]
    assert verify([], rules, [], True) == []


def test_verify_hours_next_to_days_ambiguous() -> None:
    quote = "30 gün önceden, acil hallerde 48 saat önceden bildirilir"
    assert _reasons(_finding(quote), AT_LEAST_30_DAYS) == ["ambiguous_value"]


# --- Values written in the clause outside the quote ----------------------------------------------

AT_MOST_45_DAYS = _rule("<=", 45, "gün")


@pytest.mark.parametrize(
    ("clause", "quote", "rule", "expected"),
    [
        (
            "Ödeme 30 gün içinde, gecikme halinde 90 güne kadar yapılır.",
            "30 gün içinde",
            AT_MOST_60_DAYS,
            ["numeric_mismatch"],
        ),
        (
            "Satıcı 30 gün içinde teslim eder. Alıcı 30 gün içinde öder, "
            "aksi halde 5 gün içinde fesheder.",
            "30 gün içinde",
            AT_LEAST_30_DAYS,
            ["numeric_mismatch"],
        ),
        # The breaking value stands before the quote.
        (
            "Alıcı 5 gün önceden, Satıcı 30 gün önceden bildirir.",
            "Satıcı 30 gün önceden",
            AT_LEAST_30_DAYS,
            ["numeric_mismatch"],
        ),
        (
            "Satıcı 30 gün önceden, Alıcı ise 5 iş günü önceden bildirir.",
            "Satıcı 30 gün önceden",
            AT_LEAST_30_DAYS,
            ["ambiguous_value"],
        ),
        (
            "Satıcı 45 gün önceden, Alıcı ise 1 ay önceden bildirir.",
            "Satıcı 45 gün önceden",
            AT_LEAST_30_DAYS,
            ["unit_conversion_uncertain"],
        ),
        # Unreadable and breaking together: ambiguous_value only.
        (
            "Satıcı 30 gün önceden, Alıcı 5 gün ya da 2 iş günü önceden bildirir.",
            "Satıcı 30 gün önceden",
            AT_LEAST_30_DAYS,
            ["ambiguous_value"],
        ),
        # A value that satisfies the rule changes nothing.
        (
            "Ödeme vadesi 45 gün olup itiraz süresi 10 gündür.",
            "Ödeme vadesi 45 gün",
            AT_MOST_45_DAYS,
            [],
        ),
        # Another kind's value outside the quote is not looked at.
        (
            "Bildirim 30 gün önceden yapılır; gecikme cezası %5 ve 100 TL olur.",
            "Bildirim 30 gün önceden",
            AT_LEAST_30_DAYS,
            [],
        ),
    ],
)
def test_verify_compliant_value_outside_quote(
    clause: str, quote: str, rule: Rule, expected: list[str]
) -> None:
    assert _reasons(_finding(quote), rule, clause) == expected


# The clause is read as a whole: a number that takes its unit from a connector is seen although
# the neighbour that gives it the unit stands inside the quote.
@pytest.mark.parametrize(
    ("clause", "quote", "rule", "expected"),
    [
        (
            "Taraflar sözleşmeyi 15-30 gün önceden yazılı bildirimle feshedebilir.",
            "30 gün önceden yazılı bildirimle feshedebilir",
            AT_LEAST_30_DAYS,
            ["numeric_mismatch"],
        ),
        (
            "Taraflar sözleşmeyi 15 ila 30 gün önceden yazılı bildirimle feshedebilir.",
            "30 gün önceden yazılı bildirimle feshedebilir",
            AT_LEAST_30_DAYS,
            ["numeric_mismatch"],
        ),
        (
            "Taraflar sözleşmeyi 15 veya 30 gün önceden yazılı bildirimle feshedebilir.",
            "30 gün önceden yazılı bildirimle feshedebilir",
            AT_LEAST_30_DAYS,
            ["numeric_mismatch"],
        ),
        # The unit-less number stands after the quote.
        (
            "Fesih bildirimi 30 gün veya 15 önceden yapılır.",
            "Fesih bildirimi 30 gün",
            AT_LEAST_30_DAYS,
            ["numeric_mismatch"],
        ),
        (
            "Ödeme 45 gün veya 90 içinde yapılır.",
            "Ödeme 45 gün",
            AT_MOST_45_DAYS,
            ["numeric_mismatch"],
        ),
        (
            "Gecikme cezası sözleşme bedelinin %10-25 arasındadır.",
            "Gecikme cezası sözleşme bedelinin %10",
            _rule("<=", 10, "%"),
            ["numeric_mismatch"],
        ),
        (
            "Gecikme cezası sözleşme bedelinin %10 ila 25 arasındadır.",
            "Gecikme cezası sözleşme bedelinin %10",
            _rule("<=", 10, "%"),
            ["numeric_mismatch"],
        ),
        (
            "Teminat 500.000 ila 1.000.000 TL arasındadır.",
            "1.000.000 TL arasındadır",
            _rule(">=", 1_000_000, "TL"),
            ["numeric_mismatch"],
        ),
        # The linked expression cannot be read.
        (
            "Fesih bildirimi 30 gün veya 15 bin önceden yapılır.",
            "Fesih bildirimi 30 gün",
            AT_LEAST_30_DAYS,
            ["ambiguous_value"],
        ),
        # The conversion of the linked value is not exact.
        (
            "Bildirim 1 veya 2 ay önceden, en az 45 gün önceden yapılır.",
            "en az 45 gün önceden yapılır",
            AT_LEAST_30_DAYS,
            ["unit_conversion_uncertain"],
        ),
        # A unit-less number inside the quote takes its unit from outside it.
        (
            "Bildirim 30 gün, acil halde 10 veya 45 gün önceden yapılır.",
            "Bildirim 30 gün, acil halde 10",
            AT_LEAST_30_DAYS,
            ["numeric_mismatch"],
        ),
        # A linked range that satisfies the rule changes nothing.
        (
            "Taraflar sözleşmeyi 45-60 gün önceden yazılı bildirimle feshedebilir.",
            "60 gün önceden yazılı bildirimle feshedebilir",
            AT_LEAST_30_DAYS,
            [],
        ),
        (
            "Ödeme 30 ila 45 gün içinde yapılır.",
            "45 gün içinde yapılır",
            AT_MOST_45_DAYS,
            [],
        ),
    ],
)
def test_verify_compliant_linked_value_outside_quote(
    clause: str, quote: str, rule: Rule, expected: list[str]
) -> None:
    assert _reasons(_finding(quote), rule, clause) == expected


def test_verify_non_compliant_linked_value_outside_quote_not_checked() -> None:
    clause = "Taraflar sözleşmeyi 15-30 gün önceden yazılı bildirimle feshedebilir."
    finding = _finding(
        "30 gün önceden yazılı bildirimle feshedebilir",
        status="non_compliant",
        proposal=_proposal("45 gün"),
    )
    # The quote's own value satisfies the rule; nothing outside it is looked at.
    assert _reasons(finding, AT_LEAST_30_DAYS, clause) == ["numeric_mismatch"]


def test_verify_compliant_narrow_quote_never_hides_a_clause_value_seeded() -> None:
    """Whatever the quote, a `compliant` finding whose quote matched is queued when the cited
    clause, read as a whole, holds a value of the rule's kind that breaks the rule, converts
    inexactly or cannot be read."""
    rng = random.Random(20261113)
    cases: list[tuple[Rule, list[str]]] = [
        (
            AT_LEAST_30_DAYS,
            ["15", "30", "45", "60", "otuz", "on beş", "gün", "gün", "günlük", "ay", "hafta",
             "ila", "ile", "veya", "-", "(", ")", ",", ".", "takvim", "iş", "içinde", "süre",
             "Ayrıca", "bin", "buçuk", "önceden"],
        ),
        (
            _rule("<=", 10, "%"),
            ["%", "yüzde", "5", "10", "15", "25", "on", "beş", "ila", "ile", "veya", "-", "(",
             ")", ",", ".", "oranında", "ceza", "Ayrıca", "buçuk"],
        ),
        (
            _rule(">=", 1_000_000, "TL"),
            ["500.000", "1.000.000", "2.500.000", "TL", "TL", "₺", "lira", "ila", "ile", "veya",
             "-", "(", ")", ",", ".", "bedel", "milyon", "USD", "Türk", "Ayrıca"],
        ),
    ]  # fmt: skip
    matched = queued_by_outside = 0
    for rule, vocab in cases:
        assert rule.check is not None
        kind: Kind = {"gün": "duration", "%": "percent", "TL": "amount"}[rule.check.unit]  # type: ignore[assignment]
        for _ in range(250):
            text = " ".join(rng.choice(vocab) for _ in range(rng.randint(3, 7)))
            text = text.replace(" ,", ",").replace(" .", ".").replace("( ", "(")
            text = text.replace(" )", ")")
            whole = extract(text, kind)
            bad = bool(whole.unreadable) or any(
                verify_module._evaluate([value], rule.check) is not True for value in whole.values
            )
            words = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
            for i, (start, _) in enumerate(words):
                for _, end in words[i:]:
                    result = _run(_finding(text[start:end]), rule, text)
                    if "quote_not_found" in result.review_reasons:
                        continue
                    matched += 1
                    if bad:
                        assert result.needs_review, (text, text[start:end])
                        queued_by_outside += 1
    # The generator must reach both branches, or the test proves nothing.
    assert matched > 2000
    assert queued_by_outside > 500


def test_verify_non_compliant_outside_quote_not_checked() -> None:
    clause = "Satıcı 15 gün önceden, Alıcı 5 gün önceden bildirir."
    finding = _finding(
        "Satıcı 15 gün önceden", status="non_compliant", proposal=_proposal("30 gün")
    )
    assert _reasons(finding, AT_LEAST_30_DAYS, clause) == []


def test_verify_outside_quote_not_checked_when_disabled() -> None:
    clause = "Ödeme 30 gün içinde, gecikme halinde 90 güne kadar yapılır."
    out = verify([_finding("30 gün içinde")], [AT_MOST_60_DAYS], [_clause(clause)], False, False)
    assert out[0].review_reasons == []


# --- A masked number in the clause (masked_value_in_clause) --------------------------------------
#
# The masker can mistake a number that is not personal data for a phone number or a TCKN; the
# placeholder is one word, never a number, so the value is not read. A `compliant` finding whose
# clause holds a TCKN or TELEFON placeholder goes to the lawyer. The masked clause is written with
# the placeholder directly here, so these tests do not depend on the masker.

LIABILITY_CAP = _rule()  # no fixed threshold
INSURANCE_MINIMUM = _rule(">=", 1_000_000, "TL")
PAYMENT_TERM = AT_MOST_45_DAYS
MASKED = "masked_value_in_clause"


@pytest.mark.parametrize(
    ("clause", "quote", "rule", "expected"),
    [
        pytest.param(
            "Tedarikçinin toplam sorumluluğu [TELEFON_1] TL ile sınırlıdır.",
            None,
            LIABILITY_CAP,
            [MASKED],
            id="1-liability-no-threshold",
        ),
        pytest.param(
            "Tedarikçi, [TELEFON_1] TL teminatlı mesleki sorumluluk sigortası yaptırır.",
            None,
            INSURANCE_MINIMUM,
            ["numeric_mismatch", MASKED],
            id="2-insurance-no-value-read",
        ),
        pytest.param(
            "Alıcı faturayı 30 gün içinde, gecikme halinde [TCKN_1] güne kadar öder.",
            None,
            PAYMENT_TERM,
            [MASKED],
            id="3-payment-masked-in-quote",
        ),
        pytest.param(
            "Alıcı faturayı 30 gün içinde öder, gecikme halinde 90 güne kadar öder.",
            "Alıcı faturayı 30 gün içinde öder",
            PAYMENT_TERM,
            ["numeric_mismatch"],
            id="4-payment-unmasked-outside-quote",
        ),
        pytest.param(
            "Alıcı faturayı 30 gün içinde öder, gecikme halinde [TCKN_1] güne kadar öder.",
            "Alıcı faturayı 30 gün içinde öder",
            PAYMENT_TERM,
            [MASKED],
            id="5-payment-masked-outside-quote",
        ),
        pytest.param(
            "Tedarikçi 2.000.000 TL teminatlı sigorta yaptırır. "
            "Alt yükleniciler için teminat [TELEFON_1] TL olur.",
            "Tedarikçi 2.000.000 TL teminatlı sigorta yaptırır.",
            INSURANCE_MINIMUM,
            [MASKED],
            id="6-insurance-masked-second-sentence",
        ),
        pytest.param(
            "Tedarikçinin toplam sorumluluğu sözleşme bedeli ile sınırlıdır. "
            "Bu sınır hiçbir halde [TELEFON_1] TL tutarını aşamaz.",
            "Tedarikçinin toplam sorumluluğu sözleşme bedeli ile sınırlıdır.",
            LIABILITY_CAP,
            [MASKED],
            id="7-liability-masked-second-sentence",
        ),
    ],
)
def test_verify_compliant_masked_number_in_clause_queued(
    clause: str, quote: str | None, rule: Rule, expected: list[str]
) -> None:
    result = _run(_finding(quote if quote is not None else clause), rule, clause)
    assert result.review_reasons == expected
    assert result.needs_review is True


def test_verify_masked_number_in_another_clause_not_queued() -> None:
    # Case 8, a known limit: only the clause the finding points to is looked at (as for unmasked
    # values). The masked value in c2 does not reach a finding that points to c1.
    shown = "Tedarikçinin toplam sorumluluğu 1.000.000 TL ile sınırlıdır."
    other = "Alt yükleniciler için sorumluluk [TELEFON_1] TL olur."
    clauses = [_clause(shown, "c1"), _clause(other, "c2")]
    (result,) = verify([_finding(shown)], [LIABILITY_CAP], clauses, False)
    assert result.review_reasons == []
    assert result.needs_review is False


def test_verify_masked_number_reason_after_numeric_reasons_before_injection() -> None:
    clause = "Tedarikçinin toplam sorumluluğu [TELEFON_1] TL ile sınırlıdır."
    result = _run(_finding(clause), LIABILITY_CAP, clause, injection=True)
    assert result.review_reasons == [MASKED, "injection_suspected"]


@pytest.mark.parametrize("placeholder", ["[IBAN_1]", "[KISI_1]", "[EPOSTA_1]"])
def test_verify_other_placeholder_kinds_not_masked_number(placeholder: str) -> None:
    clause = f"Tedarikçinin toplam sorumluluğu {placeholder} adına sınırlıdır."
    assert _reasons(_finding(clause), LIABILITY_CAP, clause) == []


@pytest.mark.parametrize("lookalike", ["[TCKN]", "TCKN_1", "[TELEFON_x]", "[tckn_1]"])
def test_verify_placeholder_lookalike_not_masked_number(lookalike: str) -> None:
    clause = f"Tedarikçinin toplam sorumluluğu {lookalike} TL ile sınırlıdır."
    assert _reasons(_finding(clause), LIABILITY_CAP, clause) == []


def test_verify_non_compliant_masked_number_not_signalled() -> None:
    clause = "Tedarikçinin toplam sorumluluğu [TELEFON_1] TL ile sınırlıdır."
    finding = _finding(clause, status="non_compliant", proposal=_proposal("Sınır kaldırılır."))
    assert _reasons(finding, LIABILITY_CAP, clause) == []


def test_verify_masked_number_not_signalled_when_disabled() -> None:
    clause = "Tedarikçinin toplam sorumluluğu [TELEFON_1] TL ile sınırlıdır."
    result = _run(_finding(clause), LIABILITY_CAP, clause, enabled=False)
    assert result.review_reasons == []
    assert result.needs_review is False


def test_verify_masked_number_unknown_clause_id_no_signal_no_error() -> None:
    clause = "Tedarikçinin toplam sorumluluğu [TELEFON_1] TL ile sınırlıdır."
    finding = _finding(clause, clause_id="c9")
    assert _reasons(finding, LIABILITY_CAP, clause) == ["quote_not_found"]


def test_verify_masked_number_signalled_when_quote_not_found() -> None:
    # The whole clause is looked at whether or not the quote matched
    # (agent_design.md, 'Maskelenmiş sayı').
    clause = "Tedarikçinin toplam sorumluluğu [TELEFON_1] TL ile sınırlıdır."
    result = _run(_finding("uydurma alıntı"), LIABILITY_CAP, clause)
    assert result.review_reasons == ["quote_not_found", MASKED]
    assert result.needs_review is True


def test_verify_masked_number_not_signalled_for_not_found_with_clause_id() -> None:
    # A not_found finding may carry a clause_id; the code does not treat it as evidence.
    clause = "Tedarikçinin toplam sorumluluğu [TELEFON_1] TL ile sınırlıdır."
    result = _run(_finding(clause, status="not_found"), LIABILITY_CAP, clause)
    assert MASKED not in result.review_reasons
    assert result.review_reasons == ["unverified_not_found"]


@pytest.mark.parametrize(
    ("raw", "placeholder", "rule"),
    [
        # Step 17b's two documented false positives, through the real masker.
        (
            "Tedarikçinin toplam sorumluluğu 500 000 00 00 TL ile sınırlıdır.",
            "[TELEFON_1]",
            LIABILITY_CAP,
        ),
        (
            "Alıcı faturayı 30 gün içinde, gecikme halinde 405 11 05 2020 güne kadar öder.",
            "[TCKN_1]",
            PAYMENT_TERM,
        ),
    ],
    ids=["phone-shaped-amount", "tckn-shaped-numbers"],
)
def test_verify_after_real_mask_false_positive_queued(
    raw: str, placeholder: str, rule: Rule
) -> None:
    masked, mapping = mask(raw, {})
    assert placeholder in masked
    assert list(mapping) == [placeholder]
    result = _run(_finding(masked), rule, masked)
    assert result.review_reasons == [MASKED]


# --- A unit never crosses a sentence boundary -----------------------------------------------------

_SENTENCE_MARKS = ".!?"
PRICE_CAP = _rule("<=", 15, "%")
AT_MOST_100K_TL = _rule("<=", 100000, "TL")
AT_LEAST_20_DAYS = _rule(">=", 20, "gün")
# Written from the definition (agent_design.md §5), not from the code: a mark, whitespace, then a
# word upper case first and lower case next.
_BOUNDARY_RE = re.compile(r"[.!?]\s+(?=[^\W\d_]{2})")


def _has_boundary(text: str) -> bool:
    return any(
        text[m.end()].isupper() and text[m.end() + 1].islower() for m in _BOUNDARY_RE.finditer(text)
    )


def _without_boundaries(text: str, kind: Kind) -> Extraction:
    """The reader with the sentence-boundary check switched off."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(verify_module, "_sentence_start", lambda norm, tok: False)
        return extract(text, kind)


def _contract_4_clause_5() -> str:
    path = Path(__file__).resolve().parents[1] / "evals" / "dataset" / "contracts"
    lines = (path / "04_zor_yazim.md").read_text(encoding="utf-8").splitlines()
    start = lines.index("## Madde 5 - Fiyat Güncellemesi")
    return next(line for line in lines[start + 1 :] if line.strip())


def _span_texts(text: str, kind: Kind) -> list[str]:
    norm = normalize(text, ALL_STEPS).text
    return [norm[start:end] for start, end in extract(text, kind).spans]


@pytest.mark.parametrize("mark", list(_SENTENCE_MARKS))
@pytest.mark.parametrize(
    ("template", "quote"),
    [
        ("Fiyat 5{m} Ayrıca X.", "Fiyat 5{m}"),
        ("Fiyat 5{m} Güncel.", "Fiyat 5{m}"),
        ("Fiyat beş{m} Güncel.", "Fiyat beş{m}"),
        ("Fiyat %5 olur{m} Güncel.", "Fiyat %5 olur{m}"),
        ("Fiyat %5 olur{m} Güncel.", "%5"),
        ("Fiyat yüzde beşi (%5) geçemez{m} Ayrıca X.", "Fiyat yüzde beşi (%5) geçemez{m}"),
    ],
)
def test_verify_quote_ending_at_sentence_boundary_matches(
    template: str, quote: str, mark: str
) -> None:
    clause = template.format(m=mark)
    quote_text = quote.format(m=mark)
    assert not any(_has_boundary(s) for s in _span_texts(clause, "duration"))
    result = _run(_finding(quote_text), NO_CHECK, clause)
    assert result.review_reasons == []
    assert result.quote == quote_text


@pytest.mark.parametrize("mark", list(_SENTENCE_MARKS))
@pytest.mark.parametrize(
    ("template", "kind"),
    [
        ("Fiyat 5{m} Ayrıca X.", "duration"),
        ("Fiyat 5{m} Güncel.", "duration"),
        ("Fiyat %5 olur{m} Güncel.", "duration"),
        ("beş{m} Güncel", "duration"),
        ("beşer{m} Günlük", "duration"),
        ("Ödeme 30 olur{m} Gün", "duration"),
        ("Ödeme 30{m} Takvim günü", "duration"),
        ("Süre 30{m}\nAyrıca", "duration"),
        ("Bildirim 10{m} Aynı hüküm uygulanır.", "duration"),
        # The group holds the boundary: 5 reads nothing of its own either.
        ("30 (bkz{m} Madde 5) gün", "duration"),
        # Accepted loss: a capitalized real unit word starting the next sentence.
        ("Süre 30{m} Gün hesabı yapılır.", "duration"),
        ("teslimden sonraki 45{m} İş gününde", "duration"),
        ("Bedel 500{m} Liralık", "amount"),
        ("Artış 15{m} Oran% olur", "percent"),
    ],
)
def test_extract_unit_across_sentence_boundary_no_expression(
    template: str, kind: Kind, mark: str
) -> None:
    text = template.format(m=mark)
    assert extract(text, kind) == Extraction((), ())
    assert extract(text, kind).spans == ()


@pytest.mark.parametrize("mark", list(_SENTENCE_MARKS))
@pytest.mark.parametrize(
    ("template", "kind", "expected"),
    [
        # The sentence before ends in a number: the expression starting the new one is not lost.
        ("Madde 4{m} Doksanıncı günde ödeme yapılır.", "duration", ((), ("Doksanıncı günde",))),
        ("Süre 30{m} Beşer gün", "duration", ((), ("Beşer gün",))),
        ("4.2{m} Birinci yılın sonunda", "duration", ((), ("Birinci yılın",))),
        ("Ek-3{m} Beşyüzbin liralık", "amount", ((), ("Beşyüzbin liralık",))),
        ("Süre 30{m} Beş gün içinde ödenir.", "duration", ((_days(5),), ())),
        ("Madde 4{m} Otuz (30) gün içinde", "duration", ((_days(30), _days(30)), ())),
    ],
)
def test_extract_expression_starting_new_sentence_not_swallowed(
    template: str, kind: Kind, expected: tuple[tuple[Quantity, ...], tuple[str, ...]], mark: str
) -> None:
    result = extract(template.format(m=mark), kind)
    assert (result.values, result.unreadable) == expected
    assert not any(_has_boundary(s) for s in _span_texts(template.format(m=mark), kind))


@pytest.mark.parametrize(
    ("text", "kind", "expected"),
    [
        # Amounts written with ",-" / ".-": no sentence boundary.
        ("50.000,- TL", "amount", Extraction((), ("50.000,- TL",))),
        ("500.000.-TL", "amount", Extraction((), ("500.000.-TL",))),
        ("1.500,00.- TL", "amount", Extraction((), ("1.500,00.- TL",))),
        # "." before a lower-case word: an ordinal or an abbreviation, no sentence boundary.
        ("45. iş gününde", "duration", Extraction((), ("45. iş gününde",))),
        ("90. takvim gününde", "duration", Extraction((), ("90. takvim gününde",))),
        ("90. gününü", "duration", Extraction((), ("90. gününü",))),
        ("60. günlerde", "duration", Extraction((), ("60. günlerde",))),
        ("3. yılını", "duration", Extraction((), ("3. yılını",))),
        ("12. ayını", "duration", Extraction((), ("12. ayını",))),
        ("48. saatte", "duration", Extraction((), ("48. saatte",))),
        ("2. senenin", "duration", Extraction((), ("2. senenin",))),
        ("30. gün", "duration", Extraction((), ("30. gün",))),
        ("30.gün", "duration", Extraction((), ("30.gün",))),
        # Other marks are never a boundary.
        ("30, gün", "duration", Extraction((), ("30, gün",))),
        ("30; günü", "duration", Extraction((_days(30),), ())),
        ("30: gün", "duration", Extraction((_days(30),), ())),
        ("30 ; gün", "duration", Extraction((_days(30),), ())),
        ("30 (otuz), gün", "duration", Extraction((_days(30), _days(30)), ())),
        # All upper case, a single capital letter: no boundary, the old reading stands.
        ("FİYAT 5. AYRICA X.", "duration", Extraction((), ("5. AYRICA",))),
        ("Süre 30. A gün", "duration", Extraction((), ("30. A gün",))),
        ("30 gün.", "duration", Extraction((_days(30),), ())),
        ("otuz (30) gün", "duration", Extraction((_days(30), _days(30)), ())),
        ("5 ayrı", "duration", Extraction((), ("5 ayrı",))),
        # Known limit: the stem match stays wide with no boundary (safe-side over-capture).
        ("bir aykırılık", "duration", Extraction((), ("bir aykırılık",))),
        ("bir kez güncellenebilir", "duration", Extraction((), ("bir kez güncellenebilir",))),
        ("bir önceki yılın", "duration", Extraction((), ("bir önceki yılın",))),
        # A sentence starting with a word that is no unit leaves the link as it was.
        ("30 gün veya 15. Bu", "duration", Extraction((_days(30), _days(15)), ())),
    ],
)
def test_extract_no_sentence_boundary_unchanged(
    text: str, kind: Kind, expected: Extraction
) -> None:
    result = extract(text, kind)
    assert result == expected
    assert result.spans == _without_boundaries(text, kind).spans


def test_extract_number_with_unit_across_boundary_not_linked() -> None:
    # Its unit word starts the next sentence: neither read, nor given the other value's unit.
    assert extract("30 gün veya 15. Ayrıca", "duration") == Extraction((_days(30),), ())
    assert extract("30 gün veya 15. Günlük", "duration") == Extraction((_days(30),), ())


@pytest.mark.parametrize(
    ("clause", "quote", "rule", "reasons"),
    [
        (
            "Satıcı gecikme halinde 10.000 TL ceza öder; tekrarında Satıcı 500.000,- TL cezai "
            "şart öder.",
            "Satıcı gecikme halinde 10.000 TL ceza öder",
            AT_MOST_100K_TL,
            ["ambiguous_value"],
        ),
        (
            "Satıcı gecikme halinde 10.000 TL ceza öder; tekrarında 500.000.-TL cezai şart öder.",
            "Satıcı gecikme halinde 10.000 TL ceza öder",
            AT_MOST_100K_TL,
            ["ambiguous_value"],
        ),
        (
            "Fatura 30 gün içinde düzenlenir. Ödeme, teslimden sonraki 90. iş gününde yapılır.",
            "Fatura 30 gün içinde düzenlenir.",
            AT_MOST_60_DAYS,
            ["ambiguous_value"],
        ),
        (
            "Fatura 30 gün içinde düzenlenir. Ödeme, teslimin 90. gününü izleyen hafta yapılır.",
            "Fatura 30 gün içinde düzenlenir.",
            AT_MOST_60_DAYS,
            ["ambiguous_value"],
        ),
        (
            "Fatura 30 gün içinde düzenlenir. Ödeme, teslimden sonraki 90. takvim gününde yapılır.",
            "Fatura 30 gün içinde düzenlenir.",
            AT_MOST_60_DAYS,
            ["ambiguous_value"],
        ),
        (
            "Ödeme 30 gün içinde yapılır; gecikmede 90: gün.",
            "Ödeme 30 gün içinde yapılır",
            AT_MOST_60_DAYS,
            ["numeric_mismatch"],
        ),
        (
            "FİYAT 5. AYRICA X.",
            "FİYAT 5.",
            NO_CHECK,
            ["quote_not_found"],
        ),
    ],
)
def test_verify_value_outside_quote_with_no_boundary_still_queued(
    clause: str, quote: str, rule: Rule, reasons: list[str]
) -> None:
    assert _reasons(_finding(quote), rule, clause) == reasons


def test_verify_unit_less_number_before_new_sentence_not_queued() -> None:
    # "süre 10." has no unit: like any unit-less number, the code does not see it (known limit).
    # "Aynı" starting the next sentence is no unit of it, so nothing unreadable is left over.
    clause = "Bildirim süresi 30 gündür. Kısa fesihte süre 10. Aynı hüküm uygulanır."
    assert _reasons(_finding("Bildirim süresi 30 gündür."), AT_LEAST_20_DAYS, clause) == []


def test_verify_contract_4_clause_5_first_sentence_matches() -> None:
    clause = _contract_4_clause_5()
    first = clause[: clause.index(" Güncellenen")]
    assert first.endswith("(%15) geçemez.")
    assert not any(_has_boundary(s) for s in _span_texts(clause, "duration"))
    for finding in (_finding(first), _finding(first, value=15, unit="%")):
        result = _run(finding, PRICE_CAP, clause)
        assert result.quote == first
        # "yüzde on beşini" is an inflected number word next to the read 15: ambiguous.
        assert result.review_reasons == ["ambiguous_value"]


def _random_text(rng: random.Random) -> str:
    numbers = (
        "5", "30", "15", "45", "1.000", "50.000", "2,5", "1.5", "beş", "otuz", "on iki", "Beş",
        "45.", "90.", "3.", "50.000,-", "500.000.-", "1.500,00.-",
    )  # fmt: skip
    units = (
        "gün", "günü", "günün", "gününü", "günlerde", "ay", "ayını", "yıl", "yılını", "hafta",
        "saat", "saatte", "senenin", "TL", "-TL", "lira", "USD", "₺", "%", "Gün", "GÜN", "Ay",
    )  # fmt: skip
    traps = (
        "Ayrıca", "Aynı", "aynı", "Güncel", "güncellenen", "Yılın", "Günlük", "Bu", "İş",
        "Beşer", "beşer", "AYRICA", "AYNI", "FİYAT", "A", "X",
    )  # fmt: skip
    bridges = ("takvim", "iş", "türk", "Takvim")
    fillers = ("olur", "fiyat", "veya", "ila", "-", "yüzde", "(otuz)", "(30)", "(bkz.", "5)", ")")
    marks = (".", ",", ";", ":", "!", "?", ",-", ".-", "...")
    text = ""
    for k in range(rng.randint(2, 9)):
        roll = rng.random()
        if roll < 0.3:
            piece = rng.choice(numbers)
        elif roll < 0.55:
            piece = rng.choice(units)
        elif roll < 0.72:
            piece = rng.choice(traps)
        elif roll < 0.8:
            piece = rng.choice(bridges)
        else:
            piece = rng.choice(fillers)
        if rng.random() < 0.3:
            piece += rng.choice(("", " ")) + rng.choice(marks)
        text += (rng.choice((" ", " ", " ", "\n", "")) if k else "") + piece
    return text


def test_sentence_boundary_invariants_random() -> None:
    """(i) An expression the reader with the check switched off does not read lies inside one
    of its expressions that crosses a boundary, and starts right after that boundary: the new
    sentence is read on its own. With no such expression, no value and no unreadable expression
    is added. (ii) With no boundary in the text, the two
    readers agree, spans included. (iii) No expression spans a boundary."""
    rng = random.Random(20261112)
    texts = [_random_text(rng) for _ in range(4000)]
    kinds: tuple[Kind, ...] = ("duration", "percent", "amount")
    on = [[extract(t, kind) for kind in kinds] for t in texts]
    off = [[_without_boundaries(t, kind) for kind in kinds] for t in texts]
    seen_boundary = 0
    for text, with_check, without in zip(texts, on, off, strict=True):
        norm = normalize(text, ALL_STEPS).text
        boundary = _has_boundary(norm)
        seen_boundary += boundary
        for kind, a, b in zip(kinds, with_check, without, strict=True):
            for start, end in set(a.spans) - set(b.spans):
                crossing = [
                    (s, e)
                    for s, e in b.spans
                    if s < start and end <= e and _has_boundary(norm[s:e])
                ]
                assert crossing, (text, kind)
                assert norm[start - 2] in ".!?" and norm[start - 1] == " ", (text, kind)
            if set(a.spans) <= set(b.spans):
                assert not Counter(a.values) - Counter(b.values), (text, kind)
                assert not Counter(a.unreadable) - Counter(b.unreadable), (text, kind)
            if not boundary:
                assert (a, a.spans) == (b, b.spans), (text, kind)
            for start, end in a.spans:
                assert not _has_boundary(norm[start:end]), (text, kind)
    assert seen_boundary > 300  # the generator does reach the case under test
