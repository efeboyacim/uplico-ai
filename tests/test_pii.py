import random
import re
import time

import pytest

from src.prepare import pii
from src.prepare.pii import PLACEHOLDER, mask, unmask


def _tckn_valid(number: str) -> bool:
    if len(number) != 11 or not number.isdigit() or number[0] == "0":
        return False
    d = [int(ch) for ch in number]
    odd = d[0] + d[2] + d[4] + d[6] + d[8]
    even = d[1] + d[3] + d[5] + d[7]
    return d[9] == (odd * 7 - even) % 10 and d[10] == sum(d[:10]) % 10


def _iban_valid(iban: str) -> bool:
    moved = iban[4:] + iban[:4]
    digits = "".join(str(ord(ch) - ord("A") + 10) if ch.isalpha() else ch for ch in moved)
    return int(digits) % 97 == 1


TCKN_A = "10000000146"
TCKN_B = "12345678950"
TCKN_C = "98765432150"
TCKN_BAD = "12345678951"
IBAN = "TR330006100519786457841326"
IBAN_BAD = "TR340006100519786457841326"
IBAN_SPACED = "TR33 0006 1005 1978 6457 8413 26"


def test_fixtures_checksums_are_as_expected() -> None:
    assert all(_tckn_valid(n) for n in (TCKN_A, TCKN_B, TCKN_C))
    assert not _tckn_valid(TCKN_BAD)
    assert _iban_valid(IBAN)
    assert not _iban_valid(IBAN_BAD)


def _mask(text: str) -> tuple[str, dict[str, str]]:
    return mask(text, {})


# --- TCKN ---------------------------------------------------------------------------------------


def test_mask_valid_tckn_masked_and_bad_checksum_kept() -> None:
    masked, mapping = _mask(f"Kimlik {TCKN_A}, diğer numara {TCKN_BAD}.")
    assert masked == f"Kimlik [TCKN_1], diğer numara {TCKN_BAD}."
    assert TCKN_A not in masked
    assert mapping == {"[TCKN_1]": TCKN_A}


def test_mask_tckn_inside_longer_number_not_masked() -> None:
    text = f"Referans 9{TCKN_A}0."
    assert _mask(text) == (text, {})


# --- Amounts, durations, dates ------------------------------------------------------------------

_NON_PII = [
    "1.250.000 TL",
    "5.000.000,00 TL",
    "90 gün",
    "otuz (30) gün",
    "01.01.2026",
    "2026-01-01",
    "%15",
    "Madde 12",
    "01.01.2026 1.250 TL",
]


def test_mask_amounts_durations_dates_all_together_unchanged() -> None:
    text = (
        "Madde 12 - Bedel\nToplam bedel 1.250.000 TL, üst sınır 5.000.000,00 TL'dir. "
        "Ödeme 90 gün, itiraz otuz (30) gün içinde yapılır. Başlangıç 01.01.2026, "
        "bitiş 2026-01-01. Ceza %15. Avans 01.01.2026 1.250 TL olarak ödenir."
    )
    assert _mask(text) == (text, {})


@pytest.mark.parametrize("fragment", _NON_PII)
def test_mask_non_pii_fragment_unchanged(fragment: str) -> None:
    text = f"Sözleşmede {fragment} yazar."
    assert _mask(text) == (text, {})


# --- Phone --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phone",
    [
        "+90 532 123 45 67",
        "0212 123 45 67",
        "05321234567",
        "0532-123-45-67",
        "0532.123.45.67",
        "+905321234567",
    ],
)
def test_mask_phone_accepted_shapes_masked(phone: str) -> None:
    masked, mapping = _mask(f"Tel: {phone}.")
    assert masked == "Tel: [TELEFON_1]."
    assert mapping == {"[TELEFON_1]": phone}


@pytest.mark.parametrize(
    "text",
    [
        "Tel: 0532  123 45 67",
        "Tel: 0532 123 -45 67",
        "Tel: 90 532 123 45 67",
        "Tel: 5321234567",
    ],
)
def test_mask_phone_double_separator_or_no_prefix_not_masked(text: str) -> None:
    assert _mask(text) == (text, {})


# The area code in parentheses: after the prefix, or with the "0" inside the parentheses.
@pytest.mark.parametrize(
    "phone",
    [
        "0 (532) 123 45 67",
        "0(532) 123 45 67",
        "0 (532) 1234567",
        "0(532)1234567",
        "0 (212) 555 12 34",
        "+90 (533) 555 05 30",
        "+90(533) 555 05 30",
        "+90 (212) 555 12 34",
        "+90 (533) 555-05-30",
        "+90 (533) 555.05.30",
        "+90 (0532) 123 45 67",
        "(0532) 123 45 67",
        "(0212) 555 12 34",
        "(0212) 5551234",
        "(0212)5551234",
        "(0 212) 555 12 34",
        "+90 (0 532) 123 45 67",
    ],
)
def test_mask_phone_area_code_in_parentheses_masked(phone: str) -> None:
    text = f"Tel: {phone}, faks yok."
    masked, mapping = _mask(text)
    assert masked == "Tel: [TELEFON_1], faks yok."
    assert mapping == {"[TELEFON_1]": phone}
    assert unmask(masked, mapping) == text
    # Masking the masked text again finds nothing new.
    assert mask(masked, mapping) == (masked, mapping)


@pytest.mark.parametrize(
    "text",
    [
        # No prefix: the parentheses alone do not make a phone number (documented limit).
        "Tel: (532) 123 45 67",
        "Tel: (212) 555 12 34",
        # Not the 3-3-2-2 shape, a double separator, or a separator inside the parentheses.
        "Tel: 0 (532) 123 45",
        "Tel: 0 (53) 123 45 67",
        "Tel: 0 (5321) 123 45 67",
        "Tel: 0  (532) 123 45 67",
        "Tel: 0 (532)  123 45 67",
        "Tel: 0 ( 532) 123 45 67",
        "Tel: (0  212) 555 12 34",
        "Tel: 0 (532)) 123 45 67",
        # A digit or "+" right before the number, or a digit right after it.
        "Ref: 10 (532) 123 45 67",
        "Ref: 7(0212) 555 12 34",
        "Ref: +(0212) 555 12 34",
        "Ref: (0212) 555 12 345",
        # Amounts, clause references and dates written with parentheses.
        "Bedel 0 (sıfır) TL, ceza 100 (yüz) TL olur.",
        "Tutar (1.250.000) TL, vade (30) gün, oran (%15).",
        "Madde 10 (123) uyarınca 456 78 90 adet teslim edilir.",
        "Süre otuz (30) gün; teslim 01.01.2026 (15) 100 20 30.",
        "Toplam (0100) 200 30 TL.",
    ],
)
def test_mask_parentheses_without_a_prefixed_phone_shape_unchanged(text: str) -> None:
    assert _mask(text) == (text, {})


def test_mask_phone_in_parentheses_next_to_other_values() -> None:
    text = (
        "Yetkili: Ahmet Yılmaz, TCKN 10000000146, GSM 0 (532) 123 45 67, "
        "faks (0212) 555 12 34, cep 533 555 05 30, e-posta ahmet@ornek.com."
    )
    masked, mapping = _mask(text)
    assert masked == (
        "Yetkili: [KISI_1], TCKN [TCKN_1], GSM [TELEFON_1], "
        "faks [TELEFON_2], cep [TELEFON_3], e-posta [EPOSTA_1]."
    )
    assert mapping["[TELEFON_1]"] == "0 (532) 123 45 67"
    assert mapping["[TELEFON_2]"] == "(0212) 555 12 34"
    assert unmask(masked, mapping) == text


def test_mask_phone_in_parentheses_same_spelling_same_placeholder_other_spelling_new() -> None:
    text = "Tel 0 (532) 123 45 67; yine 0 (532) 123 45 67; ayrıca 0532 123 45 67."
    masked, mapping = _mask(text)
    assert masked == "Tel [TELEFON_1]; yine [TELEFON_1]; ayrıca [TELEFON_2]."
    assert mapping == {"[TELEFON_1]": "0 (532) 123 45 67", "[TELEFON_2]": "0532 123 45 67"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A parenthesized spelling that would start earlier and end inside a prefixed phone.
        ("(0212)123 0532 123 45 67", "(0212)123 [TELEFON_1]"),
        ("(0 212) 212 0532 123 45 67", "(0 212) 212 [TELEFON_1]"),
        ("0 (212) 555 +905321234567", "0 (212) 555 [TELEFON_1]"),
        # ... or inside an e-mail address.
        ("(0212) 555 12 34-info@ornek.com", "(0212) 555 12 [EPOSTA_1]"),
    ],
)
def test_mask_phone_in_parentheses_never_takes_text_from_an_older_value(
    text: str, expected: str
) -> None:
    # The older patterns' value keeps its place; the result is the one before this spelling.
    masked, mapping = _mask(text)
    assert masked == expected
    assert unmask(masked, mapping) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A spaced TCKN (17b, weak) that starts with the last group of a parenthesized spelling:
        # 46583829454 and 21473873960 pass the checksum. The TCKN keeps its place.
        ("Tel: 0 (532) 378 00 46 58 38 29 45 4", "Tel: 0 (532) 378 00 [TCKN_1]"),
        ("TCKN: (0212) 214 73 87 39 60", "TCKN: (0212) [TCKN_1]"),
        ("TCKN: 0 (212) 214 73 87 39 60", "TCKN: 0 (212) [TCKN_1]"),
    ],
)
def test_mask_phone_in_parentheses_never_takes_text_from_a_weak_value(
    text: str, expected: str
) -> None:
    masked, mapping = _mask(text)
    assert masked == expected
    assert unmask(masked, mapping) == text
    assert mask(masked, mapping) == (masked, mapping)


@pytest.mark.parametrize(
    ("text", "first", "second"),
    [
        (
            "(0212) 555 12 34 533 555 05 30",
            "[TELEFON_1] 533 555 05 30",
            "[TELEFON_1] [TELEFON_2]",
        ),
        (
            "Tel: 0 (532) 123 45 67,214 738 739 60",
            "Tel: [TELEFON_1],214 738 739 60",
            "Tel: [TELEFON_1],[TCKN_1]",
        ),
    ],
)
def test_mask_number_right_after_phone_in_parentheses_found_only_by_masking_again(
    text: str, first: str, second: str
) -> None:
    # Known limit, pinned: a mobile number without prefix after one separator, or a spaced TCKN
    # after a comma, is found only when the digit before it belongs to a value `_discover`
    # itself found. A parenthesized phone is placed after discovery, so the first pass leaves
    # the number (as it was left before this spelling); masking the masked text finds it.
    masked, mapping = _mask(text)
    assert masked == first
    assert mask(masked, mapping)[0] == second
    # With any other separator the number is found in the first pass.
    assert _mask("Tel: (0212) 555 12 34, GSM: 533 555 05 30")[0] == (
        "Tel: [TELEFON_1], GSM: [TELEFON_2]"
    )


def _mask_threaded(text: str) -> list[tuple[str, str, dict[str, str]]]:
    """The call pattern of `prepare_contract`: the whole text first, then each line with the
    mapping threaded from call to call. Returns (line, masked line, mapping after it)."""
    _, mapping = mask(text, {})
    out: list[tuple[str, str, dict[str, str]]] = []
    for line in text.split("\n"):
        masked, mapping = mask(line, mapping)
        out.append((line, masked, mapping))
    return out


