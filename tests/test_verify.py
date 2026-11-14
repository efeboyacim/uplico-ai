import itertools
import random
import re
from collections import Counter
from pathlib import Path
from typing import get_args

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
    ReviewReason,
    Rule,
    Severity,
)
from src.prepare.pii import mask
from src.store import Store
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


@pytest.mark.parametrize("unit", ["takvim günü", "Takvim Günü", "TAKVİM GÜNÜ", "takvim  günü"])
def test_verify_model_unit_bridge_word_and_duration_form_read(unit: str) -> None:
    assert _reasons(_finding("30 gün önceden", value=30, unit=unit), AT_LEAST_30_DAYS) == []


def test_verify_model_unit_bridge_word_matches_quote_and_other_units() -> None:
    finding = _finding("30 takvim günü önceden", value=30, unit="takvim günü")
    assert _reasons(finding, AT_LEAST_30_DAYS) == []
    # Same as unit="ay": 30 days lie inside one month (28-31 days).
    finding = _finding("30 gün önceden", value=1, unit="takvim ayı")
    assert _reasons(finding, AT_LEAST_30_DAYS) == []
    finding = _finding("14 gün önceden", value=14, unit="takvim gün")
    assert _reasons(finding, _rule(">=", 2, "hafta")) == []


@pytest.mark.parametrize(
    ("quote", "value"),
    [("30 ay önceden", 30), ("30 gün önceden", 45)],
)
def test_verify_model_unit_bridge_word_value_differs_numeric_mismatch(
    quote: str, value: float
) -> None:
    finding = _finding(quote, value=value, unit="takvim günü")
    assert _reasons(finding, AT_LEAST_30_DAYS) == ["numeric_mismatch"]


@pytest.mark.parametrize(
    "unit",
    [
        "iş günü",
        "İş Günü",
        "takvim iş günü",
        "işgünü",
        "takvim saat",
        "takvim sene",
        "takvim",
        "takvim günü.",
        "gün takvim",
    ],
)
def test_verify_model_unit_not_bridge_word_and_form_numeric_mismatch(unit: str) -> None:
    finding = _finding("30 gün önceden", value=30, unit=unit)
    assert _reasons(finding, AT_LEAST_30_DAYS) == ["numeric_mismatch"]


@pytest.mark.parametrize(
    ("quote", "rule"),
    [("%30 oranında", _rule("<=", 30, "%")), ("30 TL ceza", _rule("<=", 30, "TL"))],
)
def test_verify_model_unit_bridge_word_in_other_kind_numeric_mismatch(
    quote: str, rule: Rule
) -> None:
    finding = _finding(quote, value=30, unit="takvim günü")
    assert _reasons(finding, rule) == ["numeric_mismatch"]


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


@pytest.mark.parametrize(
    "text",
    [
        "bir önceki yılın TÜFE oranında",
        "Bir önceki ay",
        "bir evvelki ayın faturası",
        "bir önceki günü",
        "bir önceki saat",
    ],
)
def test_extract_bir_onceki_past_period_not_a_value(text: str) -> None:
    for hours in (False, True):
        assert extract(text, "duration", hours=hours) == Extraction((), ())


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 önceki yıl", Extraction((), ("1 önceki yıl",))),
        ("iki önceki yılın", Extraction((), ("iki önceki yılın",))),
        ("yirmi bir önceki yıl", Extraction((), ("yirmi bir önceki yıl",))),
        ("bir sonraki gün içinde", Extraction((), ("bir sonraki gün",))),
        ("bir başka gün", Extraction((), ("bir başka gün",))),
        ("bir iş günü", Extraction((), ("bir iş günü",))),
        ("30 gün veya bir önceki yıl", Extraction((_days(30),), ("bir önceki yıl",))),
        ("(bir önceki yılın)", Extraction((), ("bir önceki yılın",))),
        ("bir yılın sonunda", Extraction((Quantity(1, "yıl"),), ())),
        ("bir takvim yılı", Extraction((Quantity(1, "yıl"),), ())),
    ],
)
def test_extract_bir_onceki_rule_stays_narrow(text: str, expected: Extraction) -> None:
    assert extract(text, "duration") == expected


AT_LEAST_45_DAYS = _rule(">=", 45, "gün")


@pytest.mark.parametrize(
    ("tail", "code_check"),
    [
        ("Her yenileme döneminde bir önceki yılın TÜFE oranında artış uygulanır.", "passed"),
        ("İtiraz bir sonraki gün içinde bildirilir.", "failed"),
    ],
)
def test_verify_bir_onceki_in_draft_proposal_check(tail: str, code_check: str) -> None:
    draft = f"Ödeme en az 45 (kırk beş) takvim günü içerisinde yapılır. {tail}"
    finding = _finding(
        "Ödeme 30 gün içinde yapılır.", status="non_compliant", proposal=_proposal(draft)
    )
    result = _run(finding, AT_LEAST_45_DAYS)
    assert result.proposal is not None
    assert result.proposal.code_check == code_check


def test_verify_bir_onceki_in_compliant_clause_not_ambiguous() -> None:
    clause = "Ödeme 45 gün içinde yapılır. Fiyata bir önceki yılın TÜFE oranında artış uygulanır."
    finding = _finding("Ödeme 45 gün içinde yapılır.", value=45, unit="gün")
    assert _reasons(finding, AT_LEAST_45_DAYS, clause) == []


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


# --- An amount restated in words in parentheses ---------------------------------------------------

_WORD_ONES = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_WORD_TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]


def _turkish_words(n: int) -> str:
    """Independent reference writer: 1 <= n < 10**12 in canonical Turkish words."""
    if n >= 10**9:
        head, rest = divmod(n, 10**9)
        return " ".join(
            filter(None, [_turkish_words(head), "milyar", _turkish_words_or_empty(rest)])
        )
    if n >= 10**6:
        head, rest = divmod(n, 10**6)
        return " ".join(
            filter(None, [_turkish_words(head), "milyon", _turkish_words_or_empty(rest)])
        )
    if n >= 1000:
        head, rest = divmod(n, 1000)
        thousands = "bin" if head == 1 else f"{_turkish_words(head)} bin"
        return " ".join(filter(None, [thousands, _turkish_words_or_empty(rest)]))
    hundreds, rest = divmod(n, 100)
    tens, ones = divmod(rest, 10)
    out = []
    if hundreds:
        out.append("yüz" if hundreds == 1 else f"{_WORD_ONES[hundreds]} yüz")
    out += [_WORD_TENS[tens], _WORD_ONES[ones]]
    return " ".join(w for w in out if w)


def _turkish_words_or_empty(n: int) -> str:
    return _turkish_words(n) if n else ""


def _tl_digits(n: int) -> str:
    return f"{n:,}".replace(",", ".")


