# CLAUDE.md

## Proje
Tedarikçi sözleşmelerini şirketin kendi kural setine göre inceleyen bir agent sistemi (`docs/CASE.md`).
Her kural için sonuç uygun / uygun değil / bulunamadı olur ve sözleşmeden birebir alıntıyla
kanıtlanır; uygun olmayan maddeler için değişiklik taslağı yazılır, belirsiz bulgular avukata
bırakılır. Python, LangChain `create_agent`, LangGraph ve LangSmith ile kurulur; varsayılan model
sağlayıcısı Groq, çevrimdışı mod Ollama'dır. İki kurgusal şirketin (tenant) verisi ayrı tutulur.

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

**Şu an:** Adım 0–4 bitti (Adım 4: `src/text.py`, `src/prepare/clauses.py`,
`src/prepare/injection.py`).
Sıradaki: Adım 5 — Hazırlık: PII maskeleme.

## Komutlar
```bash
docker compose up                                        # çalıştır
uv run pytest                                            # test
docker compose run --rm app python -m evals.run          # eval (önce/sonra tablosu dahil)
uv run ruff check . && uv run ruff format --check .      # lint + format kontrolü
uv run mypy src                                          # type-check
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