def test_mask_phone_in_parentheses_in_the_mapping_never_takes_text_in_a_later_call() -> None:
    # 30642792834 passes the checksum. The mapping of the first call holds the parenthesized
    # phone; in the later calls the spaced TCKN keeps its place all the same.
    text = (
        "Tedarikçi irtibat: (0212) 555 12 34 533 555 05 30\n"
        "Yetkilinin hattı ve kimliği: 533 555 05 30 64 27 928 34\n"
        "Kimlik: (0212) 214 73 87 39 60"
    )
    lines = _mask_threaded(text)
    assert [masked for _, masked, _ in lines] == [
        "Tedarikçi irtibat: [TELEFON_1] 533 555 05 30",
        "Yetkilinin hattı ve kimliği: 533 555 05 [TCKN_1]",
        "Kimlik: (0212) [TCKN_2]",
    ]
    # A draft edited later, masked with the review's mapping (src/api.py).
    mapping = lines[-1][2]
    draft = "Yeni metin: 533 555 05 30 64 27 928 34 numaralı kimlik, hat (0212) 555 12 34."
    assert mask(draft, mapping)[0] == (
        "Yeni metin: 533 555 05 [TCKN_1] numaralı kimlik, hat [TELEFON_1]."
    )


def test_mask_phone_in_parentheses_after_a_value_ending_in_zero_found_first_pass() -> None:
    # "0 (0212) 555 12 34" would start at the last digit of the TCKN (10000000450 is valid) and
    # lose to it; the candidate that starts at "(" is found all the same.
    text = "TCKN 10 00 00 00 45 0 (0212) 555 12 34 numaralı hat."
    masked, mapping = _mask(text)
    assert masked == "TCKN [TCKN_1] [TELEFON_1] numaralı hat."
    assert mapping == {"[TCKN_1]": "10 00 00 00 45 0", "[TELEFON_1]": "(0212) 555 12 34"}
    assert mask(masked, mapping) == (masked, mapping)


@pytest.mark.parametrize(
    "text",
    [
        # A rate or a range in parentheses before an unseparated amount is not an area code.
        "Katsayı (0.125) 1250000 TL üzerinden hesaplanır.",
        "Puan aralığı (0-100) 2500000 TL bedelin yüzdesi olarak uygulanır.",
        "Tel: (0.212) 555 12 34",
        "Tel: (0-212) 555 12 34",
        "Tel: +90 (0.532) 123 45 67",
    ],
)
def test_mask_dot_or_hyphen_after_zero_inside_parentheses_is_not_an_area_code(text: str) -> None:
    assert _mask(text) == (text, {})


def test_mask_phone_in_parentheses_is_placed_last_not_weak() -> None:
    for value in ("(0212) 555 12 34", "0 (532) 123 45 67", "+90 (533) 555 05 30"):
        assert pii._in_parentheses("TELEFON", value)
        assert not pii._is_weak("TELEFON", value)
    for value in ("0532 123 45 67", "+90 532 123 45 67", "533 555 05 30"):
        assert not pii._in_parentheses("TELEFON", value)


def test_mask_unclosed_parenthesis_before_a_phone_masked_as_before() -> None:
    # Not a parenthesized area code; the older patterns still find the number after "(".
    assert _mask("Tel: (0212 555 12 34")[0] == "Tel: ([TELEFON_1]"
    assert _mask("Tel: 0 (532 123 45 67")[0] == "Tel: 0 ([TELEFON_1]"


# --- IBAN ---------------------------------------------------------------------------------------


def test_mask_iban_valid_masked() -> None:
    masked, mapping = _mask(f"IBAN: {IBAN}.")
    assert masked == "IBAN: [IBAN_1]."
    assert mapping == {"[IBAN_1]": IBAN}


@pytest.mark.parametrize(
    "iban",
    [
        IBAN_BAD,
        "TR34 0006 1005 1978 6457 8413 26",  # grouped, wrong checksum
        "TR33  0006 1005 1978 6457 8413 26",  # two spaces
        "TR33 0006 1005 1978\n6457 8413 26",  # split over two lines (documented limit)
        "TR33-0006-1005-1978-6457-8413-26",
    ],
)
def test_mask_iban_bad_checksum_double_space_line_break_or_hyphen_not_masked(iban: str) -> None:
    text = f"IBAN: {iban}"
    assert _mask(text) == (text, {})


# --- E-mail -------------------------------------------------------------------------------------


def test_mask_email_masked_trailing_period_kept() -> None:
    masked, mapping = _mask("Bildirim adresi ahmet.yilmaz99@ornek.com.tr.")
    assert masked == "Bildirim adresi [EPOSTA_1]."
    assert mapping == {"[EPOSTA_1]": "ahmet.yilmaz99@ornek.com.tr"}


def test_mask_email_with_tckn_digits_single_placeholder() -> None:
    masked, mapping = _mask(f"Adres: {TCKN_A}@ornek.com")
    assert masked == "Adres: [EPOSTA_1]"
    assert mapping == {"[EPOSTA_1]": f"{TCKN_A}@ornek.com"}


# --- Names --------------------------------------------------------------------------------------


def test_mask_name_label_and_body_mention_same_placeholder() -> None:
    masked, mapping = _mask("Ad Soyad: Ahmet Yılmaz\nTeslimat Ahmet Yılmaz'ın onayıyla yapılır.")
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz"}
    assert masked == "Ad Soyad: [KISI_1]\nTeslimat [KISI_1]'ın onayıyla yapılır."


def test_mask_name_trailing_title_kept_unmasked() -> None:
    masked, mapping = _mask("Yetkili: Ahmet Yılmaz Genel Müdür\nBildirim Ahmet Yılmaz'a yapılır.")
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz"}
    assert masked == "Yetkili: [KISI_1] Genel Müdür\nBildirim [KISI_1]'a yapılır."


def test_mask_name_leading_title_kept_unmasked() -> None:
    masked, mapping = _mask(
        "Satıcı'yı temsilen Yönetim Kurulu Başkanı Ahmet Yılmaz\nOnay Ahmet Yılmaz verir."
    )
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz"}
    assert masked == ("Satıcı'yı temsilen Yönetim Kurulu Başkanı [KISI_1]\nOnay [KISI_1] verir.")


def test_mask_name_stops_at_next_label_tckn_masked_separately() -> None:
    masked, mapping = _mask(f"Ad Soyad: Ahmet Yılmaz TCKN: {TCKN_A}")
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz", "[TCKN_1]": TCKN_A}
    assert masked == "Ad Soyad: [KISI_1] TCKN: [TCKN_1]"


def test_mask_name_stops_at_form_words() -> None:
    masked, mapping = _mask("Ad Soyad: Ahmet Yılmaz İmza Tarih")
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz"}
    assert masked == "Ad Soyad: [KISI_1] İmza Tarih"


def test_mask_name_stops_at_hyphenated_form_word() -> None:
    masked, mapping = _mask("Ad Soyad: Ahmet Yılmaz E-posta: a@ornek.com")
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz", "[EPOSTA_1]": "a@ornek.com"}
    assert masked == "Ad Soyad: [KISI_1] E-posta: [EPOSTA_1]"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Ad Soyad: Ahmet Yılmaz Kaşe", "Ad Soyad: [KISI_1] Kaşe"),
        ("Ad Soyad: Ahmet Yılmaz, Satış Sorumlusu", "Ad Soyad: [KISI_1], Satış Sorumlusu"),
        ("Ad Soyad: Ahmet Yılmaz (Satıcı Temsilcisi)", "Ad Soyad: [KISI_1] (Satıcı Temsilcisi)"),
        ("Ad Soyad: Ahmet Yılmaz imzalamıştır", "Ad Soyad: [KISI_1] imzalamıştır"),
        ("Ad Soyad: Ahmet Yılmaz\r\nGenel Hükümler", "Ad Soyad: [KISI_1]\r\nGenel Hükümler"),
    ],
)
def test_mask_name_run_stops_value_is_name_only(line: str, expected: str) -> None:
    masked, mapping = _mask(line)
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz"}
    assert masked == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            f"Ad Soyad: Ahmet Yılmaz TCKN: {TCKN_A}\nTeslimat Ahmet Yılmaz'ın onayıyla.",
            "Ad Soyad: [KISI_1] TCKN: [TCKN_1]\nTeslimat [KISI_1]'ın onayıyla.",
        ),
        (
            "Ad Soyad: Ahmet Yılmaz İmza Tarih\nTeslimat Ahmet Yılmaz'ın onayıyla.",
            "Ad Soyad: [KISI_1] İmza Tarih\nTeslimat [KISI_1]'ın onayıyla.",
        ),
    ],
)
def test_mask_name_stops_at_label_or_form_word_body_mention_masked(
    text: str, expected: str
) -> None:
    masked, mapping = _mask(text)
    assert mapping["[KISI_1]"] == "Ahmet Yılmaz"
    assert masked == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A dotted abbreviation is not a letters-only token: the run stops before it.
        (
            f"Ad Soyad: Ahmet Yılmaz T.C. Kimlik No: {TCKN_A}\nAhmet Yılmaz'ın",
            "Ad Soyad: [KISI_1] T.C. Kimlik No: [TCKN_1]\n[KISI_1]'ın",
        ),
        # A second two-word label on the same line is the next label, not part of the name.
        (
            "Ad Soyad: Ahmet Yılmaz\tAd Soyad: Mehmet Kaya\nAhmet Yılmaz'ın",
            "Ad Soyad: [KISI_1]\tAd Soyad: [KISI_2]\n[KISI_1]'ın",
        ),
        (
            "Adı Soyadı: Ahmet Yılmaz    Adı Soyadı: Mehmet Kaya\nAhmet Yılmaz'ın",
            "Adı Soyadı: [KISI_1]    Adı Soyadı: [KISI_2]\n[KISI_1]'ın",
        ),
    ],
)
def test_mask_name_stops_at_dotted_abbreviation_or_second_label(text: str, expected: str) -> None:
    masked, mapping = _mask(text)
    assert mapping["[KISI_1]"] == "Ahmet Yılmaz"
    assert masked == expected


@pytest.mark.parametrize(
    ("text", "expected", "value"),
    [
        # ASCII "I" (str.upper() of "i") and missing diacritics still match the closed lists.
        ("YETKILI: AHMET YILMAZ\nAHMET YILMAZ", "YETKILI: [KISI_1]\n[KISI_1]", "AHMET YILMAZ"),
        ("AD SOYAD: AHMET YILMAZ TARIH", "AD SOYAD: [KISI_1] TARIH", "AHMET YILMAZ"),
        ("Şirketi TEMSILEN Ahmet Yılmaz", "Şirketi TEMSILEN [KISI_1]", "Ahmet Yılmaz"),
        (
            "Yetkili: AHMET YILMAZ ICRA KURULU UYESI",
            "Yetkili: [KISI_1] ICRA KURULU UYESI",
            "AHMET YILMAZ",
        ),
        ("Ad Soyad: Ahmet Yılmaz Imza Tarih", "Ad Soyad: [KISI_1] Imza Tarih", "Ahmet Yılmaz"),
        ("Ad Soyad: Ahmet Yılmaz IMZA", "Ad Soyad: [KISI_1] IMZA", "Ahmet Yılmaz"),
        ("Ad Soyad: Ahmet Yılmaz Kase", "Ad Soyad: [KISI_1] Kase", "Ahmet Yılmaz"),
        ("Ad Soyad: Ahmet Yılmaz Muhur", "Ad Soyad: [KISI_1] Muhur", "Ahmet Yılmaz"),
        # No-break spaces from DOCX/PDF extraction separate the label and the words.
        ("Ad Soyad:\u00a0Ahmet\u00a0Yılmaz", "Ad Soyad:\u00a0[KISI_1]", "Ahmet\u00a0Yılmaz"),
        # A decomposed "İ" (I + combining dot) stays inside the word.
        ("Ad Soyad: Ahmet Yi\u0307lmaz", "Ad Soyad: [KISI_1]", "Ahmet Yi\u0307lmaz"),
    ],
)
def test_mask_name_ascii_spelling_nbsp_and_decomposed_letters(
    text: str, expected: str, value: str
) -> None:
    masked, mapping = _mask(text)
    assert mapping == {"[KISI_1]": value}
    assert masked == expected