AT_MOST_1_2M_TL = _rule("<=", 1_200_000, "TL")


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("2.400.000 TL (iki milyon dört yüz bin Türk lirası)", 2_400_000),
        ("1.200.000 TL (bir milyon iki yüz bin TL)", 1_200_000),
        ("1.200.000 TL (bir milyon iki yüz bin ₺)", 1_200_000),
        ("1.200.000 TL (bir milyon iki yüz bin lira)", 1_200_000),
        ("1.200.000 (bir milyon iki yüz bin) TL", 1_200_000),
        ("bir milyon iki yüz bin (1.200.000) TL", 1_200_000),
        ("₺1.200.000 (bir milyon iki yüz bin)", 1_200_000),
        ("TL 1.200.000 (bir milyon iki yüz bin)", 1_200_000),
        ("₺1.200.000 (bir milyon iki yüz bin TL)", 1_200_000),
        ("1.200.000,00 TL (bir milyon iki yüz bin TL)", 1_200_000),
        ("500 TL (beş yüz Türk lirası)", 500),
        ("1.000 (bin) TL", 1000),
        ("₺1.000 (bin)", 1000),
        ("1.000 TL (bin TL)", 1000),
        ("1.001 TL (bin bir TL)", 1001),
        ("100.000 TL (yüz bin TL)", 100_000),
        ("101.000 TL (yüz bir bin TL)", 101_000),
        ("1.500 TL (bin beş yüz TL)", 1500),
        ("1.000.000 TL (bir milyon TL)", 1_000_000),
        ("5.000.000.000 TL (beş milyar TL)", 5_000_000_000),
        ("2.400.000 TL (İKİ MİLYON DÖRT YÜZ BİN TÜRK LİRASI)", 2_400_000),
    ],
)
def test_extract_amount_restated_in_words_single_value(text: str, value: float) -> None:
    result = extract(f"Bedel {text} olarak ödenir.", "amount")
    assert result == Extraction((Quantity(value, "TL"), Quantity(value, "TL")), ())
    assert verify_module._single(result)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Words that say another amount.
        (
            "2.400.000 TL (iki milyon beş yüz bin Türk lirası)",
            Extraction((Quantity(2_400_000, "TL"),), ("iki milyon beş yüz bin Türk lirası",)),
        ),
        (
            "1.200.000 TL (bir milyon üç yüz bin TL)",
            Extraction((Quantity(1_200_000, "TL"),), ("bir milyon üç yüz bin TL",)),
        ),
        (
            "1.200.001 (bir milyon iki yüz bin) TL",
            Extraction((), ("1.200.001 (bir milyon iki yüz bin) TL",)),
        ),
        (
            "₺1.199.999 (bir milyon iki yüz bin)",
            Extraction((), ("₺1.199.999 (bir milyon iki yüz bin)",)),
        ),
        ("1.100 TL (yüz bin TL)", Extraction((Quantity(1100, "TL"),), ("yüz bin TL",))),
        ("500,50 TL (beş yüz TL)", Extraction((Quantity(500.5, "TL"),), ("beş yüz TL",))),
        # "bin TL" alone in the parentheses would say 1000: the words are the whole group or none.
        ("1.000 TL (iki milyon bin TL)", Extraction((Quantity(1000, "TL"),), ("bin TL",))),
        ("2.001.000 TL (iki milyon bin TL)", Extraction((Quantity(2_001_000, "TL"),), ("bin TL",))),
        # Not canonical, not the grammar's.
        ("1.000 TL (bir bin TL)", Extraction((Quantity(1000, "TL"),), ("bir bin TL",))),
        ("500 TL (beşyüz TL)", Extraction((Quantity(500, "TL"),), ("beşyüz TL",))),
        (
            "2.400.000 TL (ikimilyondörtyüzbin TL)",
            Extraction((Quantity(2_400_000, "TL"),), ("ikimilyondörtyüzbin TL",)),
        ),
        (
            "1.000.000.000.000 TL (bir trilyon TL)",
            Extraction((Quantity(10**12, "TL"),), ("bir trilyon TL",)),
        ),
        # Not right next to the digits, or not about TL.
        (
            "2.400.000 TL, (iki milyon dört yüz bin TL)",
            Extraction((Quantity(2_400_000, "TL"),), ("iki milyon dört yüz bin TL",)),
        ),
        (
            "2.400.000 USD (iki milyon dört yüz bin TL)",
            Extraction((), ("2.400.000 USD", "iki milyon dört yüz bin TL")),
        ),
        (
            "1,2 milyon TL (bir milyon iki yüz bin TL)",
            Extraction((), ("1,2 milyon TL", "bir milyon iki yüz bin TL")),
        ),
        (
            "bir milyon iki yüz bin TL (1.200.000 TL)",
            Extraction((Quantity(1_200_000, "TL"),), ("bir milyon iki yüz bin TL",)),
        ),
        # A scale word then "bin" is two number tokens ("milyar" ends the first): the parentheses
        # hold no single amount, as before (known limit, on the safe side).
        (
            "1.000.001.000 TL (bir milyar bin TL)",
            Extraction((Quantity(1_000_001_000, "TL"),), ("bin TL",)),
        ),
        # An amount in words alone is still never read.
        ("beş yüz TL", Extraction((), ("beş yüz TL",))),
        ("yalnız iki milyon dört yüz bin TL", Extraction((), ("iki milyon dört yüz bin TL",))),
    ],
)
def test_extract_amount_words_not_restating_digits_unreadable(
    text: str, expected: Extraction
) -> None:
    assert extract(f"Bedel {text} olarak.", "amount") == expected


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("bin (1000) gün", "duration"),
        ("1000 (bin) gün", "duration"),
        ("2.400.000 (iki milyon dört yüz bin) gün", "duration"),
        ("%1.000 (bin)", "percent"),
    ],
)
def test_extract_restated_words_other_kinds_unreadable(text: str, kind: Kind) -> None:
    result = extract(f"Süre {text} olarak.", kind)
    assert result.unreadable
    assert not verify_module._single(result)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A parenthesis that never closes: the words are no restatement.
        (
            "2.400.000 TL (iki milyon dört yüz bin TL",
            Extraction((Quantity(2_400_000, "TL"),), ("iki milyon dört yüz bin TL",)),
        ),
        # A sentence boundary inside the parentheses: the unit after it belongs to no number.
        (
            "2.400.000 TL (iki milyon dört yüz bin. Türk lirası)",
            Extraction((Quantity(2_400_000, "TL"),), ()),
        ),
        # A chain: only the parentheses right after the read one-number expression restate it.
        (
            "1.000 TL (bin TL) (bin TL)",
            Extraction((Quantity(1000, "TL"), Quantity(1000, "TL")), ("bin TL",)),
        ),
        # A two-number expression is no one-number amount to restate.
        (
            "₺1.000 (5.000) (bin TL)",
            Extraction((Quantity(1000, "TL"), Quantity(5000, "TL")), ("bin TL",)),
        ),
        # No unit inside the parentheses: "bin" takes its unit from the connector; no restatement.
        (
            "1.000 TL (bin) ila 2.000 TL",
            Extraction((Quantity(1000, "TL"), Quantity(2000, "TL")), ("bin",)),
        ),
    ],
)
def test_extract_amount_words_restatement_edges(text: str, expected: Extraction) -> None:
    assert extract(text, "amount") == expected


def test_extract_amount_restatement_gives_unit_to_linked_neighbour() -> None:
    # The restatement is read, so the connector rule gives its unit to the unit-less 2.000, as it
    # does after "500 TL ila 2.000"; before, the whole expression was unreadable and 2.000 had no
    # unit (the cascade is accepted; recorded in plan step 19e, "Gerçekleşen").
    assert extract("500 (beş yüz) TL ila 2.000", "amount") == Extraction(
        (Quantity(500, "TL"), Quantity(500, "TL"), Quantity(2000, "TL")), ()
    )
    assert extract("500 TL ila 2.000", "amount").values == (
        Quantity(500, "TL"),
        Quantity(2000, "TL"),
    )


def test_verify_amount_restated_in_words_non_compliant_no_review() -> None:
    clause = "Toplam sorumluluk 2.400.000 TL (iki milyon dört yüz bin Türk lirası) ile sınırlıdır."
    draft = "Toplam sorumluluk 1.200.000 TL (bir milyon iki yüz bin Türk lirası) ile sınırlıdır."
    finding = _finding(clause, status="non_compliant", proposal=_proposal(draft))
    result = _run(finding, AT_MOST_1_2M_TL)
    assert result.review_reasons == []
    assert result.proposal is not None
    assert result.proposal.code_check == "passed"


def test_verify_amount_restated_in_words_wrong_compliant_numeric_mismatch() -> None:
    quote = "Toplam sorumluluk 2.400.000 TL (iki milyon dört yüz bin Türk lirası) ile sınırlıdır."
    assert _reasons(_finding(quote), AT_MOST_1_2M_TL) == ["numeric_mismatch"]


@pytest.mark.parametrize(
    ("draft", "rule"),
    [
        (
            "Sorumluluk 1.200.000 TL (bir milyon üç yüz bin Türk lirası) ile sınırlıdır.",
            AT_MOST_1_2M_TL,
        ),
        (
            "Sorumluluk 1.200.000 TL (bir milyon yüz bin Türk lirası) ile sınırlıdır.",
            AT_MOST_1_2M_TL,
        ),
        (
            "Sorumluluk 1.300.000 TL (bir milyon üç yüz bin Türk lirası) ile sınırlıdır.",
            AT_MOST_1_2M_TL,
        ),
        ("Sorumluluk 1.000 TL (iki milyon bin TL) ile sınırlıdır.", AT_MOST_1_2M_TL),
        ("Teminat 2.000.000 TL (üç milyon TL) olarak verilir.", _rule(">=", 1_000_000, "TL")),
        ("Teminat 500,50 TL (beş yüz TL) olarak verilir.", _rule("<=", 1000, "TL")),
    ],
)
def test_verify_proposal_amount_words_differ_failed(draft: str, rule: Rule) -> None:
    finding = _finding("x", status="non_compliant", proposal=_proposal(draft))
    result = _run(finding, rule, "x")
    assert result.proposal is not None
    assert result.proposal.code_check == "failed"
    assert "proposal_noncompliant" in result.review_reasons


def _amount_word_sequences(rng: random.Random, count: int) -> list[str]:
    vocabulary = [*_WORD_ONES[1:], *_WORD_TENS[1:], "yüz", "bin", "milyon", "milyar", "trilyon"]
    return [
        " ".join(rng.choice(vocabulary) for _ in range(rng.randint(1, 9))) for _ in range(count)
    ]


def test_amount_words_reads_every_canonical_amount_seeded() -> None:
    rng = random.Random(19_004)
    numbers = [rng.randint(1, 10**12 - 1) for _ in range(4000)]
    numbers += [rng.randint(1, 10**k) for k in range(1, 12) for _ in range(100)]
    numbers += [1, 10, 100, 1000, 1001, 100_000, 1_000_000, 1_001_000, 10**9, 10**12 - 1]
    for n in numbers:
        assert verify_module._amount_words(_turkish_words(n)) == n, n


def test_amount_words_accepts_only_canonical_sequences_seeded() -> None:
    # Every word sequence the grammar reads is the reference writer's spelling of what it read.
    rng = random.Random(19_041)
    vocabulary = [*_WORD_ONES[1:], *_WORD_TENS[1:], "yüz", "bin", "milyon", "milyar", "trilyon"]
    short = [
        " ".join(seq) for size in (1, 2, 3) for seq in itertools.product(vocabulary, repeat=size)
    ]
    accepted = 0
    for text in short + _amount_word_sequences(rng, 60_000):
        value = verify_module._amount_words(text)
        if value is not None:
            accepted += 1
            assert value == int(value) and 1 <= value < 10**12, text
            assert _turkish_words(int(value)) == text, text
    assert accepted > 1000


def test_extract_amount_restatement_round_trip_seeded() -> None:
    rng = random.Random(19_042)
    for _ in range(300):
        n = rng.randint(1, 10**12 - 1)
        same = f"Bedel {_tl_digits(n)} TL ({_turkish_words(n)} Türk lirası) olarak."
        extraction = extract(same, "amount")
        # "X milyon bin ..." is two number tokens, so it is no restatement (left unreadable).
        if " milyon bin" not in _turkish_words(n) and " milyar bin" not in _turkish_words(n):
            assert extraction == Extraction((Quantity(n, "TL"), Quantity(n, "TL")), ()), same
        for other in {n + 1, n - 1, n * 10, n + 1000, rng.randint(1, 10**12 - 1)} - {n}:
            if not 1 <= other < 10**12:
                continue
            text = f"Bedel {_tl_digits(n)} TL ({_turkish_words(other)} Türk lirası) olarak."
            result = extract(text, "amount")
            assert result.values == (Quantity(n, "TL"),), text
            assert result.unreadable, text


