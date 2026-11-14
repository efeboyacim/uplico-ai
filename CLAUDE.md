# CLAUDE.md

## Proje
Tedarikçi sözleşmelerini şirketin kendi kural setine göre inceleyen bir agent sistemi (`docs/CASE.md`).
Her kural için sonuç uygun / uygun değil / bulunamadı olur ve sözleşmeden birebir alıntıyla
kanıtlanır; uygun olmayan maddeler için değişiklik taslağı yazılır, belirsiz bulgular avukata
bırakılır. Python, LangChain `create_agent`, LangGraph ve LangSmith ile kurulur; varsayılan model
sağlayıcısı Groq, çevrimdışı mod Ollama'dır; başka bir sağlayıcı yalnızca env ile seçilir (kod
değişmez; Groq üç, Anthropic `claude-sonnet-5-5` bir tam eval koşusuyla ölçüldü). İki kurgusal şirketin (tenant) verisi ayrı tutulur.

## Ana ilke
**LLM önerir, kod karar verir, yalnızca avukat onaylar.**

## Hangi konu hangi dokümanda
| Dosya | İçerik |
|---|---|
| `docs/CASE.md` | Case'in asıl metni: istenenler ve teslim edilecekler; gereksinimlerin kaynağı |
| `docs/spec.md` | Ne ve neden: senaryolar, gereksinimler, kapsam dışı, bilinen sınırlar, açık sorular |
| `docs/architecture.md` | Katmanlar, veri akışı, API uçları, karar şeması, rapor, roller, veri nereye gidiyor, ADR'ler |
| `docs/agent_design.md` | Graph, 5 tool, prompt, doğrulama sinyalleri, maskeleme, enjeksiyon, hata davranışı, audit olayları |
| `docs/tech_stack.md` | Kütüphaneler ve sürümler, modeller, Groq bilgileri, env değişkenleri, KULLANMA listesi |
| `docs/evals.md` | "Bitti" tanımı, zorunlu unit testler, eval dataset'i, metrikler ve eşikler |
| `docs/code_standards.md` | Kod kuralları: hata yönetimi, LLM/agent kodu, avukat aşaması, test, güvenlik, git |
| `docs/plan.md` | Uygulama planı: sıralı adımlar, her adımın dosyaları, doğrulaması ve commit'i; ilerleme burada işaretlenir |

Bir işe başlamadan önce ilgili dokümanı oku; buradaki özet onun yerini tutmaz.

## İlerleme
Uygulama `docs/plan.md`'deki adımlarla, sırayla yazılır. Hangi adımda olunduğu o dosyanın "Durum"
bölümünde ve adım kutularında yazar; bir oturuma başlarken önce oraya bak. Bir adım doğrulanıp
commit edilince dur ve sıradaki adıma geçmeden önce kullanıcıdan onay al.