@pytest.mark.parametrize(
    "text",
    [
        "Yetkili: Tedarik Direktörü Ahmet Yılmaz",
        "Yetkili: Ali Veli Genel Müdür Ahmet Yılmaz",
    ],
)
def test_mask_leading_title_second_pass_changes_nothing(text: str) -> None:
    masked, mapping = _mask(text)
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz"}
    assert mask(masked, mapping) == (masked, mapping)


_DIGITS_TO_LETTERS = str.maketrans("0123456789", "abcdefghij")


def _valid_tckns(count: int) -> list[str]:
    numbers: list[str] = []
    base = 100000000
    while len(numbers) < count:
        d = [int(ch) for ch in str(base)]
        d10 = ((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10
        number = f"{base}{d10}{(sum(d) + d10) % 10}"
        assert _tckn_valid(number)
        numbers.append(number)
        base += 1
    return numbers


@pytest.mark.parametrize(
    "text",
    [
        " ".join(_valid_tckns(16600)),
        "".join(f"Ad Soyad: A{f'{i:05d}'.translate(_DIGITS_TO_LETTERS)} B\n" for i in range(11000)),
        "Temsilen Ahmet " * 13000,
        "Yetkili: " + "Ahmet " * 33000,
    ],
    ids=["many-tckn", "many-names", "repeated-label", "long-run"],
)
def test_mask_200kb_adversarial_input_fast(text: str) -> None:
    started = time.perf_counter()
    masked, mapping = _mask(text)
    assert time.perf_counter() - started < 5
    assert unmask(masked, mapping) == text


def test_mask_name_unknown_title_five_words_whole_run_one_value() -> None:
    line = "Yetkili: Ahmet Mehmet Yılmaz Tedarik Zinciri Direktörü"
    masked, mapping = _mask(line + "\nOnay Ahmet Mehmet Yılmaz verir.")
    value = "Ahmet Mehmet Yılmaz Tedarik Zinciri Direktörü"
    assert mapping == {"[KISI_1]": value}
    assert masked.splitlines()[0] == "Yetkili: [KISI_1]"
    # Fail closed: the labelled line does not leak, the bare body mention is a documented limit.
    assert masked.splitlines()[1] == "Onay Ahmet Mehmet Yılmaz verir."


def test_mask_name_title_first_single_word_left_whole_run_one_value() -> None:
    masked, mapping = _mask("Yetkili: Tedarik Direktörü Ahmet")
    assert mapping == {"[KISI_1]": "Tedarik Direktörü Ahmet"}
    assert masked == "Yetkili: [KISI_1]"


def test_mask_name_unknown_modifiers_stay_in_value_name_not_leaked() -> None:
    # After stripping "Direktörü" four words remain, so the rule takes them as the name.
    masked, mapping = _mask("Yetkili: Ahmet Yılmaz Tedarik Zinciri Direktörü")
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz Tedarik Zinciri"}
    assert masked == "Yetkili: [KISI_1] Direktörü"
    assert "Ahmet" not in masked
    assert "Yılmaz" not in masked


@pytest.mark.parametrize(
    "text",
    [
        "Yetkili: ABC Teknoloji A.Ş.",
        "Alıcı'yı temsilen Globex Lojistik Limited Şirketi",
        "Yetkili: Örnek Yazılım Ltd. Şti.",
        "Yetkili: Genel Müdür",
        "Yetkili: Ahmet",
    ],
)
def test_mask_company_title_only_or_single_word_nothing_masked(text: str) -> None:
    assert _mask(text) == (text, {})


def test_mask_known_name_not_matched_inside_longer_word() -> None:
    mapping = {"[KISI_1]": "Ahmet Yılmaz"}
    text = "Ahmet Yılmazer ve 2Ahmet Yılmaz kayıtlı."
    assert mask(text, mapping) == (text, mapping)


@pytest.mark.parametrize(
    ("text", "expected", "value"),
    [
        ("AD SOYAD: AHMET YILMAZ", "AD SOYAD: [KISI_1]", "AHMET YILMAZ"),
        ("**Ad Soyad:** Ahmet Yılmaz", "**Ad Soyad:** [KISI_1]", "Ahmet Yılmaz"),
        ("ADI SOYADI: Ayşe Kaya", "ADI SOYADI: [KISI_1]", "Ayşe Kaya"),
        ("YETKİLİ: Av. Ayşe Kaya", "YETKİLİ: Av. [KISI_1]", "Ayşe Kaya"),
    ],
)
def test_mask_name_label_case_and_markdown_variants(text: str, expected: str, value: str) -> None:
    masked, mapping = _mask(text)
    assert mapping == {"[KISI_1]": value}
    assert masked == expected


# --- Placeholders and mapping -------------------------------------------------------------------


def test_mask_same_value_twice_one_placeholder_and_order_of_appearance() -> None:
    masked, mapping = _mask(f"{TCKN_B} ve {TCKN_A}; yine {TCKN_B}.")
    assert masked == "[TCKN_1] ve [TCKN_2]; yine [TCKN_1]."
    assert mapping == {"[TCKN_1]": TCKN_B, "[TCKN_2]": TCKN_A}


def test_mask_existing_mapping_reused_and_numbering_continues() -> None:
    existing = {"[KISI_1]": "Ahmet Yılmaz", "[TCKN_1]": TCKN_A}
    snapshot = dict(existing)
    masked, mapping = mask(f"Ahmet Yılmaz yeni kimlik {TCKN_C} bildirdi.", existing)
    assert masked == "[KISI_1] yeni kimlik [TCKN_2] bildirdi."
    assert mapping == {**snapshot, "[TCKN_2]": TCKN_C}
    assert len(mapping) == 3
    assert existing == snapshot
    assert mapping is not existing


_MIXED = (
    "Ad Soyad: Ahmet Yılmaz TCKN: 10000000146\n"
    "Tel: +90 532 123 45 67, e-posta: ahmet@ornek.com.\n"
    "IBAN: TR330006100519786457841326\n"
    "Bedel 1.250.000 TL, ödeme 90 gün içinde; Ahmet Yılmaz'ın onayı gerekir."
)


def test_mask_mixed_text_every_kind_masked_and_unmask_restores() -> None:
    masked, mapping = _mask(_MIXED)
    assert masked == (
        "Ad Soyad: [KISI_1] TCKN: [TCKN_1]\n"
        "Tel: [TELEFON_1], e-posta: [EPOSTA_1].\n"
        "IBAN: [IBAN_1]\n"
        "Bedel 1.250.000 TL, ödeme 90 gün içinde; [KISI_1]'ın onayı gerekir."
    )
    for value in mapping.values():
        assert value not in masked
    assert unmask(masked, mapping) == _MIXED


def test_unmask_unknown_placeholder_left_as_is() -> None:
    assert unmask("[KISI_1] ve [KISI_9]", {"[KISI_1]": "Ahmet"}) == "Ahmet ve [KISI_9]"


def test_unmask_value_looking_like_placeholder_not_expanded_twice() -> None:
    mapping = {"[KISI_1]": "[KISI_2]", "[KISI_2]": "Ayşe"}
    assert unmask("[KISI_1]", mapping) == "[KISI_2]"


def test_mask_already_masked_text_unchanged() -> None:
    masked, mapping = _mask(_MIXED + "\nYetkili: Ahmet Mehmet Yılmaz Tedarik Zinciri Direktörü")
    assert mask(masked, mapping) == (masked, mapping)


def test_mask_existing_placeholder_in_text_untouched() -> None:
    text = "Taraf [KISI_7] ile [TCKN_3] aynen kalır."
    assert _mask(text) == (text, {})


@pytest.mark.parametrize(
    "mapping",
    [
        {"KISI_1": "Ahmet Yılmaz"},
        {"[ADRES_1]": "Ahmet Yılmaz"},
        {"Ahmet Yılmaz": "[KISI_1]"},
        {"[KISI_1]": ""},
    ],
)
def test_mask_malformed_mapping_raises_without_values(mapping: dict[str, str]) -> None:
    with pytest.raises(ValueError) as excinfo:
        mask("Ahmet Yılmaz", mapping)
    message = str(excinfo.value)
    assert "Ahmet" not in message
    assert "KISI" not in message


@pytest.mark.parametrize(
    "line",
    [
        "Yetkili: Acme Sanayi ve Ticaret A.Ş.",
        "temsilen Globex Lojistik ve Depolama Limited Şirketi",
        "Yetkili: Yönetim Kurulu",
        "temsilen İcra Kurulu",
    ],
)
def test_mask_company_with_ve_or_modifier_only_run_nothing_masked(line: str) -> None:
    text = f"{line}\nYönetim Kurulu ve Acme Sanayi kararı geçerlidir."
    assert mask(text, {}) == (text, {})


def test_mask_name_followed_by_ve_and_second_person_still_masked() -> None:
    masked, mapping = mask("temsilen Ahmet Yılmaz ve Ayşe Kaya imzalamıştır.", {})
    assert "Ahmet Yılmaz" not in masked
    assert mapping["[KISI_1]"] == "Ahmet Yılmaz"


_BODY = "Madde 5. Tedarikçi adına Ahmet Yılmaz bildirimleri yapar.\n\nİMZALAR\n"


@pytest.mark.parametrize("signature", ["AHMET YILMAZ", "Ahmet YILMAZ"])
def test_mask_name_labelled_in_other_case_body_spelling_masked(signature: str) -> None:
    masked, mapping = mask(_BODY + f"Ad Soyad: {signature}\nİmza:", {})
    assert "Ahmet Yılmaz" not in masked
    assert signature not in masked
    assert sorted(mapping.values()) == sorted(["Ahmet Yılmaz", signature])
    assert unmask(masked, mapping) == _BODY + f"Ad Soyad: {signature}\nİmza:"


def test_mask_name_labelled_once_unlabelled_upper_case_signature_masked() -> None:
    text = "Yetkili: Ahmet Yılmaz\n" + _BODY + "AHMET YILMAZ\nİmza:"
    masked, mapping = mask(text, {})
    assert "Ahmet Yılmaz" not in masked
    assert "AHMET YILMAZ" not in masked
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz", "[KISI_2]": "AHMET YILMAZ"}
    assert unmask(masked, mapping) == text
    assert mask(masked, mapping) == (masked, mapping)


@pytest.mark.parametrize("gap", ["  ", "\n", "\r\n", "\u00a0", " \t "])
def test_mask_known_name_with_other_whitespace_between_words_masked(gap: str) -> None:
    text = f"Yetkili: Ahmet Yılmaz\nGövdede Ahmet{gap}Yılmaz'ın imzası aranır."
    masked, mapping = mask(text, {})
    assert "Yılmaz" not in masked
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz", "[KISI_2]": f"Ahmet{gap}Yılmaz"}
    assert unmask(masked, mapping) == text


def test_mask_name_from_existing_mapping_other_case_gets_new_placeholder() -> None:
    masked, mapping = mask("İmza: AHMET YILMAZ", {"[KISI_1]": "Ahmet Yılmaz"})
    assert masked == "İmza: [KISI_2]"
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz", "[KISI_2]": "AHMET YILMAZ"}


def test_mask_name_other_case_inside_longer_word_not_masked() -> None:
    text = "Yetkili: Ahmet Yılmaz\nAHMET YILMAZER ayrı bir kişidir."
    masked, _ = mask(text, {})
    assert "AHMET YILMAZER" in masked


def test_mask_unlabelled_signature_line_name_masked_everywhere() -> None:
    # Step 19c: a line that is only a name, directly above "İmza", is a name; it is then searched
    # in the whole text like a labelled one, so the body spelling is masked too.
    text = _BODY + "AHMET YILMAZ\nİmza:"
    masked, mapping = mask(text, {})
    assert masked == (
        "Madde 5. Tedarikçi adına [KISI_1] bildirimleri yapar.\n\nİMZALAR\n[KISI_2]\nİmza:"
    )
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz", "[KISI_2]": "AHMET YILMAZ"}
    assert unmask(masked, mapping) == text
    assert mask(masked, mapping) == (masked, mapping)


# --- Name discovery from an e-mail's local part (step 19c) --------------------------------------


@pytest.mark.parametrize(
    ("text", "expected", "name"),
    [
        (
            "Bildirim Kemal Uçar'a (kemal.ucar@ornek.com) yapılır.",
            "Bildirim [KISI_1]'a ([EPOSTA_1]) yapılır.",
            "Kemal Uçar",
        ),
        (
            "Hakan Töre'ye hakan.tore@ornek.com üzerinden ulaşılır.",
            "[KISI_1]'ye [EPOSTA_1] üzerinden ulaşılır.",
            "Hakan Töre",
        ),
        (
            "Depo sorumlusu Elif Soylu aranır (e-posta: elif.soylu@ornek.com.tr).",
            "Depo sorumlusu [KISI_1] aranır (e-posta: [EPOSTA_1]).",
            "Elif Soylu",
        ),
        (
            "HAKAN TÖRE (hakan.tore@ornek.com)",
            "[KISI_1] ([EPOSTA_1])",
            "HAKAN TÖRE",
        ),
        (
            "Sorumlu: ahmet_can-yilmaz@ornek.com, Ahmet Can Yılmaz",
            "Sorumlu: [EPOSTA_1], [KISI_1]",
            "Ahmet Can Yılmaz",
        ),
    ],
    ids=["ucar-Uçar", "tore-Töre", "body-after-title", "upper-case", "three-parts"],
)
def test_mask_name_spelled_by_email_on_same_line_masked(
    text: str, expected: str, name: str
) -> None:
    masked, mapping = mask(text, {})
    assert masked == expected
    assert mapping["[KISI_1]"] == name
    assert unmask(masked, mapping) == text
    assert mask(masked, mapping) == (masked, mapping)


def test_mask_name_found_by_email_masked_on_other_lines_too() -> None:
    # Contract 5: the e-mail stands next to the name only in clause 11; clause 12 names her again.
    text = (
        "Depo sorumlusu Elif Soylu aranır (e-posta: elif.soylu@ornek.com).\n"
        "Giriş yetkisi Elif Soylu adına, ELİF SOYLU imzasıyla tanımlanır."
    )
    masked, mapping = mask(text, {})
    assert masked == (
        "Depo sorumlusu [KISI_1] aranır (e-posta: [EPOSTA_1]).\n"
        "Giriş yetkisi [KISI_1] adına, [KISI_2] imzasıyla tanımlanır."
    )
    assert mapping == {
        "[KISI_1]": "Elif Soylu",
        "[EPOSTA_1]": "elif.soylu@ornek.com",
        "[KISI_2]": "ELİF SOYLU",
    }


@pytest.mark.parametrize(
    "text",
    [
        "Bize info@ornek.com adresinden ulaşın; Info Masası yanıtlar.",
        "Bildirim a.yilmaz@ornek.com adresine, Sn. A. Yılmaz'a yapılır.",
        "hakan töre adına hakan.tore@ornek.com kullanılır.",
        "Ahmet Yılmaz İnşaat A.Ş. ile yazışma ahmet.yilmaz@ornek.com üzerinden yapılır.",
        "Ahmet Yılmaz\nahmet.yilmaz@ornek.com adresine yazılır.",
        "Satış Birimi siparişleri satis.birimi@ornek.com adresine iletir.",
        "Destek Ekibi destek.ekibi@ornek.com üzerinden ulaşılır.",
        "Bilgi İşlem Departmanı bilgi.islem.departmani@ornek.com",
        "Hukuk Müdürlüğü hukuk.mudurlugu@ornek.com",
        "Teknik Servisi teknik.servisi@ornek.com",
        "Hukuk Müdürü hukuk.muduru@ornek.com",
        "Genel Müdür genel.mudur@ornek.com",
        "Satın Alma satin.alma@ornek.com",
    ],
    ids=[
        "one-part-box",
        "initial",
        "lower-case",
        "company",
        "other-line",
        "unit-birimi",
        "unit-ekibi",
        "unit-departmani",
        "unit-mudurlugu",
        "unit-servisi",
        "title-head",
        "title-head-genel",
        "modifiers-only",
    ],
)
def test_mask_email_spelling_no_person_only_email_masked(text: str) -> None:
    masked, mapping = mask(text, {})
    assert list(mapping) == ["[EPOSTA_1]"]
    assert unmask(masked, mapping) == text


# --- Unlabelled signature line (step 19c) -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("## İmzalar\n\nKemal Uçar\nİmza:", "## İmzalar\n\n[KISI_1]\nİmza:"),
        ("Kemal Uçar\nIMZA", "[KISI_1]\nIMZA"),
        ("Kemal Uçar\nİmza / Kaşe:", "[KISI_1]\nİmza / Kaşe:"),
        ("   Kemal Uçar  \n   İmza: ____", "   [KISI_1]  \n   İmza: ____"),
        ("Kemal Uçar\r\nİmza:", "[KISI_1]\r\nİmza:"),
    ],
    ids=["imza-colon", "imza-upper", "imza-kase", "indented", "crlf"],
)
def test_mask_unlabelled_signature_line_masked(text: str, expected: str) -> None:
    masked, mapping = mask(text, {})
    assert masked == expected
    assert mapping == {"[KISI_1]": "Kemal Uçar"}
    assert unmask(masked, mapping) == text
    assert mask(masked, mapping) == (masked, mapping)