# --- A percent restated in words in parentheses ---------------------------------------------------

AT_LEAST_100_PERCENT = _rule(">=", 100, "%")


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("%25'i (yüzde yirmi beşi)", 25),
        ("%100'ü (yüzde yüzü)", 100),
        ("%20'sini (yüzde yirmisini)", 20),
        ("%50'si (yüzde ellisi)", 50),
        ("%40'ı (yüzde kırkı)", 40),
        ("%10'u (yüzde onu)", 10),
        ("%6'sı (yüzde altısı)", 6),
        ("%25'ine (yüzde yirmi beşine)", 25),
        ("%30'unu (yüzde otuzunu)", 30),
        ("%15'inden (yüzde on beşinden)", 15),
        ("%25'e (yüzde yirmi beşe)", 25),
        ("%120'si (yüzde yüz yirmisi)", 120),
        ("% 25'i (yüzde yirmi beşi)", 25),
        ("yüzde 25'i (yüzde yirmi beşi)", 25),
        (f"%25{chr(0x2019)}i (yüzde yirmi beşi)", 25),
        ("%25'i (YÜZDE YİRMİ BEŞİ)", 25),
        ("%25'i(yüzde yirmi beşi)", 25),
    ],
)
def test_extract_percent_restated_in_words_single_value(text: str, value: float) -> None:
    result = extract(f"Sorumluluk bedelin {text} ile sınırlıdır.", "percent")
    assert result == Extraction((Quantity(value, "%"), Quantity(value, "%")), ())
    assert verify_module._single(result)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Words that say another value.
        ("%25'i (yüzde otuzu)", Extraction((Quantity(25, "%"),), ("yüzde otuzu",))),
        ("%25'i (yüzde yirmi altısı)", Extraction((Quantity(25, "%"),), ("yüzde yirmi altısı",))),
        ("%100'ü (yüzde onu)", Extraction((Quantity(100, "%"),), ("yüzde onu",))),
        # The suffix differs, or is written on one side only.
        ("%25'i (yüzde yirmi beşini)", Extraction((Quantity(25, "%"),), ("yüzde yirmi beşini",))),
        ("%25 (yüzde yirmi beşi)", Extraction((Quantity(25, "%"),), ("yüzde yirmi beşi",))),
        # Not a suffix of the closed list, not canonical words, not a percent sign.
        ("%10'luk (yüzde onluk)", Extraction((Quantity(10, "%"),), ("yüzde onluk",))),
        ("%25'i (yüzde yirmibeşi)", Extraction((Quantity(25, "%"),), ("yüzde yirmibeşi",))),
        ("%25'i (%yirmi beşi)", Extraction((Quantity(25, "%"),), ("%yirmi beşi",))),
        # Not right after the digits, or not the whole group.
        ("%25'i, (yüzde yirmi beşi)", Extraction((Quantity(25, "%"),), ("yüzde yirmi beşi",))),
        ("%25'i (yüzde yirmi beşi", Extraction((Quantity(25, "%"),), ("yüzde yirmi beşi",))),
        (
            "%25'i (oran yüzde yirmi beşi)",
            Extraction((Quantity(25, "%"),), ("yüzde yirmi beşi",)),
        ),
        # The digits are no read one-number percent expression.
        ("%1,5 milyon'u (yüzde yüzü)", Extraction((), ("%1,5 milyon", "yüzde yüzü"))),
        (
            "%25 (30)'u (yüzde otuzu)",
            Extraction((Quantity(25, "%"), Quantity(30, "%")), ("yüzde otuzu",)),
        ),
        # The words first: as for TL ("bin TL (₺1.000)"), that order stays unreadable.
        ("yüzde yirmi beşi (%25'i)", Extraction((Quantity(25, "%"),), ("yüzde yirmi beşi",))),
    ],
)
def test_extract_percent_words_not_restating_digits_unreadable(
    text: str, expected: Extraction
) -> None:
    assert extract(f"Sorumluluk bedelin {text} ile sınırlıdır.", "percent") == expected


def test_extract_percent_words_without_suffix_unchanged() -> None:
    # Words without a suffix were already read; they still are, and a different value conflicts.
    assert extract("bedelin %25 (yüzde yirmi beş) ile", "percent") == Extraction(
        (Quantity(25, "%"), Quantity(25, "%")), ()
    )
    assert extract("bedelin %25 (yüzde otuz) ile", "percent") == Extraction(
        (Quantity(25, "%"), Quantity(30, "%")), ()
    )
    assert extract("yüzde yirmi beş (%25) oranında", "percent") == Extraction(
        (Quantity(25, "%"), Quantity(25, "%")), ()
    )


def test_extract_percent_restatement_gives_unit_to_linked_neighbour() -> None:
    # As for TL: the restatement is read, so the connector rule gives its unit to the unit-less
    # 30; before, the words were unreadable and 30 had no unit.
    assert extract("bedelin %25'i (yüzde yirmi beşi) ila 30 arası", "percent") == Extraction(
        (Quantity(25, "%"), Quantity(25, "%"), Quantity(30, "%")), ()
    )


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("25'i (yüzde yirmi beşi) gün", "duration"),
        ("₺25'i (yüzde yirmi beşi)", "amount"),
    ],
)
def test_extract_percent_restatement_other_kinds_no_value(text: str, kind: Kind) -> None:
    assert Quantity(25, "%") not in extract(f"Süre {text} olarak.", kind).values


def test_verify_percent_restated_in_words_draft_passed() -> None:
    clause = "Sorumluluk bedelin %25'i (yüzde yirmi beşi) ile sınırlıdır."
    draft = "Sorumluluk bedelin %100'ü (yüzde yüzü) ile sınırlıdır."
    finding = _finding(clause, status="non_compliant", proposal=_proposal(draft))
    result = _run(finding, AT_LEAST_100_PERCENT)
    assert result.review_reasons == []
    assert result.proposal is not None
    assert result.proposal.code_check == "passed"


def test_verify_percent_restated_in_words_wrong_compliant_numeric_mismatch() -> None:
    quote = "Sorumluluk bedelin %25'i (yüzde yirmi beşi) ile sınırlıdır."
    assert _reasons(_finding(quote), AT_LEAST_100_PERCENT) == ["numeric_mismatch"]


@pytest.mark.parametrize(
    "draft",
    [
        "Sorumluluk bedelin %100'ü (yüzde onu) ile sınırlıdır.",
        "Sorumluluk bedelin %100'ü (yüzde yüzünü) ile sınırlıdır.",
        "Sorumluluk bedelin %10'u (yüzde onu) ile sınırlıdır.",
    ],
)
def test_verify_proposal_percent_words_differ_failed(draft: str) -> None:
    finding = _finding("x", status="non_compliant", proposal=_proposal(draft))
    result = _run(finding, AT_LEAST_100_PERCENT, "x")
    assert result.proposal is not None
    assert result.proposal.code_check == "failed"
    assert "proposal_noncompliant" in result.review_reasons


_PERCENT_SUFFIXES = ["i", "ı", "u", "ü", "si", "sı", "su", "sü", "ini", "ine", "inde", "e", "a"]


def test_extract_percent_restatement_round_trip_seeded() -> None:
    # The suffix is written as-is on both sides (harmony is not checked): the value comes from the
    # words, compared with the digits.
    rng = random.Random(19_061)
    for _ in range(400):
        n = rng.randint(1, 999)
        suffix = rng.choice(_PERCENT_SUFFIXES)
        same = f"bedelin %{n}'{suffix} (yüzde {_turkish_words(n)}{suffix}) ile"
        assert extract(same, "percent") == Extraction((Quantity(n, "%"), Quantity(n, "%")), ()), (
            same
        )
        other = rng.choice([k for k in (n + 1, n - 1, n * 2, rng.randint(1, 999)) if k != n])
        if not 1 <= other <= 999:
            continue
        text = f"bedelin %{n}'{suffix} (yüzde {_turkish_words(other)}{suffix}) ile"
        result = extract(text, "percent")
        assert result.values == (Quantity(n, "%"),), text
        assert result.unreadable, text


# --- A ones word before an inflected "yüz" is not read as a number --------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "bedelin yüzde iki yüzü olarak",
        "bedelin yüzde üç yüzünü aşamaz",
        "bedelin yüzde beş yüzü olarak",
        "bedelin yüzde dokuz yüzüne kadar",
        "bedelin yüzde iki yüzden fazla",
        "bedelin yüzde iki yüzlük artışı",
        "bedelin YÜZDE İKİ YÜZÜ olarak",
        # Any ending counts, and so does a hyphen between the words.
        "sınır bedelin yüzde iki yüzüdür",
        "bedelin yüzde iki yüzüyle sınırlıdır",
        "bedelin yüzde üç yüzünce",
        "bedelin yüzde iki-yüzü olarak",
    ],
)
def test_extract_ones_word_before_inflected_hundred_unreadable(text: str) -> None:
    # "iki" is the start of 200, not 2 % (agent_design.md §5: no part of a number is read).
    result = extract(text, "percent")
    assert result.values == ()
    assert len(result.unreadable) == 1


