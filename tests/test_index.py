import pytest

from src.models import Clause, MaskedText
from src.prepare.index import (
    ClauseIndex,
    SearchHit,
    drop_index,
    get_index,
    put_index,
    tokenize,
)


def _clause(clause_id: str, text: str, clause_no: str | None = None) -> Clause:
    return Clause(
        clause_id=clause_id,
        clause_no=clause_no,
        start=0,
        end=len(text),
        text=MaskedText(text),
    )


def _index(*texts: str) -> ClauseIndex:
    return ClauseIndex([_clause(f"c{i}", t, str(i)) for i, t in enumerate(texts, start=1)])


def _shares_key(a: str, b: str) -> bool:
    return bool(set(tokenize(a)) & set(tokenize(b)))


_REALISTIC = (
    "Taraflar bu sözleşmeyi imzalamakla yükümlülüklerini kabul eder.",
    "Ödeme süresi fatura tarihinden itibaren 60 gündür.",
    "Teslimat gecikirse satıcı günlük binde bir oranında cezai şart öder.",
    "Gizlilik yükümlülüğü sözleşme sona erdikten sonra da devam eder.",
    "Uyuşmazlıklarda İstanbul mahkemeleri yetkilidir.",
    "Alıcı ürünleri teslim aldığında muayene eder.",
    "Sözleşme bir yıl süreyle geçerlidir ve yenilenebilir.",
    "Bildirimler yazılı olarak adrese gönderilir.",
)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("süre", "süresi"),
        ("ceza", "cezası"),
        ("gün", "günlük"),
        ("bildirim", "bildirimle"),
        ("fesih", "feshedebilir"),
        ("borç", "borcu"),
    ],
)
def test_tokenize_related_forms_share_a_key(a: str, b: str) -> None:
    assert _shares_key(a, b)


@pytest.mark.parametrize(("a", "b"), [("ret", "reddi"), ("ay", "aylık")])
def test_tokenize_known_limits_share_no_key(a: str, b: str) -> None:
    assert not _shares_key(a, b)


def test_tokenize_single_word_emits_exact_prefixes() -> None:
    assert tokenize("süresi") == ["sür", "süre", "süres"]


def test_tokenize_short_words_emit_themselves() -> None:
    assert tokenize("ay") == ["ay"]
    assert tokenize("gün") == ["gün"]
    assert tokenize("süre") == ["sür", "süre"]


@pytest.mark.parametrize(
    ("upper", "lower"),
    [("SÜRESİ", "süre"), ("FESİH", "fesih"), ("IŞIK", "ışık")],
)
def test_tokenize_turkish_upper_case_matches_lower_case(upper: str, lower: str) -> None:
    assert _shares_key(upper, lower)


def test_search_realistic_set_returns_right_clause_first() -> None:
    index = _index(*_REALISTIC)
    assert index.search("ödeme süresi")[0].clause_id == "c2"
    assert index.search("gecikme cezası")[0].clause_id == "c3"
    assert index.search("gizlilik")[0].clause_id == "c4"


def test_search_many_matches_returns_at_most_five() -> None:
    index = _index(*(f"Madde sözleşme ödeme {i}" for i in range(12)))
    assert len(index.search("sözleşme")) == 5
    assert len(index.search("sözleşme", limit=2)) == 2


def test_search_long_clause_truncates_snippet_to_300_chars() -> None:
    text = "ödeme " + "abcdefghij" * 100
    index = _index(text)
    hit = index.search("ödeme")[0]
    assert hit.snippet == text[:300]
    assert len(hit.snippet) == 300


def test_search_placeholders_produce_no_keys() -> None:
    index = _index("[KISI_1] [TELEFON_1]")
    assert index.search("kısıtlama telafi 1") == []


def test_search_clause_with_placeholder_found_by_its_real_words() -> None:
    text = "Bildirimler [KISI_1] adına [TELEFON_1] numarasına yapılır."
    index = _index(text, "Teslimat adrese yapılır.")
    assert index.search("kısıtlama telafi") == []
    assert index.search("bildirim") == [SearchHit(clause_id="c1", clause_no="1", snippet=text)]


def test_search_query_of_only_a_placeholder_returns_empty() -> None:
    index = _index("Bildirimler [KISI_1] adına yapılır.")
    assert index.search("[KISI_1]") == []


def test_search_hit_carries_clause_id_and_clause_no() -> None:
    index = _index("Ödeme süresi 60 gündür.")
    assert index.search("ödeme") == [
        SearchHit(clause_id="c1", clause_no="1", snippet="Ödeme süresi 60 gündür.")
    ]


def test_search_two_clauses_skips_clause_sharing_no_key() -> None:
    index = _index("Ceza oranı binde bir olarak uygulanır.", "Teslimat adrese yapılır.")
    hits = index.search("ceza")
    assert [h.clause_id for h in hits] == ["c1"]


def test_search_term_in_every_clause_still_returns_hits() -> None:
    index = _index("Ödeme birinci madde.", "Ödeme ikinci madde.", "Ödeme üçüncü madde.")
    assert [h.clause_id for h in index.search("ödeme")] == ["c1", "c2", "c3"]


def test_search_no_shared_key_returns_empty() -> None:
    assert _index(*_REALISTIC).search("zeytinyağı") == []


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_search_blank_query_returns_empty(query: str) -> None:
    assert _index(*_REALISTIC).search(query) == []


def test_search_zero_clauses_returns_empty() -> None:
    index = ClauseIndex([])
    assert index.search("ödeme") == []
    assert len(index) == 0


def test_get_known_id_returns_clause_and_unknown_returns_none() -> None:
    index = _index("Ödeme süresi.", "Gizlilik.")
    assert len(index) == 2
    found = index.get("c2")
    assert found is not None
    assert found.text == "Gizlilik."
    assert index.get("yok") is None


def test_registry_put_then_get_returns_same_object() -> None:
    index = _index("Ödeme.")
    put_index("acme", "r1", index)
    assert get_index("acme", "r1") is index


def test_registry_drop_removes_and_double_drop_is_silent() -> None:
    put_index("acme", "r1", _index("Ödeme."))
    drop_index("acme", "r1")
    assert get_index("acme", "r1") is None
    drop_index("acme", "r1")


def test_registry_same_review_id_under_two_tenants_stays_separate() -> None:
    acme = _index("Ödeme.")
    globex = _index("Gizlilik.")
    put_index("acme", "r1", acme)
    put_index("globex", "r1", globex)
    assert get_index("acme", "r1") is acme
    assert get_index("globex", "r1") is globex
    drop_index("acme", "r1")
    assert get_index("acme", "r1") is None
    assert get_index("globex", "r1") is globex