@pytest.mark.parametrize(
    "text",
    [
        "Kemal\nİmza:",
        "Kemal Uçar\n\nİmza:",
        "Genel Müdür\nİmza:",
        "Alıcı Yetkilisi\nİmza:",
        "Satın Alma\nİmza:",
        "Tedarikçi Kaşesi\nİmza:",
        "Şirket Mührü\nİmza:",
        "Acme Teknoloji A.Ş.\nİmza:",
        "Kemal Uçar:\nİmza:",
        "Kemal Uçar\nİmzası alınır.",
        "Kemal Uçar 0532\nİmza:",
        "Satış Birimi\nİmza:",
        "Destek Ekibi\nİmza:",
        "Tedarikçi Adına\nİmza:",
        "Alıcı Adına\nİmza:",
        "Ad Soyad\nİmza:",
        "ADI SOYADI\nİmza:",
    ],
    ids=[
        "single-word",
        "blank-line-between",
        "title-only",
        "title-head-last",
        "modifiers-only",
        "kasesi",
        "muhru",
        "company-marker",
        "colon",
        "imzasi",
        "digit-on-line",
        "unit-birimi",
        "unit-ekibi",
        "tedarikci-adina",
        "alici-adina",
        "label-without-colon",
        "label-without-colon-upper",
    ],
)
def test_mask_signature_line_not_a_name_nothing_masked(text: str) -> None:
    assert mask(text, {}) == (text, {})


def test_mask_signature_form_words_only_on_signature_path_label_path_unchanged() -> None:
    # "Kaşesi" is a form word only for an unlabelled signature line; after a label the run is
    # cut as before step 19c.
    masked, mapping = mask("Yetkili: Ahmet Yılmaz Kaşesi\nİmza:", {})
    assert masked == "Yetkili: [KISI_1]\nİmza:"
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz Kaşesi"}


def test_mask_two_word_company_on_signature_line_masked_documented_limit() -> None:
    # Documented in agent_design.md "PII maskeleme": a company name without a company marker
    # looks like a name on a signature line.
    assert mask("Acme Teknoloji\nİmza:", {}) == (
        "[KISI_1]\nİmza:",
        {"[KISI_1]": "Acme Teknoloji"},
    )


@pytest.mark.parametrize(
    "caption",
    [
        "İŞ SAHİBİ",
        "HİZMET SAĞLAYICI",
        "Hizmet Alan",
        "Hizmet Veren",
        "Kiraya Veren",
        "TEDARİKÇİ FİRMA",
        "Satıcı Firma",
        "Alıcı Firma",
        "ALT YÜKLENİCİ",
        "Onaylayan Taraf",
        "Tedarikçi Ünvanı",
        "Müşteri Temsilci",
    ],
)
def test_mask_party_role_caption_on_signature_line_nothing_masked(caption: str) -> None:
    text = f"Madde 2. Teslim 30 gün içinde yapılır.\n\n{caption}\nİmza / Kaşe:"
    assert mask(text, {}) == (text, {})


@pytest.mark.parametrize("surname", ["Alan", "Veren"])
def test_mask_role_pair_word_alone_is_a_surname_masked(surname: str) -> None:
    masked, mapping = mask(f"Kemal {surname}\nİmza:", {})
    assert masked == "[KISI_1]\nİmza:"
    assert mapping == {"[KISI_1]": f"Kemal {surname}"}


@pytest.mark.parametrize(("uses", "masked_name"), [(2, True), (3, False)])
def test_mask_signature_caption_used_three_times_elsewhere_is_defined_term(
    uses: int, masked_name: bool
) -> None:
    # Any case and any whitespace between the words count as a use; the signature line itself
    # does not.
    sentences = [
        "Kalite Kontrol raporu verir.",
        "KALİTE\nKONTROL onaylar.",
        "Kalite  Kontrol imzalar.",
    ]
    text = " ".join(sentences[:uses]) + "\n\nKalite Kontrol\nİmza:"
    masked, mapping = mask(text, {})
    assert ("Kalite Kontrol" in mapping.values()) is masked_name
    if not masked_name:
        assert (masked, mapping) == (text, {})


def test_mask_defined_term_signature_line_masked_alone_in_signature_part() -> None:
    # The prepare call chain: the whole text sees the uses and finds no name; the signature part
    # alone is masked on its own and the caption becomes a name there only. The body stays raw,
    # and the mapping holds one placeholder that unmasks the part exactly.
    body = "Kalite Kontrol raporu verir. Kalite Kontrol onaylar. Kalite Kontrol imzalar.\n\n"
    part = "Kalite Kontrol\nİmza:"
    _, mapping = mask(body + part, {})
    assert mapping == {}
    masked_body, mapping = mask(body, mapping, pii.unlabelled_names(body + part))
    masked_part, mapping = mask(part, mapping, pii.unlabelled_names(body + part))
    assert masked_body == body
    assert masked_part == "[KISI_1]\nİmza:"
    assert mapping == {"[KISI_1]": "Kalite Kontrol"}
    assert unmask(masked_part, mapping) == part


# --- An unlabelled name gives way to every other value (step 19c) -------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "İletişim: Elif Soylu (elif.soylu@ornek.com).\nYedek adres: Elif Soylu-elif@ornek.com",
            "İletişim: [KISI_1] ([EPOSTA_1]).\nYedek adres: Elif [EPOSTA_2]",
        ),
        (
            "Elif Soylu (elif.soylu@ornek.com)\nElif Soylu.yedek@ornek.com",
            "[KISI_1] ([EPOSTA_1])\nElif [EPOSTA_2]",
        ),
        (
            "Kemal Ozturk\nİmza:\nKemal Ozturk_kemal@ornek.com",
            "[KISI_1]\nİmza:\nKemal [EPOSTA_1]",
        ),
        (
            "Kemal Ozturk\nİmza:\nKemal Ozturk+kemal@ornek.com, Kemal Ozturk",
            "[KISI_1]\nİmza:\nKemal [EPOSTA_1], [KISI_1]",
        ),
    ],
    ids=["email-path-hyphen", "email-path-dot", "signature-path-underscore", "plus-and-free"],
)
def test_mask_unlabelled_name_glued_to_email_email_keeps_its_place(
    text: str, expected: str
) -> None:
    masked, mapping = mask(text, {})
    assert masked == expected
    assert unmask(masked, mapping) == text


# --- Every name gives way to a value that is not a name (step 19d) -------------------------------