@pytest.mark.parametrize(
    ("text", "values"),
    [
        # "yüzde" after a ones word starts the next percent.
        ("yüzde iki yüzde üç", (2, 3)),
        # "bir yüzü" is "one side of it"; a word that only starts with "yüz" is another word.
        ("belgenin bir yüzü yüzde beş", (5,)),
        ("iki yüzyıl yüzde beş", (5,)),
        ("yüzde iki yüzey", (2,)),
        # No ones word right before: unchanged.
        ("yüzde yirmi beş yüzü", (25,)),
        ("yüzde iki yüz", (200,)),
    ],
)
def test_extract_inflected_hundred_rule_leaves_other_words(
    text: str, values: tuple[int, ...]
) -> None:
    assert extract(text, "percent") == Extraction(tuple(Quantity(v, "%") for v in values), ())


def test_extract_inflected_hundred_restated_reads_whole_value() -> None:
    assert extract("bedelin %200'ü (yüzde iki yüzü) ile", "percent") == Extraction(
        (Quantity(200, "%"), Quantity(200, "%")), ()
    )


@pytest.mark.parametrize("words", ["iki", "üç", "beş"])
def test_verify_compliant_inflected_hundred_in_clause_ambiguous(words: str) -> None:
    # The 25 % restatement is one value; the clause's "yüzde iki yüzü" (200 %) is not 2 %.
    quote = "sözleşme bedelinin %25'i (yüzde yirmi beşi) ile sınırlıdır"
    clause = (
        f"Yüklenicinin sorumluluğu {quote}; ağır kusur hâlinde bu sınır bedelin yüzde {words} "
        "yüzü olarak uygulanır."
    )
    assert _reasons(_finding(quote), _rule("<=", 100, "%"), clause) == ["ambiguous_value"]


def test_verify_proposal_inflected_hundred_not_passed() -> None:
    draft = "Sorumluluk bedelin %100'ü (yüzde yüzü), ağır kusurda yüzde iki yüzü ile sınırlıdır."
    finding = _finding("x", status="non_compliant", proposal=_proposal(draft))
    result = _run(finding, _rule("<=", 100, "%"), "x")
    assert result.proposal is not None
    assert result.proposal.code_check != "passed"


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


# --- Hours: read only for a rule in saat ---------------------------------------------------------

_HOUR_FORMS = (
    "saat", "saatlik", "saati", "saate", "saatte", "saatten", "saatin", "saatler", "saattir",
)  # fmt: skip
AT_MOST_48_HOURS = _rule("<=", 48, "saat")


def test_unit_tables_cover_every_check_unit() -> None:
    # A unit added to CheckUnit alone would raise KeyError in `_KIND_OF_UNIT` / `align`.
    units = set(get_args(CheckUnit))
    assert set(verify_module._KIND_OF_UNIT) == units
    durations = {u for u in units if verify_module._KIND_OF_UNIT[u] == "duration"}
    assert set(verify_module._DURATION_FORM_LISTS) == durations
    assert set(verify_module._DURATION_STEMS) == durations
    for a in units:
        for b in units:
            if verify_module._KIND_OF_UNIT[a] == verify_module._KIND_OF_UNIT[b]:
                align(Quantity(1, a), Quantity(1, b))
            else:
                with pytest.raises(ValueError):
                    align(Quantity(1, a), Quantity(1, b))


@pytest.mark.parametrize("form", _HOUR_FORMS)
def test_extract_hour_form_read_only_for_hours(form: str) -> None:
    text = f"Müdahale 48 {form} içinde"
    assert extract(text, "duration", hours=True) == Extraction((Quantity(48, "saat"),), ())
    assert extract(text, "duration") == Extraction((), (f"48 {form}",))
    assert extract(text, "duration").spans == extract(text, "duration", hours=True).spans


@pytest.mark.parametrize(
    ("text", "expression"),
    [
        ("17 saatine kadar", "17 saatine"),
        ("72 saatinde", "72 saatinde"),
        ("24 saatinden", "24 saatinden"),
        ("8 iş saati", "8 iş saati"),
        ("8 mesai saati", "8 mesai saati"),
        ("48. saatte", "48. saatte"),
        ("7/24 saat", "7/24 saat"),
        ("9,30 saatinde", "9,30 saatinde"),
        ("08:30 saatinde", "30 saatinde"),
        ("17:00 saatine", "00 saatine"),
        ("24:00 saatinden", "00 saatinden"),
        ("08:30 saati", "30 saati"),
        ("08:30 saat", "30 saat"),
    ],
)
def test_extract_hour_clock_time_or_other_form_unreadable(text: str, expression: str) -> None:
    assert extract(f"Bildirim {text} yapılır", "duration", hours=True) == Extraction(
        (), (expression,)
    )


@pytest.mark.parametrize(
    "text", ["saat 17:00'ye kadar", "17.00'ye kadar", "saat başı", "saatlik ücret 500 TL"]
)
def test_extract_hour_word_without_number_nothing(text: str) -> None:
    assert extract(text, "duration", hours=True) == Extraction((), ())


def test_extract_hour_restatement_range_and_minutes() -> None:
    hours = [Quantity(72, "saat")] * 2
    assert extract("yetmiş iki (72) saat", "duration", hours=True).values == tuple(hours)
    assert extract("48 ila 72 saat", "duration", hours=True).values == (
        Quantity(48, "saat"),
        Quantity(72, "saat"),
    )
    assert extract("1 saat 30 dakika", "duration", hours=True) == Extraction(
        (Quantity(1, "saat"),), ("30 dakika",)
    )
    # The bridge word holds for hours as for days (known oddity, harmless).
    assert extract("72 takvim saati", "duration", hours=True).values == (Quantity(72, "saat"),)


def test_extract_clock_time_never_an_hour_value_seeded() -> None:
    rng = random.Random(19_005)
    forms = [*_HOUR_FORMS, "saatine", "saatinde", "saatinden", "saatleri", "Saat", "SAATTE"]
    fillers = ["Teslim", "en geç", "içinde", "30 gün", "veya", "Ayrıca", "%5", "", "(", ")"]
    for _ in range(10_000):
        hh, mm = rng.randrange(25), rng.randrange(60)
        before = " ".join(rng.choice(fillers) for _ in range(rng.randint(0, 3)))
        after = " ".join(rng.choice(fillers) for _ in range(rng.randint(0, 3)))
        text = f"{before} {hh:02d}:{mm:02d} {rng.choice(forms)} {after}"
        values = extract(text, "duration", hours=True).values
        assert all(q.unit != "saat" for q in values), text


def test_align_hours_exact_for_days_and_weeks_interval_for_months_and_years() -> None:
    assert align(Quantity(72, "saat"), Quantity(3, "gün")) == ((72, 72), (72, 72))
    assert align(Quantity(1, "hafta"), Quantity(168, "saat")) == ((168, 168), (168, 168))
    assert align(Quantity(1, "ay"), Quantity(720, "saat")) == ((672, 744), (720, 720))
    assert align(Quantity(720, "saat"), Quantity(1, "yıl")) == ((720, 720), (8760, 8784))


@pytest.mark.parametrize(
    ("quote", "status", "rule", "expected"),
    [
        ("Müdahale 48 saat içinde yapılır.", "compliant", AT_MOST_48_HOURS, []),
        ("Müdahale 2 gün içinde yapılır.", "compliant", AT_MOST_48_HOURS, []),
        ("Müdahale 1 hafta içinde yapılır.", "compliant", AT_MOST_48_HOURS, ["numeric_mismatch"]),
        ("Müdahale 96 saat içinde yapılır.", "compliant", AT_MOST_48_HOURS, ["numeric_mismatch"]),
        ("Müdahale 3 iş günü içinde yapılır.", "compliant", AT_MOST_48_HOURS, ["numeric_mismatch"]),
        ("Müdahale 1 saat 30 dakika içinde.", "compliant", AT_MOST_48_HOURS, ["ambiguous_value"]),
        ("Müdahale 08:30 saatinde yapılır.", "compliant", AT_MOST_48_HOURS, ["numeric_mismatch"]),
        ("Müdahale 1 ay içinde yapılır.", "compliant", _rule(">=", 720, "saat"),
         ["unit_conversion_uncertain"]),
        ("Süre bir gündür.", "compliant", _rule("==", 24, "saat"), []),
    ],
)  # fmt: skip
def test_verify_hour_rule_quote(quote: str, status: str, rule: Rule, expected: list[str]) -> None:
    assert _reasons(_finding(quote, status=status), rule) == expected


def test_verify_hour_rule_value_outside_quote_queued() -> None:
    clause = "Müdahale 48 saat içinde yapılır. Teslimat 5 gün sürer."
    finding = _finding("Müdahale 48 saat içinde yapılır.")
    assert _reasons(finding, AT_MOST_48_HOURS, clause) == ["numeric_mismatch"]


@pytest.mark.parametrize(
    ("draft", "code_check"),
    [
        ("Müdahale 48 saat içinde yapılır.", "passed"),
        ("Müdahale 2 gün içinde yapılır.", "passed"),
        ("Müdahale kırk sekiz (48) saat içinde yapılır.", "passed"),
        ("Müdahale 5 gün içinde yapılır.", "failed"),
        ("Müdahale saat 17:00'ye kadar yapılır.", "failed"),
        ("Müdahale 08:30 saatinde yapılır.", "failed"),
        ("Müdahale 72 saatinde yapılır.", "failed"),
        ("Müdahale 8 iş saati içinde yapılır.", "failed"),
    ],
)
def test_verify_hour_rule_proposal_check(draft: str, code_check: str) -> None:
    finding = _finding(
        "Müdahale 72 saat içinde yapılır.", status="non_compliant", proposal=_proposal(draft)
    )
    result = _run(finding, AT_MOST_48_HOURS)
    assert result.proposal is not None
    assert result.proposal.code_check == code_check


