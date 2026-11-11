import time

import pytest

from src.prepare.pii import mask, unmask


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
        "Tel: 532 123 45 67",
        "Tel: 90 532 123 45 67",
    ],
)
def test_mask_phone_double_separator_or_no_prefix_not_masked(text: str) -> None:
    assert _mask(text) == (text, {})


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
        "TR33 000 61005 1978 6457 8413 26",  # irregular grouping (documented limit)
        "TR33 0006 1005 1978\n6457 8413 26",  # split over two lines (documented limit)
        "TR33-0006-1005-1978-6457-8413-26",
    ],
)
def test_mask_iban_bad_checksum_or_irregular_grouping_not_masked(iban: str) -> None:
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


def test_mask_unlabelled_name_everywhere_nothing_masked_documented_limit() -> None:
    text = _BODY + "AHMET YILMAZ\nİmza:"
    assert mask(text, {}) == (text, {})


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