@pytest.mark.parametrize("joint", ["-", ".", "_", "+", "%"])
def test_mask_labelled_name_glued_to_email_email_masked_as_without_names(joint: str) -> None:
    # The e-mail is masked as if no name were known; the labelled line keeps its placeholder and
    # the part of the name outside the e-mail ("Elif") stays raw.
    text = f"Yetkili: Elif Soylu\nYedek adres: Elif Soylu{joint}elif@ornek.com"
    masked, mapping = mask(text, {})
    assert masked == "Yetkili: [KISI_1]\nYedek adres: Elif [EPOSTA_1]"
    assert mapping == {"[KISI_1]": "Elif Soylu", "[EPOSTA_1]": f"Soylu{joint}elif@ornek.com"}
    assert unmask(masked, mapping) == text
    assert mask(masked, mapping) == (masked, mapping)


def test_mask_labelled_name_spaced_from_email_both_masked() -> None:
    text = "Yetkili: Elif Soylu\nYedek: Elif Soylu - elif@ornek.com"
    masked, _ = mask(text, {})
    assert masked == "Yetkili: [KISI_1]\nYedek: [KISI_1] - [EPOSTA_1]"


def test_mask_name_from_mapping_glued_to_email_gives_way_without_list() -> None:
    # The clause call of `prepare_contract` and the lawyer's edited text (`_masked_decision`):
    # the name comes from the mapping and no `unlabelled` list is needed.
    whole = "Kemal Ozturk\nİmza:\nYedek: Kemal Ozturk_kemal@ornek.com"
    _, mapping = mask(whole, {})
    assert mapping == {"[KISI_1]": "Kemal Ozturk", "[EPOSTA_1]": "Ozturk_kemal@ornek.com"}
    clause = "Yedek: Kemal Ozturk_kemal@ornek.com, Kemal Ozturk onaylar."
    expected = "Yedek: Kemal [EPOSTA_1], [KISI_1] onaylar."
    assert mask(clause, mapping, pii.unlabelled_names(whole))[0] == expected
    assert mask(clause, mapping)[0] == expected
    # A name only in the mapping, as in an edited draft; the e-mail is new there.
    edited = "Bildirim Kemal Uçar-kemal@ornek.com adresine; Kemal Uçar onaylar."
    masked, result = mask(edited, {"[KISI_1]": "Kemal Uçar"})
    assert masked == "Bildirim Kemal Uç[EPOSTA_1] adresine; [KISI_1] onaylar."
    assert result == {"[KISI_1]": "Kemal Uçar", "[EPOSTA_1]": "ar-kemal@ornek.com"}
    assert unmask(masked, result) == edited


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Elif Soylu21473873960", "Elif Soylu[TCKN_1]"),
        ("Elif Soylu 214 738 739 60", "[KISI_1] [TCKN_1]"),
        ("Elif Soylu0532 123 45 67", "Elif Soylu[TELEFON_1]"),
        ("Elif Soylu-0532 123 45 67", "[KISI_1]-[TELEFON_1]"),
        ("Elif Soylu533 555 05 30", "Elif Soylu[TELEFON_1]"),
        ("Elif Soylu TR33 0006 1005 1978 6457 8413 26", "[KISI_1] [IBAN_1]"),
    ],
    ids=["tckn-glued", "tckn-spaced", "phone-glued", "phone-hyphen", "bare-mobile", "iban-spaced"],
)
def test_mask_labelled_name_next_to_number_number_masked(line: str, expected: str) -> None:
    # A name ends only before a character that is not a letter or digit, so a glued number never
    # overlaps it: the number is masked and that occurrence of the name stays raw (as before
    # step 19d).
    text = f"Yetkili: Elif Soylu\n{line}"
    masked, mapping = mask(text, {})
    assert masked == f"Yetkili: [KISI_1]\n{expected}"
    assert unmask(masked, mapping) == text


def test_mask_labelled_name_ending_in_tr_keeps_place_before_weak_iban_documented_limit() -> None:
    # Documented in agent_design.md "PII maskeleme": the label run "Ahmet TR" overlaps the freely
    # grouped IBAN "TR 33 …", a weak (step 17b) spelling, which still gives way to a name; the
    # IBAN's digits stay raw, as before step 19d.
    text = "Yetkili: Ahmet TR 33 0006 1005 1978 6457 8413 26"
    masked, mapping = mask(text, {})
    assert masked == "Yetkili: [KISI_1] 33 0006 1005 1978 6457 8413 26"
    assert unmask(masked, mapping) == text


def test_mask_name_glued_to_iban_neither_masked_documented_limit() -> None:
    # Documented in agent_design.md "PII maskeleme" (before step 19d too): the IBAN needs a
    # character that is not a letter before it, and the name one that is not a letter or digit
    # after it, so neither is found.
    text = "Yetkili: Elif Soylu\nElif SoyluTR330006100519786457841326"
    masked, _ = mask(text, {})
    assert masked == "Yetkili: [KISI_1]\nElif SoyluTR330006100519786457841326"


def test_mask_unlabelled_name_gives_way_to_labelled_name_only_when_listed() -> None:
    # Between two names, a name only the e-mail or signature path found gives way; in a clause
    # call the mapping alone does not show how a name was found, so the list is still needed.
    whole = "Yetkili: Ahmet Yılmaz\nCan Ahmet\nİmza:\nNot: Can Ahmet Yılmaz"
    masked, mapping = mask(whole, {})
    assert masked == "Yetkili: [KISI_1]\n[KISI_2]\nİmza:\nNot: Can [KISI_1]"
    assert pii.unlabelled_names(whole) == frozenset({"Can Ahmet"})
    clause = "Not: Can Ahmet Yılmaz"
    assert mask(clause, mapping, pii.unlabelled_names(whole))[0] == "Not: Can [KISI_1]"
    assert mask(clause, mapping)[0] == "Not: [KISI_2] Yılmaz"


def test_mask_unlabelled_names_excludes_names_the_label_path_finds() -> None:
    text = "Yetkili: Elif Soylu\nElif Soylu (elif.soylu@ornek.com)\nKemal Uçar\nİmza:"
    assert pii.unlabelled_names(text) == frozenset({"Kemal Uçar"})


@pytest.mark.parametrize(
    ("company", "masked_name"),
    [
        ("Ahmet Yılmaz Gıda İnşaat Turizm Sanayi ve Ticaret Limited Şirketi", False),
        ("Ahmet Yılmaz " + "Ab " * 14 + "A.Ş.", True),
    ],
    ids=["marker-within-12-words", "marker-past-12-words-documented-limit"],
)
def test_mask_email_name_company_check_looks_twelve_words_ahead(
    company: str, masked_name: bool
) -> None:
    _, mapping = mask(f"{company} (ahmet.yilmaz@ornek.com)", {})
    assert ("Ahmet Yılmaz" in mapping.values()) is masked_name


@pytest.mark.parametrize(
    "iban",
    [IBAN_SPACED, IBAN_SPACED.replace(" ", "\u00a0"), "TR33 0006 1005 1978 6457 841326"],
)
def test_mask_iban_standard_grouping_masked(iban: str) -> None:
    text = f"Ödemeler {iban} numaralı hesaba 30 gün içinde yapılır."
    masked, mapping = mask(text, {})
    assert masked == "Ödemeler [IBAN_1] numaralı hesaba 30 gün içinde yapılır."
    assert mapping == {"[IBAN_1]": iban}
    assert unmask(masked, mapping) == text
    assert mask(masked, mapping) == (masked, mapping)


def test_mask_iban_grouped_and_compact_spelling_two_placeholders() -> None:
    masked, mapping = mask(f"{IBAN_SPACED} / {IBAN}", {})
    assert masked == "[IBAN_1] / [IBAN_2]"
    assert mapping == {"[IBAN_1]": IBAN_SPACED, "[IBAN_2]": IBAN}


def test_mask_grouped_iban_followed_by_amount_and_date_only_iban_masked() -> None:
    text = f"IBAN: {IBAN_SPACED} 1.250.000 TL 01.01.2026 90 gün"
    masked, _ = mask(text, {})
    assert masked == "IBAN: [IBAN_1] 1.250.000 TL 01.01.2026 90 gün"


@pytest.mark.parametrize(
    ("line", "value"),
    [
        ("Ad Soyad: Ayşe Kaya-Demir", "Ayşe Kaya-Demir"),
        ("Ad Soyad: Ayşe Kaya-Demir İmza Tarih", "Ayşe Kaya-Demir"),
        ("Yetkili: Ayşe Kaya-Demir Genel Müdür", "Ayşe Kaya-Demir"),
        ("temsilen Genel Müdür Ayşe Kaya-Demir", "Ayşe Kaya-Demir"),
        ("Ad Soyad: Ayşe Kaya-Demir Şirket: ABC A.Ş.", "Ayşe Kaya-Demir"),
        ("Ad Soyad: Sean O'Brien", "Sean O'Brien"),
        ("Ad Soyad: Jean-Luc Picard", "Jean-Luc Picard"),
        ("Ad Soyad: AYŞE KAYA-DEMİR", "AYŞE KAYA-DEMİR"),
    ],
)
def test_mask_name_with_inner_hyphen_or_apostrophe_whole_name_masked(line: str, value: str) -> None:
    text = f"{line}\nGövdede {value}'in imzası aranır."
    masked, mapping = mask(text, {})
    assert mapping == {"[KISI_1]": value}
    assert value not in masked
    assert "Demir" not in masked
    assert "Brien" not in masked
    assert "Gövdede [KISI_1]'in imzası" in masked
    assert unmask(masked, mapping) == text


@pytest.mark.parametrize(
    ("line", "masked_line"),
    [
        ("Ad Soyad: Ahmet Yılmaz-Genel Müdür", "Ad Soyad: [KISI_1]-Genel Müdür"),
        ("Ad Soyad: Ahmet Yılmaz-Müdür", "Ad Soyad: [KISI_1]-Müdür"),
        ("Ad Soyad: Ahmet Yılmaz-İmza", "Ad Soyad: [KISI_1]-İmza"),
        ("Ad Soyad: Ahmet Yılmaz-Unvan: Müdür", "Ad Soyad: [KISI_1]-Unvan: Müdür"),
        ("Yetkili: Ahmet Yılmaz-ABC A.Ş.", "Yetkili: [KISI_1]-ABC A.Ş."),
        ("Yetkili: Ahmet Yılmaz-ABC Teknoloji A.Ş.", "Yetkili: [KISI_1]-ABC Teknoloji A.Ş."),
        ("Ad Soyad: Ahmet Yılmaz - Genel Müdür", "Ad Soyad: [KISI_1] - Genel Müdür"),
        ("Ad Soyad: Ahmet Yılmaz'ın imzası", "Ad Soyad: [KISI_1]'ın imzası"),
    ],
)
def test_mask_name_hyphen_before_title_form_word_or_company_run_stops(
    line: str, masked_line: str
) -> None:
    masked, mapping = mask(f"{line}\nGövdede Ahmet Yılmaz geçer.", {})
    assert masked == f"{masked_line}\nGövdede [KISI_1] geçer."
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz"}


def test_mask_two_people_joined_by_hyphen_one_value_documented_limit() -> None:
    masked, mapping = mask("Ad Soyad: Ahmet Yılmaz-Mehmet Demir", {})
    assert masked == "Ad Soyad: [KISI_1]"
    assert mapping == {"[KISI_1]": "Ahmet Yılmaz-Mehmet Demir"}


def test_mask_long_hyphen_chain_fast() -> None:
    text = "Ad Soyad: " + "-".join(["Ab"] * 40000) + " ve " + " ve ".join(["Ab-Cd"] * 20000)
    started = time.perf_counter()
    masked, mapping = mask(text, {})
    assert time.perf_counter() - started < 5
    assert unmask(masked, mapping) == text


# --- Spaced TCKN, freely grouped IBAN, bare mobile number (step 17b) ----------------------------