@pytest.mark.parametrize("unit", ["saat", "Saat", "SAAT", "takvim saati", "gün"])
def test_verify_hour_rule_model_unit_read(unit: str) -> None:
    value = 2 if unit == "gün" else 48
    finding = _finding("Müdahale 48 saat içinde yapılır.", value=value, unit=unit)
    assert _reasons(finding, AT_MOST_48_HOURS) == []


@pytest.mark.parametrize("unit", ["saat", "Saat", "takvim saati"])
def test_verify_hour_unit_in_day_rule_unknown(unit: str) -> None:
    # Only a rule in saat knows the hour: elsewhere the model's "saat" is no unit.
    finding = _finding("30 gün önceden", value=720, unit=unit)
    assert _reasons(finding, AT_LEAST_30_DAYS) == ["numeric_mismatch"]
    assert verify_module._model_unit(unit, "gün") is None
    assert verify_module._model_unit(unit, "saat") == "saat"


def test_verify_hour_in_day_rule_still_unreadable() -> None:
    assert extract("itiraz 48 saat içinde", "duration") == Extraction((), ("48 saat",))
    clause = "30 gün içinde ödenir; itiraz 48 saat içinde yapılır."
    assert _reasons(_finding("30 gün içinde ödenir"), AT_LEAST_30_DAYS, clause) == [
        "ambiguous_value"
    ]


def test_verify_hour_rule_loaded_from_rules_file_end_to_end(data_dir: Path) -> None:
    rules_file = data_dir / "tenants" / "acme" / "rules.yaml"
    rules_file.write_text(
        '- id: response_time\n  text: "Müdahale en geç 48 saat içinde."\n'
        '  check: {op: "<=", value: 48, unit: saat}\n',
        encoding="utf-8",
    )
    (rule,) = Store(data_dir, "acme").load_rules()
    assert rule.check == Check(op="<=", value=48, unit="saat")
    clause = _clause("Arızaya 72 saat içinde müdahale edilir.")
    finding = _finding(
        "Arızaya 72 saat içinde müdahale edilir.",
        status="non_compliant",
        rule_id="response_time",
        value=72,
        unit="saat",
        proposal=_proposal("Arızaya 48 saat içinde müdahale edilir."),
    )
    (result,) = verify([finding], [rule], [clause], False)
    assert result.review_reasons == []
    assert result.proposal is not None and result.proposal.code_check == "passed"
    wrong = finding.model_copy(update={"proposal": _proposal("Arızaya 3 gün içinde.")})
    (result,) = verify([wrong], [rule], [clause], False)
    assert result.review_reasons == ["proposal_noncompliant"]


# Clock times in a rule in hours: no member that may be a clock time or its minutes is read.
_CLOCK_COLON_CHARS = (":", "\u2236", "\uff1a", "\u02d0", "\ua789")
_NBSP = "\u00a0"
_CLOCK_TIMES = [
    ("08: 30 saati", "30 saati"),
    ("08 :30 saati", "30 saati"),
    ("08 : 30 saati", "30 saati"),
    (f"08{_NBSP}:{_NBSP}30 saati", "30 saati"),
    ("08\u223630 saati", "30 saati"),
    ("08 \u2236 30 saatte", "30 saatte"),
    ("08\uff1a30 saati", "30 saati"),
    ("08\u02d030 saati", "30 saati"),
    ("08\ua78930 saati", "30 saati"),
    ("08h30 saatte", "30 saatte"),
    ("08H30 saatte", "30 saatte"),
    ("08 h 30 saatte", "30 saatte"),
    ("08 30 saatte", "30 saatte"),
    ("9,30 saati", "9,30 saati"),
    ("8,30 saatte", "8,30 saatte"),
    ("08,30 saatte", "08,30 saatte"),
    ("00,00 saatte", "00,00 saatte"),
    ("24,59 saatte", "24,59 saatte"),
]


@pytest.mark.parametrize(("text", "expression"), _CLOCK_TIMES)
def test_extract_hour_clock_time_spellings_unreadable(text: str, expression: str) -> None:
    sentence = f"Müdahale {text} itibarıyla yapılır."
    assert extract(sentence, "duration", hours=True) == Extraction((), (expression,))
    # Outside a rule in hours nothing changes: the hour stays unreadable, with the same span.
    assert extract(sentence, "duration") == Extraction((), (expression,))
    assert extract(sentence, "duration").spans == extract(sentence, "duration", hours=True).spans


@pytest.mark.parametrize(("text", "_expression"), _CLOCK_TIMES)
def test_verify_hour_rule_clock_time_queued_and_draft_failed(text: str, _expression: str) -> None:
    sentence = f"Müdahale {text} itibarıyla yapılır."
    assert _reasons(_finding(sentence), AT_MOST_48_HOURS) == ["numeric_mismatch"]
    clause = f"Müdahale 48 saat içinde yapılır. Bakım {text} itibarıyla başlar."
    finding = _finding("Müdahale 48 saat içinde yapılır.")
    assert _reasons(finding, AT_MOST_48_HOURS, clause) == ["ambiguous_value"]
    draft = _non_compliant_with_hours(sentence)
    assert draft.proposal is not None
    assert draft.proposal.code_check == "failed"


def _non_compliant_with_hours(draft: str) -> Finding:
    finding = _finding(
        "Müdahale 72 saat içinde yapılır.", status="non_compliant", proposal=_proposal(draft)
    )
    return _run(finding, AT_MOST_48_HOURS)


def test_verify_hour_rule_clock_time_minimal_reproduction() -> None:
    quote = "Müdahale 08: 30 saati itibarıyla yapılır."
    assert _reasons(_finding(quote), AT_MOST_48_HOURS) == ["numeric_mismatch"]


def test_extract_hour_comma_with_two_decimals_unreadable() -> None:
    # The cost of reading "9,30 saati" as a clock time: a real duration written H,MM stays
    # unreadable too and goes to the lawyer (the safe side).
    assert extract("1,50 saat içinde", "duration", hours=True) == Extraction((), ("1,50 saat",))
    assert _reasons(_finding("1,50 saat içinde"), AT_MOST_48_HOURS) == ["numeric_mismatch"]
    # Out of the clock ranges a comma number is a duration.
    assert extract("25,30 saatte", "duration", hours=True).values == (Quantity(25.3, "saat"),)
    assert extract("9,60 saatte", "duration", hours=True).values == (Quantity(9.6, "saat"),)


@pytest.mark.parametrize("text", ["9.30 saati", "17.00 saatte"])
def test_extract_hour_dotted_clock_time_unreadable_as_before(text: str) -> None:
    assert extract(f"Bakım {text} başlar", "duration", hours=True) == Extraction((), (text,))


def test_extract_hour_clock_time_known_limits() -> None:
    # A clock time with no minutes reads as hours.
    assert extract("17 saati itibarıyla", "duration", hours=True) == Extraction(
        (Quantity(17, "saat"),), ()
    )
    # The hours of a clock time linked by a connector take the unit: two values, so queued.
    text = "Müdahale 14 saat veya 12:00 itibarıyla yapılır."
    assert extract(text, "duration", hours=True).values == (
        Quantity(14, "saat"),
        Quantity(12, "saat"),
    )
    assert _reasons(_finding(text), AT_MOST_48_HOURS) == ["ambiguous_value"]


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("72 saat", [(72, "saat")]),
        ("yetmiş iki (72) saat", [(72, "saat"), (72, "saat")]),
        ("24 saatlik", [(24, "saat")]),
        ("48 saatte", [(48, "saat")]),
        ("1,5 saat", [(1.5, "saat")]),
        ("0,5 saat", [(0.5, "saat")]),
        ("2 gün 12 saat", [(2, "gün"), (12, "saat")]),
        ("2 gün, 12 saat", [(2, "gün"), (12, "saat")]),
        ("Süre: 30 saat", [(30, "saat")]),
        ("48 ila 72 saat", [(48, "saat"), (72, "saat")]),
    ],
)
def test_extract_hour_durations_still_read(text: str, values: list[tuple[float, str]]) -> None:
    extraction = extract(f"Müdahale {text} içinde yapılır.", "duration", hours=True)
    assert extraction.unreadable == ()
    assert [(q.amount, q.unit) for q in extraction.values] == values


def test_extract_clock_time_spellings_never_an_hour_value_seeded() -> None:
    rng = random.Random(19_051)
    spaces = ["", " ", _NBSP, " " + _NBSP]
    colons = [left + c + right for c in _CLOCK_COLON_CHARS for left in spaces for right in spaces]
    separators = [*colons, " ", "  ", _NBSP, "h", "H", " h ", "h ", " H", ","]
    forms = [*_HOUR_FORMS, "saatine", "saatinde", "saatinden", "saatleri", "Saat", "SAATTE"]
    fillers = ["Teslim", "en geç", "içinde", "30 gün", "veya", "Ayrıca", "%5", "", "(", ")"]
    for _ in range(10_000):
        hh, mm = rng.randrange(25), rng.randrange(60)
        hours = f"{hh:02d}" if rng.random() < 0.5 else str(hh)
        before = " ".join(rng.choice(fillers) for _ in range(rng.randint(0, 3)))
        after = " ".join(rng.choice(fillers) for _ in range(rng.randint(0, 3)))
        text = f"{before} {hours}{rng.choice(separators)}{mm:02d} {rng.choice(forms)} {after}"
        extraction = extract(text, "duration", hours=True)
        assert all(q.unit != "saat" for q in extraction.values), ascii(text)
        assert extraction.unreadable, ascii(text)


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