**Şu an:** Adım 0–19b bitti (Adım 17: `evals/metrics.py` ve `evals/run.py`; tek komut önce/sonra
tablosunu basar, `--limit N` ve `--prepare-only` seçenekleri var).
Adım 17 sonrası: `--resume` (içerik özetli önbellek) ve Adım 17b (maskeleyici üç biçim daha
yakalar; garanti edilmeyen PII 5 / 5 → 2 / 5).
Ardından `verify`'a `masked_value_in_clause` sinyali eklendi ("uygun" bulgunun maddesinde TCKN /
telefon yer tutucusu varsa avukata gider).
Adım 18: Groq ile iki tam eval koşusu (2026-11-12); ilki iki hata gösterdi, ikisi ayrı `fix:`
commit'iyle düzeltildi (`verify`'da cümle sınırını aşan birim; taslak yönlendirmesi, 1 / 11 →
11 / 11). İkinci koşuda bütün sert eşikler tuttu (değiştirilmedi); iki yanlış sözleşme 4'teki
arama kaçışı (BM25 sınırı), sonuçlar `docs/evals.md` §4–§5'te.
Adım 18b (görülmemiş iki sözleşmede tek koşu) bitti: 10 / 10 karar doğru, 4'ü avukata gitti;
hiçbir şey düzeltilmedi (`docs/evals.md` §4). Adım 19 bitti: README ve karar dokümanı; temiz
klonda modelsiz yürüyüş ve gerçek modelle tek uçtan uca akış doğrulandı.
Ardından salt okunur kod incelemesinin bulduğu üç hata düzeltildi (2026-11-13): `verify` alıntı
dışındaki değerleri maddeyi bütün ve parça parça okuyarak görür, alan kodu parantezli telefon
maskelenir, BOM'dan sonraki ilk başlık tanınır.
Adım 18c bitti (2026-11-13): sağlayıcı, model, anahtar ve adres yalnızca env ile seçilir
(`LLM_PROVIDER` kapalı liste değildir; `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_TEMPERATURE`,
`LLM_MAX_TOKENS`, build argümanı `LLM_EXTRAS`); Groq'a giden istek adımdan önceki koda göre
değişmedi. Gemini ile elle deneme (OpenAI uyumlu adres) incelemeyi uçtan uca tamamlamadı (`docs/plan.md` Adım 18c "Elle deneme").
Adım 18d bitti (2026-11-13): `google` paket grubu (`langchain-google-genai`) kilide ve varsayılan
imaja girdi, `google_genai` bilinen sağlayıcı tablosunda; herkesin kurduğu `langchain-core`
1.7.7 → 1.8.0 oldu (Groq / Ollama yolu sahte taşıyıcılarla karşılaştırıldı, fark yok). Gemini
`google_genai` ile tek sözleşmede denendi, ölçülmedi: inceleme avukat kuyruğuna ulaştı, status
5'te 4 doğru, bir kural ücretsiz katman hız limitiyle kesildi (`docs/plan.md` Adım 18d "Elle
deneme").
Eval yeniden koşuldu (2026-11-13, bugünkü kod): Groq'ta üçüncü tam koşu (`langchain-core` 1.8.0;
bütün sert eşikler tuttu, aynı iki yanlış satır) ve Anthropic `claude-sonnet-5-5` ile bir tam koşu
(35 / 35; başka model, Groq'la karşılaştırılmaz). Sonnet varsayılan `LLM_TEMPERATURE` (0) ile
çalışmaz, `none` ile çalıştı (başka değer denenmedi): 0 ile her çağrı yerelde `ValueError` verir, başlangıç kontrolü yakalamaz.
Adım 19b (madde listesi denemesi) kullanıcının kararıyla geri alındı; iş `exp/clause-outline`
dalında, `main`'de yok. Sonuçlar `docs/evals.md` §4, `docs/plan.md` Adım 18 "Ek koşular" ve 19b.
Sıradaki: Adım 19c — Sonnet koşusunun gösterdiği üç hata (kod denetiminden kalan uygun taslaklar,
maskelenmeyen iki ad, denetlenemeyen taslağın avukata gitmemesi) düzeltilir ve Sonnet ile yeniden
ölçülür. Plan `docs/plan.md` "Adım 19c"de eksiksiz yazılıdır; `fable-orchestration` skill'iyle,
`step-19c` dalında yürütülür; araştırma kayıtları `.claude/plans/19c/` altındadır (commit edilmez).
Sonra Adım 20 — teslim (kullanıcı onayıyla push). Ayrıntı `docs/plan.md` "Durum".

## Komutlar
```bash
docker compose up                                        # çalıştır
uv run pytest                                            # test
docker compose run --rm app python -m evals.run          # eval (önce/sonra tablosu dahil)
uv run ruff check . && uv run ruff format --check .      # lint + format kontrolü
uv run mypy src evals                                    # type-check
```

## Commit mesajları
İngilizce yazılır; conventional commits biçimindedir (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
Ayrıntı: `docs/code_standards.md` "Git".

## Asla çiğnenmeyecek kurallar
- **Tenant ayrımı:** tenant ve rol yalnızca API anahtarından çözülür; istekteki hiçbir alandan
  okunmaz. Tüm veri erişimi tenant'ın kendi alanı üzerinden yapılır.
- **Modele maskesiz PII gitmez:** maskeleme graph'tan önce yapılır; model, indeks, checkpoint, audit
  log ve trace yalnızca maskeli metin görür. Maskelemeyi atlayan bir kod yolu yazılmaz.
- **Audit log uygulama üzerinden değiştirilemez:** tabloya yalnızca `INSERT` yazılır; `UPDATE` ve
  `DELETE`'i reddeden tetikleyiciler düşürülmez ya da atlanmaz.
- **Onay tool'u yok:** agent'a onay, değişiklik talebi ya da karar veren bir tool eklenmez. Karar
  yalnızca `lawyer` rolündeki anahtarla, API üzerinden verilir.

Ayrıntılar: `docs/code_standards.md` "Güvenlik" ve `docs/architecture.md` §3.

## Dokümanlar esas alınır
Kod `docs/` altındaki kararlara göre yazılır. Kod yazarken dokümandaki bir karardan sapman
gerekirse **önce ilgili dokümanı güncelle, sonra kodu yaz**; sapmayı sessizce kodda bırakma.