NBSP = "\N{NO-BREAK SPACE}"
TCKN_D = "21473873960"  # contract 5, written there as "214 738 739 60"
TCKN_E = "66047595282"  # generated by script, checksum checked below
TCKN_F = "54470948270"
TCKN_TRAP_PREFIX = "45123456789"  # "45 123456789" in the trap text: 11 digits, bad checksum
IBAN_FREE = "TR18 00010 00406 02833 88480 99"  # contract 5
IBAN_FREE_BAD = "TR19 00010 00406 02833 88480 99"
# Generated so that its first eleven digits, written "42 579849772", are a valid TCKN.
IBAN_WITH_TCKN = "TR42 579849772 5136993391178"
TCKN_IN_IBAN = "42579849772"


def _compact(value: str) -> str:
    return "".join(value.split())


def test_fixtures_step_17b_checksums_are_as_expected() -> None:
    assert all(_tckn_valid(n) for n in (TCKN_B, TCKN_D, TCKN_E, TCKN_F, TCKN_IN_IBAN))
    assert not _tckn_valid(TCKN_TRAP_PREFIX)
    assert not _tckn_valid("21473873961")
    assert _iban_valid(_compact(IBAN_FREE))
    assert _iban_valid(_compact(IBAN_WITH_TCKN))
    assert not _iban_valid(_compact(IBAN_FREE_BAD))


def _assert_masked_alone(text: str, expected: str, value: str) -> None:
    """`value` is the only PII in `text`; round trip, repeat and re-masking all hold."""
    masked, mapping = mask(text, {})
    assert masked == expected
    placeholder = next(iter(mapping))
    assert mapping == {placeholder: value}
    assert value not in masked
    assert _compact(value) not in _compact(masked)
    assert unmask(masked, mapping) == text
    assert mask(masked, mapping) == (masked, mapping)
    twice, twice_mapping = mask(f"{text} / {text}", {})
    assert twice == f"{expected} / {expected}"
    assert twice_mapping == mapping


@pytest.mark.parametrize(
    "tckn",
    [
        "214 738 739 60",
        "2147 3873 960",
        "21473 873960",
        "2 1 4 7 3 8 7 3 9 6 0",
        f"214{NBSP}738{NBSP}739{NBSP}60",
        f"2147 3873{NBSP}960",
        "660 475 952 82",
        "5447 0948 270",
        TCKN_D,
    ],
)
def test_mask_spaced_tckn_any_grouping_masked(tckn: str) -> None:
    _assert_masked_alone(f"Kimlik no: {tckn} kayıtlıdır.", "Kimlik no: [TCKN_1] kayıtlıdır.", tckn)


@pytest.mark.parametrize(
    ("text", "expected", "value"),
    [
        # "45 123456789" is eleven digits with a bad checksum; it must not swallow the real value.
        ("Sıra 45 123456789 50 kayıtlı.", "Sıra 45 [TCKN_1] kayıtlı.", "123456789 50"),
        (f"No 45{NBSP}123456789{NBSP}50.", f"No 45{NBSP}[TCKN_1].", f"123456789{NBSP}50"),
        ("Sıra 7 2147 3873 960 kayıtlı.", "Sıra 7 [TCKN_1] kayıtlı.", "2147 3873 960"),
    ],
)
def test_mask_spaced_tckn_after_bad_candidate_real_value_masked(
    text: str, expected: str, value: str
) -> None:
    _assert_masked_alone(text, expected, value)


@pytest.mark.parametrize(
    "text",
    [
        "Kimlik 214 738 739 61 yazar.",
        "Kimlik 2147 3873 961 yazar.",
        "Kimlik 2 1 4 7 3 8 7 3 9 6 1 yazar.",
        "Kimlik 214  738 739 60 yazar.",  # two spaces
        "Kimlik 214\t738 739 60 yazar.",
        "Kimlik 214 738\n739 60 yazar.",
        "Kimlik 214\N{THIN SPACE}738 739 60 yazar.",
    ],
)
def test_mask_spaced_tckn_bad_checksum_or_other_gap_not_masked(text: str) -> None:
    assert _mask(text) == (text, {})


@pytest.mark.parametrize(
    "iban",
    [
        IBAN_FREE,
        "TR 18 0001 0004 0602 8338 8480 99",
        "TR180001 000406028338 848099",
        IBAN_FREE.replace(" ", NBSP),
        "TR1 8 0 0 0 1 0 0 0 4 0 6 0 2 8 3 3 8 8 4 8 0 9 9",
        "TR33 000 61005 1978 6457 8413 26",
        IBAN_WITH_TCKN,
    ],
)
def test_mask_iban_free_grouping_masked(iban: str) -> None:
    _assert_masked_alone(
        f"İade {iban} numaralı hesaba yapılır.", "İade [IBAN_1] numaralı hesaba yapılır.", iban
    )


@pytest.mark.parametrize(
    "iban",
    [
        IBAN_FREE_BAD,
        "TR18 00010 00406\n02833 88480 99",  # split over two lines (documented limit)
        "TR18 00010  00406 02833 88480 99",  # two spaces
        "TR18 00010\t00406 02833 88480 99",
        "TR18 00010 00406 02833 88480 9",  # 23 digits
    ],
)
def test_mask_iban_free_grouping_bad_checksum_or_gap_not_masked(iban: str) -> None:
    text = f"İade {iban} numaralı hesaba yapılır."
    assert _mask(text) == (text, {})


def test_mask_tckn_digits_inside_iban_single_iban_placeholder() -> None:
    masked, mapping = _mask(f"Hesap {IBAN_WITH_TCKN}, kimlik ayrıca bildirilir.")
    assert masked == "Hesap [IBAN_1], kimlik ayrıca bildirilir."
    assert mapping == {"[IBAN_1]": IBAN_WITH_TCKN}


@pytest.mark.parametrize(
    "phone",
    ["533 555 05 30", "533.555.05.30", "533-555-05-30", "533 555-05.30", "532 123 45 67"],
)
def test_mask_bare_mobile_with_separators_masked(phone: str) -> None:
    _assert_masked_alone(f"(cep: {phone}, mesai içinde)", "(cep: [TELEFON_1], mesai içinde)", phone)


@pytest.mark.parametrize(
    "prefixed", ["+90 532 123 45 67", "0532 123 45 67", "0532-123-45-67", "+905321234567"]
)
def test_mask_prefixed_phone_stays_one_value_bare_spelling_separate(prefixed: str) -> None:
    text = f"Tel: {prefixed} / cep: 532 123 45 67"
    masked, mapping = _mask(text)
    assert masked == "Tel: [TELEFON_1] / cep: [TELEFON_2]"
    assert mapping == {"[TELEFON_1]": prefixed, "[TELEFON_2]": "532 123 45 67"}
    assert unmask(masked, mapping) == text


@pytest.mark.parametrize("phone", ["+90 532 123 45 67", "0532 123 45 67", "0212 123 45 67"])
def test_mask_prefixed_phone_no_second_value_inside(phone: str) -> None:
    masked, mapping = _mask(f"Tel: {phone}.")
    assert masked == "Tel: [TELEFON_1]."
    assert mapping == {"[TELEFON_1]": phone}


_NON_PII_17B = [
    "214 738 739 61",
    "TR19 00010 00406 02833 88480 99",
    "1 250 000 TL",
    "10 000 000 000 TL",
    "01 04 2026",
    "90 gün 30 gün 15 gün",
    "Madde 532 123 45 67",
    "MADDE 532.123.45.67",
    "532.123,45",
    "1.532.123.45.67",
    "10.532.123.45",
    "532 123 45 67 89",
    "5335550530",
    "212 555 12 34",
    "53 3555 05 30",
    "533 555 0530",
]


@pytest.mark.parametrize("fragment", _NON_PII_17B)
def test_mask_step_17b_non_pii_fragment_unchanged(fragment: str) -> None:
    text = f"Sözleşmede {fragment} yazar."
    assert _mask(text) == (text, {})


def test_mask_step_17b_non_pii_all_together_unchanged() -> None:
    text = (
        "Madde 532 123 45 67 - Bedel\nToplam bedel 1 250 000 TL, üst sınır 10 000 000 000 TL. "
        "Başlangıç 01 04 2026; süreler 90 gün 30 gün 15 gün. Oran 532.123,45 ve "
        "1.532.123.45.67; sürüm 10.532.123.45. Kimlik 214 738 739 61."
    )
    assert _mask(text) == (text, {})


def test_mask_bare_mobile_shape_amount_known_false_positive_masked() -> None:
    # Documented in agent_design.md "PII maskeleme": no checksum separates it from a phone.
    text = "Bedel 500 000 00 00 TL olarak yazılmıştır."
    masked, mapping = _mask(text)
    assert masked == "Bedel [TELEFON_1] TL olarak yazılmıştır."
    assert mapping == {"[TELEFON_1]": "500 000 00 00"}
    assert unmask(masked, mapping) == text


# Round-million amounts whose eleven digits pass the TCKN checksum, found by script (1800 of the
# 90 000 from 10 000 000 000 to 99 999 000 000 do).
AMOUNTS_TCKN_SHAPED = ("55000000000", "50500000000", "10054000000")


def _thousands(digits: str, gap: str = " ") -> str:
    head = len(digits) % 3 or 3
    return gap.join([digits[:head]] + [digits[i : i + 3] for i in range(head, len(digits), 3)])


def test_fixtures_amounts_pass_tckn_checksum() -> None:
    assert all(_tckn_valid(a) for a in AMOUNTS_TCKN_SHAPED)
    assert _thousands("55000000000") == "55 000 000 000"
    assert _tckn_valid("15400000000")  # "1 5 400 000 000" in "Madde 5.1 5 400 000 000 TL"


@pytest.mark.parametrize(
    "text",
    [
        *(f"Sorumluluk üst sınırı {_thousands(a)} TL'dir." for a in AMOUNTS_TCKN_SHAPED),
        *(f"Sorumluluk üst sınırı {_thousands(a, NBSP)} TL'dir." for a in AMOUNTS_TCKN_SHAPED),
        "Madde 5.1 5 400 000 000 TL ödenir.",
        "Madde 5,1 5 400 000 000 TL ödenir.",
    ],
)
def test_mask_thousands_grouped_amount_passing_tckn_checksum_not_masked(text: str) -> None:
    assert _mask(text) == (text, {})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"Kimlikler {TCKN_B},214 738 739 60 kayıtlı.", "Kimlikler [TCKN_1],[TCKN_2] kayıtlı."),
        ("Kimlikler 214 738 739 60,660 475 952 82.", "Kimlikler [TCKN_1],[TCKN_2]."),
        (
            f"Kimlik {TCKN_D} 533 555 05 30,660 475 952 82.",
            "Kimlik [TCKN_1] [TELEFON_1],[TCKN_2].",
        ),
    ],
)
def test_mask_spaced_tckn_after_value_and_comma_still_masked(text: str, expected: str) -> None:
    # The digit before the comma ends a found value, so it does not block the spaced TCKN.
    masked, mapping = _mask(text)
    assert masked == expected
    assert unmask(masked, mapping) == text
    assert mask(masked, mapping) == (masked, mapping)


def test_mask_compact_tckn_after_dotted_number_still_masked() -> None:
    text = f"Kimlik 1.{TCKN_B} kayıtlı."
    assert _mask(text) == ("Kimlik 1.[TCKN_1] kayıtlı.", {"[TCKN_1]": TCKN_B})


def test_mask_adjacent_numbers_forming_tckn_known_false_positive_masked() -> None:
    # Documented in agent_design.md "PII maskeleme": separate numbers divided by single spaces that
    # together make eleven digits and pass the checksum cannot be told from a spaced TCKN.
    assert _tckn_valid("10301201020")
    text = "Teslim süreleri sırasıyla 10 30 120 10 20 gündür."
    masked, mapping = _mask(text)
    assert masked == "Teslim süreleri sırasıyla [TCKN_1] gündür."
    assert mapping == {"[TCKN_1]": "10 30 120 10 20"}
    assert unmask(masked, mapping) == text