# "No value in the draft breaks the rule" (agent_design.md §5 "Taslak kontrolü"): a whole-clause
# draft keeps the clause's other values of the rule's kind, so several different values are fine
# as long as every one of them complies and nothing is unreadable.
AT_MOST_10_PERCENT = _rule("<=", 10, "%")
AT_MOST_45_DAYS = _rule("<=", 45, "gün")


def _draft_result(text: str, rule: Rule) -> Finding:
    # The finding itself is a correct "non_compliant" one, so only the draft can add a reason.
    if rule == AT_MOST_10_PERCENT:
        quote = "cezanın toplamı teslimat bedelinin %20'sini aşamaz"
    elif rule == AT_MOST_45_DAYS:
        quote = "ithal ürünlerde bu süre 75 gündür"
    else:
        assert rule == AT_LEAST_30_DAYS
        quote = "15 gün önceden"
    return _run(_finding(quote, status="non_compliant", proposal=_proposal(text)), rule)


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        # Both ends are at least 30 days.
        ("Bildirim 30 ila 60 gün önceden yapılır.", AT_LEAST_30_DAYS),
        # Daily rate next to the cap, both within 10 %.
        (
            "Geciken her gün için teslimat bedelinin günlük %1'i oranında ceza uygulanır; "
            "cezanın toplamı teslimat bedelinin %10'unu aşamaz.",
            AT_MOST_10_PERCENT,
        ),
        # The general term next to the corrected exception term: 30, 30, 45.
        (
            "Ödeme, fatura tarihinden itibaren otuz (30) gün içinde yapılır. "
            "İthal ürünlerde bu süre 45 gündür.",
            AT_MOST_45_DAYS,
        ),
        ("Gecikme cezası %10 (yüzde on) ile sınırlıdır.", AT_MOST_10_PERCENT),
    ],
)
def test_verify_proposal_several_compliant_values_passed(text: str, rule: Rule) -> None:
    result = _draft_result(text, rule)
    assert result.proposal is not None
    assert result.proposal.code_check == "passed"
    assert result.review_reasons == []
    assert result.needs_review is False


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        # One end is below 30 days.
        ("Bildirim 15 ila 60 gün önceden yapılır.", AT_LEAST_30_DAYS),
        # The cap still breaks the rule; the compliant daily rate does not save it.
        (
            "Geciken her gün için teslimat bedelinin günlük %1'i oranında ceza uygulanır; "
            "cezanın toplamı teslimat bedelinin %20'sini aşamaz.",
            AT_MOST_10_PERCENT,
        ),
        # The exception term is kept at 75.
        (
            "Ödeme, fatura tarihinden itibaren otuz (30) gün içinde yapılır. "
            "İthal ürünlerde bu süre 75 gündür.",
            AT_MOST_45_DAYS,
        ),
        # The draft quotes the old value, which the code reads like any other.
        (
            "Ödeme 30 gün içinde yapılır. "
            "İthal ürünlerde bu süre 45 gündür (önceki 75 gün yerine).",
            AT_MOST_45_DAYS,
        ),
        # "45 iş günü" is unreadable: the compliant 30 next to it does not save the draft.
        ("Ödeme 30 gün içinde yapılır. İthal ürünlerde bu süre 45 iş günüdür.", AT_MOST_45_DAYS),
    ],
)
def test_verify_proposal_one_value_breaking_or_unreadable_failed(text: str, rule: Rule) -> None:
    result = _draft_result(text, rule)
    assert result.proposal is not None
    assert result.proposal.code_check == "failed"
    assert result.review_reasons == ["proposal_noncompliant"]
    assert result.needs_review is True


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        # The cap is gone and "sınırsızdır" is not a value; "20 gün" is of another kind.
        (
            "Geciken her gün için bedelin %1'i ceza uygulanır, ancak 20 günü aşan gecikmede "
            "ceza sınırsızdır.",
            AT_MOST_10_PERCENT,
        ),
        # "45 günü izleyen ayın sonunda" can exceed 45 days, but the code reads only 45.
        (
            "Ödeme, fatura tarihinden itibaren 45 günü izleyen ayın sonunda yapılır.",
            AT_MOST_45_DAYS,
        ),
    ],
)
def test_verify_proposal_known_limit_draft_that_drops_the_rule_passed(
    text: str, rule: Rule
) -> None:
    # Documented limit (agent_design.md §5): the code does not know which value is the regulated
    # one and checks only the values it reads. These drafts pass and are not queued.
    result = _draft_result(text, rule)
    assert result.proposal is not None
    assert result.proposal.code_check == "passed"
    assert result.review_reasons == []


def test_verify_proposal_rule_without_check_unverifiable_queued() -> None:
    proposal = _proposal("Sorumluluk sözleşme bedeli ile sınırlıdır.")
    finding = _finding("Satıcı sorumluluğu sınırsızdır.", status="non_compliant", proposal=proposal)
    result = _run(finding, NO_CHECK)
    assert result.proposal is not None
    assert result.proposal.code_check == "unverifiable"
    assert result.review_reasons == ["proposal_unverifiable"]
    assert result.needs_review is True


@pytest.mark.parametrize(
    "text",
    [
        "Sorumluluk sözleşme bedeli ile sınırlıdır.",
        "Satıcı sorumluluğu sınırsızdır.",  # repeats the violation: code still cannot tell
        "",
    ],
)
def test_verify_proposal_unverifiable_queued_whatever_the_text(text: str) -> None:
    finding = _finding(
        "Satıcı sorumluluğu sınırsızdır.", status="non_compliant", proposal=_proposal(text)
    )
    result = _run(finding, NO_CHECK)
    assert result.review_reasons == ["proposal_unverifiable"]
    assert result.needs_review is True


def test_verify_proposal_unverifiable_on_compliant_finding_queued() -> None:
    # A compliant finding can carry a draft (agent_design.md §5): the reason ignores the status.
    proposal = _proposal("Türk hukuku uygulanır.")
    finding = _finding("Türk hukuku uygulanır.", proposal=proposal)
    result = _run(finding, NO_CHECK)
    assert result.proposal is not None
    assert result.proposal.code_check == "unverifiable"
    assert result.review_reasons == ["proposal_unverifiable"]
    assert result.needs_review is True
    # Without the draft the same finding is not queued: the draft alone queues it.
    assert _reasons(_finding("Türk hukuku uygulanır."), NO_CHECK) == []


def test_verify_proposal_unverifiable_on_not_found_finding_kept_with_other_reasons() -> None:
    finding = _finding(
        status="not_found", clause_id=None, proposal=_proposal("Türk hukuku uygulanır.")
    )
    result = _run(finding, _rule(severity="critical"))
    assert result.review_reasons == [
        "unverified_not_found",
        "critical_not_found",
        "proposal_unverifiable",
    ]


def test_verify_proposal_unverifiable_not_added_when_disabled() -> None:
    proposal = _proposal("Türk hukuku uygulanır.", code_check="unverifiable")
    finding = _finding("İngiliz hukuku uygulanır.", status="non_compliant", proposal=proposal)
    result = _run(finding, NO_CHECK, enabled=False)
    assert result.review_reasons == []
    assert result.needs_review is False
    assert result.proposal is not None
    assert result.proposal.code_check is None


@pytest.mark.parametrize(
    ("text", "code_check", "reasons"),
    [
        ("Bildirim 45 gün önceden yapılır.", "passed", []),
        ("Bildirim 15 gün önceden yapılır.", "failed", ["proposal_noncompliant"]),
    ],
)
def test_verify_proposal_unverifiable_not_added_to_checked_draft(
    text: str, code_check: str, reasons: list[str]
) -> None:
    # A stale "unverifiable" on the input is rebuilt from the rule's check.
    finding = _finding(
        "15 gün önceden", status="non_compliant", proposal=_proposal(text, "unverifiable")
    )
    result = _run(finding, AT_LEAST_30_DAYS)
    assert result.proposal is not None
    assert result.proposal.code_check == code_check
    assert result.review_reasons == reasons


def test_verify_proposal_unverifiable_in_literal_order() -> None:
    finding = _finding(
        "uydurma alıntı",
        status="non_compliant",
        proposal=_proposal("Türk hukuku uygulanır."),
        escalation_reason="Emin değilim.",
        interrupted_by="llm_error",
    )
    result = _run(finding, NO_CHECK, "Başka metin.", injection=True)
    assert result.review_reasons == [
        "quote_not_found",
        "proposal_unverifiable",
        "agent_escalated",
        "run_interrupted",
        "injection_suspected",
    ]
    order = list(get_args(ReviewReason))
    assert order.index("proposal_unverifiable") == order.index("proposal_noncompliant") + 1
    assert verify([result], [NO_CHECK], [_clause("Başka metin.")], True) == [result]


# --- Partial draft (proposal_partial) -------------------------------------------------------------

PARTIAL = "proposal_partial"
MISMATCH = "proposal_clause_mismatch"
PAYMENT_CLAUSE = (
    "3. ÜCRET VE ÖDEME\n"
    "3.1. Yıllık bedel 100.000 TL'dir.\n"
    "3.2. Faturalar 60 gün içinde ödenir.\n"
    "3.3. Bedel her yıl TÜFE oranında güncellenir."
)
PAYMENT_QUOTE = "Faturalar 60 gün içinde ödenir."


def _partial_run(
    draft: str,
    clause_text: str = PAYMENT_CLAUSE,
    rule: Rule = AT_MOST_45_DAYS,
    proposal_clause_id: str = "c1",
    enabled: bool = True,
    **kwargs: object,
) -> Finding:
    proposal = Proposal(clause_id=proposal_clause_id, proposed_text=draft, rationale="Gerekçe.")
    finding = _finding(PAYMENT_QUOTE, status="non_compliant", proposal=proposal, **kwargs)
    (result,) = verify([finding], [rule], [_clause(clause_text)], False, enabled=enabled)
    return result


def test_verify_proposal_partial_draft_with_one_sub_item_queued() -> None:
    # The run's case: the draft for clause 3 kept only 3.2; the price and the indexation are gone.
    result = _partial_run("3.2. Faturalar 45 gün içinde ödenir.")
    assert result.review_reasons == [PARTIAL]
    assert result.needs_review is True
    # The reason does not touch code_check: the value in the draft is fine.
    assert result.proposal is not None
    assert result.proposal.code_check == "passed"


@pytest.mark.parametrize(
    "draft",
    [
        PAYMENT_CLAUSE.replace("60 gün", "45 gün"),
        # No heading, markers without their trailing dot.
        "3.1 Yıllık bedel 100.000 TL'dir.\n3.2 Faturalar 45 gün içinde ödenir.\n3.3 Bedel aynı.",
        # One line: each marker after a sentence mark and a space.
        "3.1. Yıllık bedel 100.000 TL'dir. 3.2. Faturalar 45 gün içinde ödenir. 3.3. Bedel aynı.",
        "  3.1. Bedel aynı.\n\t3.2. Faturalar 45 gün içinde ödenir;  3.3. Bedel aynı.",
    ],
)
def test_verify_proposal_with_every_sub_item_not_partial(draft: str) -> None:
    # The values of some drafts fail the check ("3.1 Yıllık" is read next to the days); only the
    # marker reading is under test here.
    assert PARTIAL not in _partial_run(draft).review_reasons


@pytest.mark.parametrize(
    "draft",
    [
        # Each draft keeps 3.1 and 3.2; "3.3" appears only as part of something else.
        "3.1. Bedel aynı.\n3.2. Faturalar 45 gün içinde ödenir.\n13.3. Bedel aynı.",
        "3.1. Bedel aynı.\n3.2. Faturalar 45 gün içinde ödenir.\n3.31. Bedel aynı.",
        "3.1. Bedel aynı.\n3.2. Faturalar 45 gün içinde ödenir.\n3.3.1. Bedel aynı.",
        "3.1. Bedel aynı.\n3.2. Faturalar 45 gün içinde ödenir.\n3.3.2027 tarihinde güncellenir.",
        "3.1. Bedel aynı.\n3.2. Faturalar 45 gün içinde ödenir.\n3.3,5 oranında güncellenir.",
        "3.1. Bedel aynı.\n3.2. Faturalar 45 gün içinde ödenir; madde 3.3'te yazılı oran esastır.",
        "3.1. Bedel aynı.\n3.2. Faturalar 45 gün içinde ödenir ve 3.3 maddesi geçerlidir.",
        "3.1. Bedel aynı.\n3.2. Faturalar 45 gün içinde ödenir.\n(3.3) Bedel aynı.",
    ],
)
def test_verify_proposal_marker_inside_a_number_or_sentence_does_not_count(draft: str) -> None:
    assert PARTIAL in _partial_run(draft).review_reasons


@pytest.mark.parametrize(
    "clause_text",
    [
        "Faturalar 60 gün içinde ödenir. Bedel 3.1 oranında güncellenir.",
        "5. ÖDEME\na) Faturalar 60 gün içinde ödenir.\nb) Bedel aynı.",
        "5. ÖDEME\n(i) Faturalar 60 gün içinde ödenir.\n(ii) Bedel aynı.",
        "1. Faturalar 60 gün içinde ödenir.\n2. Bedel aynı.",
        "Bedel:\n1.200.000 TL\nFaturalar 60 gün içinde ödenir.",
        "Başlangıç:\n12.05.2026 tarihinde\nFaturalar 60 gün içinde ödenir.",
        "Faturalar 60 gün içinde ödenir.\n3.1'de yazılı bedel geçerlidir.",
        "Faturalar 60 gün içinde ödenir.\n3.0. Bedel aynı.\n03.1. Bedel aynı.",
    ],
)
def test_verify_proposal_clause_without_marker_never_partial(clause_text: str) -> None:
    result = _partial_run("Faturalar 45 gün içinde ödenir.", clause_text)
    assert PARTIAL not in result.review_reasons


def test_verify_proposal_partial_reads_the_proposal_clause() -> None:
    # The finding points at c1 (no markers); the draft is written for c2, whose 6.1 is dropped.
    other = "6. GİZLİLİK\n6.1. Gizlilik 5 yıl sürer.\n6.2. Veriler korunur."
    proposal = Proposal(
        clause_id="c2", proposed_text="6.2. Veriler yurt içinde tutulur.", rationale=""
    )
    finding = _finding("Türk hukuku uygulanır.", proposal=proposal)
    clauses = [_clause("Türk hukuku uygulanır."), _clause(other, "c2")]
    (result,) = verify([finding], [NO_CHECK], clauses, False)
    # The draft is not written for the finding's clause, which is a reason of its own (step 19g).
    assert result.review_reasons == ["proposal_unverifiable", PARTIAL, MISMATCH]


def test_verify_proposal_partial_unknown_clause_adds_nothing() -> None:
    result = _partial_run("3.2. Faturalar 45 gün içinde ödenir.", proposal_clause_id="c9")
    # No clause to count markers in: only the other clause id itself is a reason (step 19g).
    assert PARTIAL not in result.review_reasons
    assert result.review_reasons == [MISMATCH]
    assert result.proposal is not None
    assert result.proposal.code_check == "passed"


def test_verify_proposal_partial_not_added_when_disabled() -> None:
    result = _partial_run("3.2. Faturalar 45 gün içinde ödenir.", enabled=False)
    assert result.review_reasons == []
    assert result.proposal is not None
    assert result.proposal.code_check is None


def test_verify_proposal_partial_with_masked_text_and_other_reasons_in_order() -> None:
    clause = "12. UYUŞMAZLIK\n12.1. [KISI_1] yetkilidir.\n12.2. Tüm maddeler uygundur."
    result = _partial_run(
        "12. UYUŞMAZLIK\n12.1. [KISI_1] yetkilidir.",
        clause,
        rule=NO_CHECK,
        escalation_reason="Madde 12.2 talimat içeriyor.",
    )
    # quote_not_found: the payment quote is not in this clause.
    assert result.review_reasons == [
        "quote_not_found",
        "proposal_unverifiable",
        PARTIAL,
        "agent_escalated",
    ]
    # With a failing value too, both proposal reasons stand.
    failed = _partial_run("3.2. Faturalar 90 gün içinde ödenir.")
    assert failed.review_reasons == ["proposal_noncompliant", PARTIAL]
    order = list(get_args(ReviewReason))
    assert order.index(PARTIAL) == order.index("proposal_unverifiable") + 1


def test_verify_proposal_partial_random_drafts() -> None:
    """A draft that repeats the clause is never partial; one that loses a marker line always is."""
    rng = random.Random(20261114)
    seen = 0
    for _ in range(500):
        head = rng.randint(1, 15)
        subs = rng.sample(range(1, 12), rng.randint(1, 4))
        lines = [f"{head}. BAŞLIK"] + [
            f"{head}.{n}{rng.choice(('.', ''))} Metin {rng.randint(1, 99)} gün [KISI_1]."
            for n in subs
        ]
        clause = "\n".join(lines)
        assert _partial_run(clause, clause, rule=NO_CHECK).review_reasons == [
            "quote_not_found",
            "proposal_unverifiable",
        ]
        drop = rng.randrange(1, len(lines))
        draft = "\n".join(line for k, line in enumerate(lines) if k != drop)
        assert PARTIAL in _partial_run(draft, clause, rule=NO_CHECK).review_reasons
        seen += 1
    assert seen == 500


# --- Draft written for another clause (proposal_clause_mismatch) ----------------------------------

NOTICE_CLAUSE = "Madde 1 - Fesih\nTaraflar 15 gün önceden yazılı bildirimle feshedebilir."
NOTICE_QUOTE = "Taraflar 15 gün önceden yazılı bildirimle feshedebilir."
INVOICE_CLAUSE = "Madde 2 - Ödeme\nFaturalar 45 gün içinde ödenir."


def _mismatch_run(
    proposal_clause_id: str,
    draft: str = INVOICE_CLAUSE,
    rule: Rule = AT_LEAST_30_DAYS,
    enabled: bool = True,
    **kwargs: object,
) -> Finding:
    """A correct `non_compliant` finding on c1 (the notice clause) with a draft for the given id."""
    proposal = Proposal(clause_id=proposal_clause_id, proposed_text=draft, rationale="Gerekçe.")
    finding = Finding.model_validate(
        {
            "rule_id": "r1",
            "status": "non_compliant",
            "clause_id": "c1",
            "quote": NOTICE_QUOTE,
            "proposal": proposal,
            **kwargs,
        }
    )
    clauses = [_clause(NOTICE_CLAUSE), _clause(INVOICE_CLAUSE, "c2")]
    (result,) = verify([finding], [rule], clauses, False, enabled=enabled)
    return result


def test_verify_proposal_for_another_clause_queued() -> None:
    # The reviewed case: the draft names c2 and copies its text, whose "45 gün" satisfies the
    # notice rule. The value check passes; only the clause id shows the draft fixes nothing.
    result = _mismatch_run("c2")
    assert result.review_reasons == [MISMATCH]
    assert result.needs_review is True
    assert result.proposal is not None
    assert result.proposal.code_check == "passed"
    assert result.proposal.clause_id == "c2"


def test_verify_proposal_for_the_finding_clause_not_mismatch() -> None:
    result = _mismatch_run("c1", NOTICE_CLAUSE.replace("15 gün", "30 gün"))
    assert result.review_reasons == []
    assert result.needs_review is False