def test_mask_thousands_grouped_tckn_not_masked_documented_limit() -> None:
    assert _tckn_valid("21473873960")
    text = "Kimlik 21 473 873 960 kayıtlı."
    assert _mask(text) == (text, {})


@pytest.mark.parametrize(
    ("text", "phone"),
    [
        ("Sözleşme No 4437 0532 648 99 50", "0532 648 99 50"),
        ("Sözleşme No 2072 0532 360 82 92", "0532 360 82 92"),
        ("Sözleşme No 7878 0532 998 74 02", "0532 998 74 02"),
    ],
)
def test_mask_spaced_tckn_candidate_before_prefixed_phone_does_not_split_it(
    text: str, phone: str
) -> None:
    prefix = text[: text.index(phone)]
    assert _tckn_valid(_compact(text[len("Sözleşme No ") :])[:11])  # the trap candidate is valid
    masked, mapping = _mask(text)
    assert masked == f"{prefix}[TELEFON_1]"
    assert mapping == {"[TELEFON_1]": phone}
    assert unmask(masked, mapping) == text
    assert mask(masked, mapping) == (masked, mapping)


@pytest.mark.parametrize(
    ("text", "expected", "values"),
    [
        (
            "Tel: 533 555 05 30 532 123 45 67",
            "Tel: [TELEFON_1] [TELEFON_2]",
            ["533 555 05 30", "532 123 45 67"],
        ),
        (
            "Tel: 533 555 05 30 532 123 45 67 544-111-22-33.",
            "Tel: [TELEFON_1] [TELEFON_2] [TELEFON_3].",
            ["533 555 05 30", "532 123 45 67", "544-111-22-33"],
        ),
    ],
)
def test_mask_bare_mobiles_separated_by_one_space_each_masked(
    text: str, expected: str, values: list[str]
) -> None:
    masked, mapping = _mask(text)
    assert masked == expected
    assert list(mapping.values()) == values
    assert unmask(masked, mapping) == text
    assert mask(masked, mapping) == (masked, mapping)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("TCKN 21473873960 533 555 05 30", "TCKN [TCKN_1] [TELEFON_1]"),
        ("TCKN 214 738 739 60 533 555 05 30", "TCKN [TCKN_1] [TELEFON_1]"),
        ("IBAN TR180001000406028338848099 533 555 05 30", "IBAN [IBAN_1] [TELEFON_1]"),
        ("0532 123 45 67 533 555 05 30", "[TELEFON_1] [TELEFON_2]"),
    ],
)
def test_mask_bare_mobile_right_after_value_masked_first_pass_and_stable(
    text: str, expected: str
) -> None:
    masked, mapping = _mask(text)
    assert masked == expected
    assert "533 555 05 30" in mapping.values()
    assert mask(masked, mapping) == (masked, mapping)
    assert unmask(masked, mapping) == text


@pytest.mark.parametrize(
    "phone",
    [
        "533\t555 05 30",
        "533 555\n05 30",
        "533\r\n555 05 30",
        "533\N{THIN SPACE}555 05 30",
        f"533{NBSP}555 05 30",
        f"533 555 05{NBSP}30",
        f"533{NBSP}555{NBSP}05{NBSP}30",
    ],
)
def test_mask_bare_mobile_tab_line_break_or_unicode_space_separator_not_masked(phone: str) -> None:
    # NBSP is not a phone separator either (agent_design.md "PII maskeleme"; documented limit).
    text = f"(cep: {phone}, mesai içinde)"
    assert _mask(text) == (text, {})


_MIXED_17B = (
    "Ad Soyad: Ahmet Yılmaz TCKN: 214 738 739 60\n"
    "Cep: 533 555 05 30, sabit: 0212 123 45 67, e-posta: ahmet@ornek.com.\n"
    f"IBAN: {IBAN_FREE}; avans {IBAN_SPACED}.\n"
    "Bedel 1 250 000 TL, ödeme 90 gün içinde, 01 04 2026 tarihinde; "
    "Ahmet Yılmaz (2147 3873 960) onaylar, cep 533.555.05.30."
)


def test_mask_step_17b_mixed_text_no_raw_value_left_and_unmask_restores() -> None:
    masked, mapping = _mask(_MIXED_17B)
    assert sorted(mapping.values()) == sorted(
        [
            "Ahmet Yılmaz",
            "214 738 739 60",
            "2147 3873 960",
            "533 555 05 30",
            "533.555.05.30",
            "0212 123 45 67",
            "ahmet@ornek.com",
            IBAN_FREE,
            IBAN_SPACED,
        ]
    )
    for value in mapping.values():
        assert value not in masked
    for digits in (TCKN_D, "5335550530", _compact(IBAN_FREE)):
        assert digits not in _compact(masked)
    assert "1 250 000 TL" in masked
    assert "01 04 2026" in masked
    assert unmask(masked, mapping) == _MIXED_17B
    assert mask(masked, mapping) == (masked, mapping)


def _random_digits(seed: int, gaps: tuple[str, ...]) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice("0123456789") + rng.choice(gaps) for _ in range(140_000))[:200_000]


@pytest.mark.parametrize(
    "text",
    [
        _random_digits(5, ("", "", " ")),
        _random_digits(9, ("", " ", " ", ".", "-")),
        "5 " * 100_000,
        "533 555 05 30 " * 14_300,
        "1 " * 100_000,
    ],
    ids=["random-spaces", "random-separators", "five-space", "phones", "one-space"],
)
def test_mask_200kb_digits_and_spaces_round_trip(text: str) -> None:
    # Speed is measured by hand, not asserted; this checks the round trip on a 200 KB input.
    masked, mapping = _mask(text)
    assert unmask(masked, mapping) == text
    assert mask(masked, mapping) == (masked, mapping)


# --- Step 17b invariant: a value the old patterns find never loses its place --------------------

_IBAN_BORROWS_PHONE = (
    "Ödeme hesabı TR34 0006 1005 1978 6457 84 0532 123 45 67 numaralı telefondan teyit edilir."
)


def test_mask_iban_missing_digit_before_prefixed_phone_does_not_split_it() -> None:
    # "TR34 0006 1005 1978 6457 84 0532" (borrowing the phone's "0532") passes mod-97; the freely
    # grouped IBAN is weak, so the prefixed phone keeps its place, as before step 17b.
    assert _iban_valid("TR340006100519786457840532")
    masked, mapping = _mask(_IBAN_BORROWS_PHONE)
    assert masked == (
        "Ödeme hesabı TR34 0006 1005 1978 6457 84 [TELEFON_1] numaralı telefondan teyit edilir."
    )
    assert mapping == {"[TELEFON_1]": "0532 123 45 67"}
    assert mask(masked, mapping) == (masked, mapping)
    assert unmask(masked, mapping) == _IBAN_BORROWS_PHONE


@pytest.mark.parametrize("joint", ["-", ".", "_", ""])
def test_mask_bare_mobile_glued_to_email_email_keeps_its_place_documented_limit(
    joint: str,
) -> None:
    # Documented in agent_design.md "PII maskeleme": the e-mail's local part starts inside the
    # phone; the e-mail is strict and wins, so the result is the one from before step 17b.
    text = f"Tel: 533 555 05 30{joint}info@ornek.com"
    masked, mapping = _mask(text)
    assert masked == "Tel: 533 555 05 [EPOSTA_1]"
    assert mapping == {"[EPOSTA_1]": f"30{joint}info@ornek.com"}
    assert mask(masked, mapping) == (masked, mapping)
    assert unmask(masked, mapping) == text


# The patterns before step 17b: compact or standard-grouped IBAN, compact TCKN, no bare mobile.
_OLD_GAP = "[ " + NBSP + "]?"
_OLD_PATTERNS = {
    "_IBAN": re.compile(
        r"(?<![^\W_])TR[0-9]{2}(?:" + _OLD_GAP + r"[0-9]{4}){5}" + _OLD_GAP + r"[0-9]{2}(?![^\W_])"
    ),
    "_TCKN": re.compile(r"(?<![0-9])(?=([1-9][0-9]{10})(?![0-9]))"),
    "_PHONE_BARE": re.compile(r"(?!)"),
    "_PHONE_PARENS": re.compile(r"(?!)"),
}


def _masked_spans(text: str, masked: str, mapping: dict[str, str]) -> set[tuple[int, int, str]]:
    """(start, end, raw value) in `text` of every placeholder in `masked`."""
    spans: set[tuple[int, int, str]] = set()
    shift = 0
    for m in PLACEHOLDER.finditer(masked):
        value = mapping[m.group()]
        start = m.start() + shift
        assert text[start : start + len(value)] == value
        spans.add((start, start + len(value), value))
        shift += len(value) - len(m.group())
    return spans