@pytest.mark.parametrize("proposal_clause_id", ["c9", "C1", "c1 ", "c10"])
def test_verify_proposal_clause_ids_compared_as_written(proposal_clause_id: str) -> None:
    # An unknown id, another letter case, a trailing space, an id the finding's is a prefix of.
    assert MISMATCH in _mismatch_run(proposal_clause_id).review_reasons


def test_verify_proposal_clause_mismatch_whatever_the_status_and_the_check() -> None:
    compliant = _mismatch_run("c2", status="compliant", quote="Taraflar")
    assert MISMATCH in compliant.review_reasons
    unchecked = _mismatch_run("c2", rule=NO_CHECK)
    assert unchecked.review_reasons == ["proposal_unverifiable", MISMATCH]
    failing = _mismatch_run("c2", "Faturalar 10 gün içinde ödenir.")
    assert failing.review_reasons == ["proposal_noncompliant", MISMATCH]


def test_verify_proposal_on_finding_without_clause_not_mismatch() -> None:
    # A `not_found` finding names no clause: there is nothing to compare the draft's clause with.
    result = _mismatch_run("c2", status="not_found", clause_id=None, quote=None)
    assert MISMATCH not in result.review_reasons
    assert result.review_reasons == ["unverified_not_found"]
    # With a clause id, a `not_found` finding is compared like any other.
    named = _mismatch_run("c2", status="not_found", quote=None)
    assert named.review_reasons == ["unverified_not_found", MISMATCH]


def test_verify_proposal_clause_mismatch_not_added_when_disabled() -> None:
    result = _mismatch_run("c2", enabled=False)
    assert result.review_reasons == []
    assert result.proposal is not None
    assert result.proposal.code_check is None


def test_verify_proposal_clause_mismatch_in_literal_order() -> None:
    result = _mismatch_run("c2", escalation_reason="Emin değilim.")
    assert result.review_reasons == [MISMATCH, "agent_escalated"]
    order = list(get_args(ReviewReason))
    assert order.index(MISMATCH) == order.index(PARTIAL) + 1


def test_verify_proposal_clause_mismatch_random_ids() -> None:
    """The reason is there exactly when the two ids differ, and nothing else in the result moves."""
    rng = random.Random(20261114)
    ids = ["c1", "c2", "c3", "c9"]
    differing = 0
    for _ in range(300):
        proposal_id = rng.choice(ids)
        draft = rng.choice((INVOICE_CLAUSE, NOTICE_CLAUSE, "Faturalar 10 gün içinde ödenir."))
        rule = rng.choice((AT_LEAST_30_DAYS, NO_CHECK))
        result = _mismatch_run(proposal_id, draft, rule)
        same = _mismatch_run("c1", draft, rule)
        expected = [r for r in get_args(ReviewReason) if r in {*same.review_reasons, MISMATCH}]
        if proposal_id == "c1":
            assert result == same
        else:
            differing += 1
            assert result.review_reasons == expected
            assert result.proposal is not None and same.proposal is not None
            assert result.proposal.code_check == same.proposal.code_check
    assert differing > 100


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
            "Bildirim 1 veya 2 ay önceden yapılır.",
            "2 ay önceden yapılır",
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


# The text before or after the match, read on its own, sees what the whole clause drops at a
# sentence boundary: a number whose unit word would stand across the boundary reads nothing in
# the whole clause, and a quote that starts at that sentence must not hide it.
@pytest.mark.parametrize(
    ("clause", "quote", "rule"),
    [
        (
            "Fesih bildirim süresi 30 gün veya 10. Aylık raporlama 60 gün içinde yapılır.",
            "Aylık raporlama 60 gün içinde yapılır.",
            AT_LEAST_30_DAYS,
        ),
        (
            "Fesih bildirimi 60 gün önceden yapılır; acil halde 7 (10 gün). "
            "Günlük raporlar 60 gün saklanır.",
            "Günlük raporlar 60 gün saklanır.",
            AT_LEAST_30_DAYS,
        ),
        (
            "Sigorta bedeli 1.000.000 TL veya 500.000. "
            "Lira cinsinden 2.000.000 TL teminat verilir.",
            "Lira cinsinden 2.000.000 TL teminat verilir.",
            _rule(">=", 1_000_000, "TL"),
        ),
    ],
)
def test_verify_compliant_value_before_sentence_boundary_seen_by_part_reading(
    clause: str, quote: str, rule: Rule
) -> None:
    assert rule.check is not None
    kind: Kind = "amount" if rule.check.unit == "TL" else "duration"
    # The whole clause does not hold the breaking value; only the text before the quote does.
    assert all(verify_module._evaluate([v], rule.check) for v in extract(clause, kind).values)
    assert _reasons(_finding(quote), rule, clause) == ["numeric_mismatch"]


def _breaks_or_unclear(text: str, kind: Kind, check: Check) -> bool:
    """The text holds a value of the kind that does not clearly satisfy the check."""
    reading = extract(text, kind)
    return bool(reading.unreadable) or any(
        verify_module._evaluate([value], check) is not True for value in reading.values
    )


def _seeded_clause(rng: random.Random, kind: Kind) -> str:
    """A clause of two to four pieces; most pieces link two numbers with a connector or end a
    sentence with a number before a capitalized unit word, so that a word-aligned quote can
    start or end between the two."""
    connectors = ("ila", "ile", "veya", "-")
    fillers = ("Bildirim", "önceden yapılır", "acil halde", "süre", "ayrıca", "bedel", "ceza")
    if kind == "duration":
        numbers, units, capitals = ("10", "15", "30", "45", "60"), ("gün", "günlük"), ("Aylık",)
    elif kind == "amount":
        numbers, units, capitals = ("500.000", "1.000.000", "2.500.000"), ("TL",), ("Lira",)
    else:
        numbers, units, capitals = ("5", "10", "15", "25"), ("",), ()

    def value(number: str) -> str:
        return f"%{number}" if kind == "percent" else f"{number} {rng.choice(units)}"

    def piece() -> str:
        a, b, conn = rng.choice(numbers), rng.choice(numbers), rng.choice(connectors)
        shape = rng.randrange(6)
        if shape == 0:
            return rng.choice(fillers)
        if shape == 1:
            return value(a)
        if shape == 2:  # the unit-less number first
            return f"%{a} {conn} {b}" if kind == "percent" else f"{a} {conn} {value(b)}"
        if shape == 3:  # the unit-less number last
            return f"{a} {conn} %{b}" if kind == "percent" else f"{value(b)} {conn} {a}"
        if shape == 4:  # written without spaces: "15-30 gün", "%10-25"
            return f"%{a}-{b}" if kind == "percent" else f"{a}-{value(b)}"
        if not capitals:
            return value(a)
        capital = rng.choice(capitals)  # a sentence boundary before a capitalized unit word
        if rng.random() < 0.5:
            return (
                f"{value(b)} veya {a}. {capital} rapor {value('60' if kind == 'duration' else b)}"
            )
        return f"{a} ({value(b)}). {capital} rapor"

    return " ".join(piece() for _ in range(rng.randint(2, 4)))


def test_verify_compliant_narrow_quote_never_hides_a_clause_value_seeded() -> None:
    """Whatever the quote, a `compliant` finding whose quote matched is queued when the cited
    clause read as a whole, or the text before or after the match read on its own, holds a value
    of the rule's kind that breaks the rule, converts inexactly or cannot be read."""
    rng = random.Random(20261113)
    rules: list[tuple[Rule, Kind]] = [
        (AT_LEAST_30_DAYS, "duration"),
        (AT_MOST_45_DAYS, "duration"),
        (_rule("<=", 10, "%"), "percent"),
        (_rule(">=", 1_000_000, "TL"), "amount"),
    ]
    matched = only_whole = only_parts = 0
    for rule, kind in rules:
        check = rule.check
        assert check is not None
        for _ in range(40):
            text = _seeded_clause(rng, kind)
            clause = _clause(text)
            whole_bad = _breaks_or_unclear(text, kind, check)
            words = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
            for i, (start, _) in enumerate(words):
                for _, end in words[i:]:
                    quote = text[start:end]
                    span = verify_module._match_quote(quote, clause)
                    if span is None:
                        continue
                    matched += 1
                    parts_bad = _breaks_or_unclear(
                        text[: span[0]], kind, check
                    ) or _breaks_or_unclear(text[span[1] :], kind, check)
                    if not (whole_bad or parts_bad):
                        continue
                    finding = _finding(quote)
                    assert _run(finding, rule, text).needs_review, (text, quote)
                    # Only the outside check can queue a finding whose quote passes on its own.
                    if not verify_module._numeric_reasons(finding, check, text[span[0] : span[1]]):
                        only_whole += whole_bad and not parts_bad
                        only_parts += parts_bad and not whole_bad
    # The generator must build the cases each reading alone would miss, or the test proves
    # nothing: `only_whole` are the ones a part-by-part reading does not queue.
    assert matched > 3000
    assert only_whole > 40
    assert only_parts > 5


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
    # The rule has no check, so the draft alone queues the finding (proposal_unverifiable);
    # masked_value_in_clause is still not added to a non_compliant finding.
    assert _reasons(finding, LIABILITY_CAP, clause) == ["proposal_unverifiable"]
    without_draft = _finding(clause, status="non_compliant")
    assert _reasons(without_draft, LIABILITY_CAP, clause) == ["missing_proposal"]


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
        # "bir önceki" + unit names a past period: neither value nor unreadable (step 19g).
        ("bir önceki yılın", "duration", Extraction((), ())),
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