def _gen_tckn(rng: random.Random) -> str:
    d = [rng.randint(1, 9)] + [rng.randint(0, 9) for _ in range(8)]
    d.append(((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10)
    d.append(sum(d) % 10)
    if rng.random() < 0.3:
        d[10] = (d[10] + rng.randint(1, 9)) % 10  # bad checksum
    return "".join(map(str, d))


def _gen_spaced(rng: random.Random, chars: str, keep: int = 1) -> str:
    """`chars` with a space (sometimes a no-break space) between characters at random."""
    out = chars[:keep]
    for ch in chars[keep:]:
        if rng.random() < 0.4:
            out += NBSP if rng.random() < 0.1 else " "
        out += ch
    return out


def _gen_iban(rng: random.Random) -> str:
    bban = "".join(rng.choice("0123456789") for _ in range(22))
    check = f"{98 - int(bban + '292700') % 97:02d}"
    if rng.random() < 0.2:
        check = f"{(int(check) + rng.randint(1, 90)) % 100:02d}"  # bad checksum
    digits = check + bban
    if rng.random() < 0.25:
        cut = rng.randrange(len(digits))
        digits = digits[:cut] + digits[cut + 1 :]  # a digit missing
    shape = rng.random()
    if shape < 0.25:
        return "TR" + digits
    if shape < 0.55:
        return "TR" + " ".join(digits[i : i + 4] for i in range(0, len(digits), 4))
    return _gen_spaced(rng, "TR" + digits, keep=2)


def _gen_phone(rng: random.Random) -> str:
    def sep() -> str:
        return rng.choice(["", " ", " ", ".", "-"])

    def bare_sep() -> str:
        return rng.choice([" ", " ", ".", "-"])

    groups = [str(rng.choice([5, 5, 5, 2, 3])) + f"{rng.randint(0, 99):02d}"]
    groups += [
        f"{rng.randint(0, 999):03d}",
        f"{rng.randint(0, 99):02d}",
        f"{rng.randint(0, 99):02d}",
    ]
    if rng.random() < 0.5:
        return "".join(g + bare_sep() for g in groups)[:-1]
    prefix = rng.choice(["+90", "0"])
    return prefix + "".join(sep() + g for g in groups)


def _gen_token(rng: random.Random) -> str:
    kind = rng.randrange(9)
    if kind == 0:
        tckn = _gen_tckn(rng)
        return tckn if rng.random() < 0.4 else _gen_spaced(rng, tckn)
    if kind == 1:
        return _gen_iban(rng)
    if kind == 2:
        return _gen_phone(rng)
    if kind == 3:
        local = "".join(rng.choice("abcxyz0123456789._-") for _ in range(rng.randint(1, 6)))
        return rng.choice(["", "info", "30", "a"]) + local + "@ornek.com"
    if kind == 4:
        return "Ad Soyad: " + rng.choice(["Ahmet Yılmaz", "Elif Soylu", "Kemal Uçar"])
    if kind == 5:
        amount = str(rng.randint(1, 99_999_999_999))
        return rng.choice([_thousands(amount), _thousands(amount, "."), amount]) + " TL"
    if kind == 6:
        day, month = rng.randint(1, 28), rng.randint(1, 12)
        return f"{day:02d}{rng.choice(['.', ' '])}{month:02d}{rng.choice(['.', ' '])}2026"
    return str(rng.randint(0, 10 ** rng.randint(1, 4)))  # a short digit group


_JOINTS = (" ", " ", ", ", ",", ".", "-", "_", "\n", "", "")


def _gen_text(rng: random.Random) -> str:
    parts = [_gen_token(rng) for _ in range(rng.randint(2, 7))]
    return "".join(part + rng.choice(_JOINTS) for part in parts)


def test_mask_new_formats_never_take_place_of_old_value(monkeypatch: pytest.MonkeyPatch) -> None:
    # Speed is not asserted. Each masked (start, end, raw value) with the step 17b discovery
    # switched off in memory is masked identically with it switched on.
    rng = random.Random(1717)
    for _ in range(3_000):
        text = _gen_text(rng)
        with monkeypatch.context() as patched:
            for name, pattern in _OLD_PATTERNS.items():
                patched.setattr(pii, name, pattern)
            old_masked, old_mapping = mask(text, {})
        new_masked, new_mapping = mask(text, {})
        old = _masked_spans(text, old_masked, old_mapping)
        new = _masked_spans(text, new_masked, new_mapping)
        assert old <= new, (text, sorted(old - new))
        assert unmask(new_masked, new_mapping) == text


def _gen_phone_in_parens(rng: random.Random) -> str:
    def sep() -> str:
        return rng.choice(["", " ", " ", ".", "-"])

    area = f"{rng.choice([5, 2, 3, 4])}{rng.randint(0, 99):02d}"
    groups = [
        f"{rng.randint(0, 999):03d}",
        f"{rng.randint(0, 99):02d}",
        f"{rng.randint(0, 99):02d}",
    ]
    rest = groups[0] + sep() + groups[1] + sep() + groups[2]
    shape = rng.randrange(4)
    if shape == 0:
        return f"{rng.choice(['+90', '0'])}{sep()}({area}){sep()}{rest}"
    if shape == 1:
        return f"(0{sep()}{area}){sep()}{rest}"
    if shape == 2:
        return f"+90{sep()}(0{area}){sep()}{rest}"
    return f"({area}){sep()}{rest}"  # no prefix: not a phone number for the masker


def _gen_phone_in_parens_into_weak_value(rng: random.Random) -> str:
    """A parenthesized spelling whose last group is the start of a spaced TCKN: the two
    candidates overlap."""
    area = f"{rng.choice([5, 2, 3, 4])}{rng.randint(0, 99):02d}"
    head = f"{rng.choice(['0 ', '+90 ', ''])}({'' if rng.random() < 0.5 else '0'}{area})"
    tckn = _gen_tckn(rng)
    tail = tckn[:2] + " " + " ".join(tckn[i : i + 2] for i in range(2, 10, 2)) + " " + tckn[10]
    return f"{head} {rng.randint(0, 999):03d} {rng.randint(0, 99):02d} {tail}"


def test_mask_phone_in_parentheses_never_takes_place_of_old_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Whatever is masked with the parenthesized spelling switched off (the masker as it was
    # before it, step 17b spellings included) is masked identically with it switched on. The
    # texts mix parenthesized area codes with every other value, and a part of them makes the
    # spelling overlap a spaced TCKN on purpose.
    rng = random.Random(2613)
    found = overlapping = 0
    for _ in range(3_000):
        parts = []
        for _ in range(rng.randint(2, 7)):
            pick = rng.random()
            if pick < 0.3:
                parts.append(_gen_phone_in_parens(rng))
            elif pick < 0.45:
                parts.append(_gen_phone_in_parens_into_weak_value(rng))
            else:
                parts.append(_gen_token(rng))
        text = "".join(part + rng.choice(_JOINTS) for part in parts)
        with monkeypatch.context() as patched:
            patched.setattr(pii, "_PHONE_PARENS", re.compile(r"(?!)"))
            old_masked, old_mapping = mask(text, {})
        new_masked, new_mapping = mask(text, {})
        old = _masked_spans(text, old_masked, old_mapping)
        new = _masked_spans(text, new_masked, new_mapping)
        assert old <= new, (text, sorted(old - new))
        assert unmask(new_masked, new_mapping) == text
        found += sum("(" in value for _, _, value in new - old)
        candidates = [(m.start(1), m.end(1)) for m in pii._PHONE_PARENS.finditer(text)]
        overlapping += any(
            start < o_end and o_start < end
            for start, end in candidates
            for o_start, o_end, _ in old
        )
        # The same through the call pattern of `prepare_contract`: whole text, then each line
        # with the threaded mapping.
        with monkeypatch.context() as patched:
            patched.setattr(pii, "_PHONE_PARENS", re.compile(r"(?!)"))
            old_lines = _mask_threaded(text)
        for (line, old_line, old_map), (_, new_line, new_map) in zip(
            old_lines, _mask_threaded(text), strict=True
        ):
            old_spans = _masked_spans(line, old_line, old_map)
            assert old_spans <= _masked_spans(line, new_line, new_map), (text, line)
            assert unmask(new_line, new_map) == line
    # The generator must produce the spelling under test and the overlaps, or the test proves
    # nothing.
    assert found > 500
    assert overlapping > 100


_GEN_NAMES = ("Ahmet Yılmaz", "Elif Soylu", "Kemal Uçar", "Hakan Töre", "AHMET YILMAZ")
_GEN_OTHER_RUNS = (
    "Satış Birimi",
    "Acme Teknoloji",
    "Genel Müdür",
    "Tedarikçi Kaşesi",
    "Ahmet Yılmaz İnşaat A.Ş.",
    "Kemal",
)
_GEN_LOCALS = ("ahmet.yilmaz", "elif.soylu", "kemal.ucar", "hakan.tore", "satis.birimi", "info")
_GEN_SIGNATURES = ("İmza:", "IMZA", "İmza / Kaşe:", "  İmza: ____", "İmzası alınır")


def _gen_name_token(rng: random.Random) -> str:
    pick = rng.randrange(6)
    if pick == 0:
        return rng.choice(_GEN_NAMES)
    if pick == 1:
        return rng.choice(_GEN_OTHER_RUNS)
    if pick == 2:
        return rng.choice(_GEN_LOCALS) + "@ornek.com"
    if pick == 3:
        line = rng.choice(_GEN_NAMES + _GEN_OTHER_RUNS + ("", "Tel: 0532 123 45 67"))
        return line + rng.choice(("\n", "\n\n", " ")) + rng.choice(_GEN_SIGNATURES)
    if pick == 4:
        return "Ad Soyad: " + rng.choice(_GEN_NAMES + _GEN_OTHER_RUNS)
    return _gen_token(rng)


def test_mask_email_and_signature_names_never_take_place_of_old_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With the two step 19c discovery paths switched off in memory, every masked (start, end,
    # raw value) is masked identically with them on, or lies inside a new name; numbers, e-mails
    # and IBANs are masked byte for byte the same.
    rng = random.Random(1903)
    # "-", ".", "_", "+" and "%" glue a name to an e-mail's local part ("Elif Soylu-elif@..."):
    # the e-mail must keep its place against a newly discovered name.
    joints = (" ", ", ", "\n", "\n", "\n\n", " (", ") ", "'a ", "", "-", ".", "_", "+", "%")
    by_email = by_signature = 0
    for _ in range(3_000):
        parts = [_gen_name_token(rng) for _ in range(rng.randint(2, 7))]
        text = "".join(part + rng.choice(joints) for part in parts)
        with monkeypatch.context() as patched:
            patched.setattr(pii, "_names_from_emails", lambda _text: iter(()))
            patched.setattr(pii, "_names_from_signature_lines", lambda _text: iter(()))
            old_masked, old_mapping = mask(text, {})
        new_masked, new_mapping = mask(text, {})
        old = _masked_spans(text, old_masked, old_mapping)
        new = _masked_spans(text, new_masked, new_mapping)
        old_kind = {value: key[1 : key.rindex("_")] for key, value in old_mapping.items()}
        new_kind = {value: key[1 : key.rindex("_")] for key, value in new_mapping.items()}
        assert {s for s in old if old_kind[s[2]] != "KISI"} == {
            s for s in new if new_kind[s[2]] != "KISI"
        }, text
        names = [(start, end) for start, end, value in new if new_kind[value] == "KISI"]
        for span in old - new:
            assert any(start <= span[0] and span[1] <= end for start, end in names), (text, span)
        assert unmask(new_masked, new_mapping) == text
        if new != old:
            by_email += any(pii._names_from_emails(text))
            by_signature += any(pii._names_from_signature_lines(text))
    # The generator must reach both paths, or the test proves nothing.
    assert by_email > 50
    assert by_signature > 50


def _gen_glued_name(rng: random.Random) -> str:
    """A name, labelled or not, glued to an e-mail or a number (step 19d)."""
    name = rng.choice(_GEN_NAMES)
    head = rng.choice(("Ad Soyad: ", "Yetkili: ", "temsilen ", "", ""))
    glue = rng.choice("-._+% ") if rng.random() < 0.8 else ""
    tail = rng.choice(
        (
            rng.choice(_GEN_LOCALS) + "@ornek.com",
            "elif@ornek.com",
            "0532 123 45 67",
            "21473873960",
            "TR33 0006 1005 1978 6457 8413 26",
        )
    )
    return head + name + glue + tail


def test_mask_names_never_take_place_of_strict_value(monkeypatch: pytest.MonkeyPatch) -> None:
    # Step 19d: every strict value that is not a name (e-mail, compact or standard-grouped
    # IBAN, prefixed phone, compact TCKN) masked with no name known is masked byte for byte the
    # same with names, whether a name is labelled, found by a step 19c path or given in the
    # mapping. Text that is raw with names but masked without them belongs to a name that gave
    # way.
    rng = random.Random(1904)
    joints = (" ", ", ", "\n", "\n\n", " (", ") ", "", "-", ".", "_", "+", "%")
    gave_way = 0
    for i in range(3_000):
        parts = [
            _gen_glued_name(rng) if rng.random() < 0.5 else _gen_name_token(rng)
            for _ in range(rng.randint(2, 6))
        ]
        text = "".join(part + rng.choice(joints) for part in parts)
        mapping = {"[KISI_1]": rng.choice(_GEN_NAMES)} if i % 3 == 0 else {}
        with monkeypatch.context() as patched:
            for path in ("_names_from_labels", "_names_from_emails", "_names_from_signature_lines"):
                patched.setattr(pii, path, lambda _text: iter(()))
            plain_masked, plain_mapping = mask(text, {})
        masked, result = mask(text, mapping)
        plain = _masked_spans(text, plain_masked, plain_mapping)
        spans = _masked_spans(text, masked, result)
        kinds = {value: key[1 : key.rindex("_")] for key, value in result.items()}
        plain_kinds = {value: key[1 : key.rindex("_")] for key, value in plain_mapping.items()}
        strict = {
            s
            for s in plain
            if not pii._is_weak(plain_kinds[s[2]], s[2])
            and not pii._in_parentheses(plain_kinds[s[2]], s[2])
        }
        assert strict <= spans, (text, sorted(strict - spans))
        assert unmask(masked, result) == text
        name_spans = [s for s in spans if kinds[s[2]] == "KISI"]
        assert not any(a < e and s < b for a, b, _ in name_spans for s, e, _ in strict), text
        # A known name written into a strict value: the case the rule decides.
        gave_way += any(
            text.startswith(name, p)
            for name in _GEN_NAMES
            for s, _, _ in strict
            for p in range(max(s - len(name) + 1, 0), s)
        )
    # The generator must write names into strict values, or the test proves nothing.
    assert gave_way > 100
