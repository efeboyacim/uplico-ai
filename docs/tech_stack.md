# Teknoloji Yığını

> Bir şey eklerken buradaki seçimler tercih edilir. "KULLANMA" listesi, çalışan ama projeye
> uymayan kütüphanelerin eklenmesini engeller. Sürümler ve Groq bilgileri 2026-11-10 tarihinde
> kaynağından doğrulandı (aşağıda "Groq: doğrulanan bilgiler").

## Dil ve çalışma ortamı
- Dil / sürüm: Python 3.12
- Paket yöneticisi: uv (`pyproject.toml` + `uv.lock`); proje paket olarak build edilmez
  (`[tool.uv] package = false`)
- OS / container: Docker, `python:3.12-slim`; `docker compose up` ile tek komut

## LLM & agent katmanı
İki mod vardır. Fark yalnızca sohbet modelindedir; graph, tool'lar, maskeleme ve doğrulama aynıdır.

| Konu | Seçim | Not |
|---|---|---|
| **Varsayılan mod** | Groq (`LLM_PROVIDER=groq`) | GPU gerektirmez, hızlı. **Maskeli sözleşme metni Groq'a gider.** |
| Ana model (Groq) | `openai/gpt-oss-120b` | Production model, tool calling, 131K bağlam. Ucuz alternatif: `openai/gpt-oss-20b` |
| **Çevrimdışı mod** | Ollama (`LLM_PROVIDER=ollama`) | LangSmith kapalıyken veri makineden çıkmaz. İnternetsiz (banka) kurulum budur. |
| Ana model (Ollama) | `qwen3.5:9b` | ≤ 8 GB VRAM'e sığar, tool calling. Yedek: `qwen3.5:4b` |
| Agent framework | LangChain v1 `create_agent` + LangGraph `StateGraph` | Kural agent'ı `create_agent`; akış LangGraph. |
| Tool protokolü | Native tool calling (`@tool`) | MCP yok. Tool'lar sırayla çağrılır; paralel tool çağrısına güvenilmez. |
| Model kurulumu | `init_chat_model(model, model_provider=...)` | Sağlayıcı değişimi tek yerde: `src/llm.py`. Sağlayıcı ve model ayrı env'lerdir. |
| Arama | Yalnızca BM25: `rank-bm25` (doğrudan, `BM25Plus`) | `langchain-community` arşivlendi; sarmalayıcı kullanılmaz. İndeks inceleme başına bellekte kurulur; paylaşılan indeks yok. |

### Groq: doğrulanan bilgiler (2026-11-10)
| Konu | Bulgu | Kaynak |
|---|---|---|
| Kendi kendine erişilebilen sohbet modelleri | Production: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`. Preview: `qwen/qwen3.8-27b` (Groq: preview modeller production'da kullanılmamalı). | console.groq.com/docs/models |
| Llama modelleri | `llama-3.3-70b-versatile` ve `llama-3.1-8b-instant` artık "Enterprise" etiketli; fiyat ve limit "Contact Sales". **Kullanılmaz.** | console.groq.com/docs/models |
| Tool calling | Groq'taki tüm modeller destekler. `gpt-oss` modellerinde **paralel tool çağrısı yok**; JSON modu var. | console.groq.com/docs/tool-use |
| Fiyat | `gpt-oss-120b`: 1M token başına $0.15 girdi / $0.60 çıktı. `gpt-oss-20b`: $0.075 / $0.30. | console.groq.com/docs/models |
| Hız limiti — düşük tablo | `gpt-oss-120b` ve `gpt-oss-20b`: 30 RPM, 1K RPD, **8K TPM, 200K TPD** | console.groq.com/docs/rate-limits |
| Hız limiti — yüksek tablo | `gpt-oss-120b` ve `gpt-oss-20b`: 1K RPM, 250K TPM ("Developer Plan" başlığı altında) | console.groq.com/docs/models |
| **Hangi tablo hangi plan?** | **Kaynaklar çelişiyor.** Hız limitleri sayfasında "Free Plan" ve "Developer Plan" sekmeleri var, ama düşük tablonun üstündeki cümle onu "Developer plan'ın temel limitleri" diye tanımlıyor; models sayfası ise Developer plan için yüksek değerleri veriyor. En olası okuma: düşük tablo Free plan. Bu okuma doğrulandı: geliştirme hesabı ücretsiz katmandadır ve Limits sayfası düşük tabloyu gösterir (2026-11-12). Kesin değer hesabın Limits sayfasından okunur. | iki sayfa birlikte |
| Limit aşımı | HTTP **429**; `retry-after` başlığı saniye cinsinden bekleme süresini verir. Limitler anahtar başına değil **organizasyon** başınadır. | console.groq.com/docs/rate-limits |
| SDK yeniden denemesi | `groq` Python SDK'sı 408 / 409 / 429 / 5xx'i yeniden dener. `retry-after` başlığına **60 sn'ye kadar** uyar; daha uzunsa en fazla 8 sn'lik üstel beklemeye düşer. Varsayılan 2 deneme, 60 sn zaman aşımı. | github.com/groq/groq-python (`_base_client.py`, `_constants.py`) |
| Veri saklama | "Varsayılan olarak çıkarım isteklerinde müşteri verisi saklanmaz." İstisna: güvenilirlik ve kötüye kullanım incelemesi için girdi/çıktı **30 güne kadar** loglanabilir. | console.groq.com/docs/your-data |
| Zero Data Retention | Konsolda Data Controls'tan açılır; bu loglamayı da kapatır. | console.groq.com/docs/your-data |
| Veri konumu | Saklanan veri ABD'de (Google Cloud). | console.groq.com/docs/your-data |
| Eğitimde kullanım | Hizmet sözleşmesi §4.2: Groq girdi ve çıktıları model eğitimi için kullanamaz (son değişiklik 2026-06-22). | console.groq.com/docs/legal/services-agreement |
| Reasoning | `gpt-oss`: `reasoning_effort` = `low` / `medium` / `high`. `reasoning_format` **desteklenmez**. | console.groq.com/docs/reasoning |
| Varsayılanlar | `max_completion_tokens` 1024, `temperature` 0.6 — ikisi de açıkça ayarlanmalı. | console.groq.com/docs/reasoning |
| LangChain | `langchain-groq` 1.1.3; `init_chat_model` `groq` sağlayıcısını destekler; anahtar `GROQ_API_KEY`. | pypi.org, reference.langchain.com |

Doğrulanamayan `spec.md` §10'da açık sorudur: `gpt-oss-120b`'nin Türkçe kalitesi.

### Hangi plan?
- **Geliştirme hesabı ücretsiz katmandadır:** `gpt-oss-120b` için **30 RPM, 1K RPD, 8K TPM, 200K
  TPD** (Limits sayfası ve yanıt başlıkları, 2026-11-12). Burada daha önce "250K TPM teyit edildi"
  yazıyordu (2026-11-10); bu anahtar için yanlıştı. Tasarım başta ona göreydi (varsayılan
  `MAX_CONCURRENCY=4`): gerçek çağrıda 5 kuraldan 3'ü `rate_limited` oldu, varsayılan 1 yapıldı.
- **Ölçülen** (Adım 12, örnek sözleşme, tek koşu): sözleşme başına ~25K token ve 15 model çağrısı,
  112 sn. Önce/sonra tablosu ikinci bir geçiş istemez: model sözleşme başına bir kez çalışır, iki
  satır aynı ham bulgulardan kodla hesaplanır (`evals.md` §5).
- **Ölçülen** (Adım 18, 7 sözleşmelik tam eval, iki koşu, 2026-11-12, `MAX_CONCURRENCY=1`):
  206.387 token / 576 sn (taslak 1 / 11) ve 241.576 token / 813 sn (taslak 11 / 11); sözleşme
  başına 23,5K–39,5K, ilk üç sözleşme ≈ 105K. Taslaklarla **günlük 200K sınırı aşıldı**. Maliyet
  koşu başına $0,035–0,041 (birim fiyatla hesaplanan).
- **Limit gözlemi (Adım 18):** 2026-11-12'de bu anahtarla toplam ≈ 473K token harcandı (≈ 25K
  tek sözleşmelik deneme + 206K + 242K); koşular dakikada ≈ 18–21K token hızla gitti, hiç 429
  gelmedi. Yukarıdaki 8K TPM / 200K TPD bu anahtarda o gün uygulanmadı; sebebi bilinmiyor (limit
  ya da hesap katmanı değişmiş olabilir; Limits sayfası bu oturumda kontrol edilmedi). **Geçerli
  limit bilinmiyor**; aşağıdaki ücretsiz katman rehberi koşullu olarak kalır.
- **Maliyet hesabı:** eval'in birim fiyatları `src/config.py`'de `PROVIDER_DEFAULTS` içinde,
  sağlayıcının varsayılan modelinin yanında durur (`groq`: 1M token başına $0.15 girdi / $0.60
  çıktı; `ollama`: 0). Model `LLM_MODEL` ile ezilirse maliyet "bilinmiyor" yazılır; başka modelin
  fiyatı tahmin edilmez.
- **Bu yüzden eval** (200K TPD uygulanıyorsa) `MAX_CONCURRENCY=1` ve `--limit N` ile kısmi
  koşulur (N=3 ≈ 105K), `--resume` ile iki güne bölünür ya da çevrimdışı modda koşulur. Yüksek
  limitli (250K TPM) bir hesapta tam eval birkaç dakikada biter.
  README bunu yazar.

## Uygulama katmanı
- API: FastAPI + uvicorn (senkron endpoint'ler); `python -m src.api` `0.0.0.0:8000`'de dinler
- Veritabanı: SQLite (stdlib `sqlite3`) — tenant başına ayrı dosya
- Graph state: `langgraph-checkpoint-sqlite` (`SqliteSaver`) — tenant başına ayrı dosya,
  `sqlite3.connect(path, check_same_thread=False)` ile
- Kuyruk / arka plan işleri: Yok (istek bloklar)
- Frontend: Tek statik HTML dosyası + sade JavaScript (build adımı yok)
- Doğrulama / şema: pydantic v2
- Kural setleri: YAML (`pyyaml`)

## Başlangıç ve hata davranışı
- **Anahtar yoksa:** `LLM_PROVIDER=groq` iken `GROQ_API_KEY` boşsa uygulama **başlamaz**; hangi
  değişkenin eksik olduğunu ve çevrimdışı moda nasıl geçileceğini söyleyen tek satırlık hatayla,
  sıfır olmayan çıkış koduyla durur. Kontrol `src/config.py`'dedir; kütüphanenin hatasına bırakılmaz.
  Aynı kontrol bilinmeyen `LLM_PROVIDER` ve boş `TENANT_API_KEYS` için de yapılır.
- **Hız limiti (429):** Groq modeli `max_retries=3` ile kurulur (toplam en fazla 4 istek); yeniden
  denemeyi SDK yapar, başka yerde yeniden deneme döngüsü yoktur. Denemeler tükenirse o kural
  `no_finding` (sebep `rate_limited`) olur ve avukat kuyruğuna düşer; inceleme çökmez. Eval'de tek
  bir `rate_limited` koşuyu geçersiz kılar (`evals.md` §6).
  - Günlük limit dolduğunda `retry-after` saatler olabilir; SDK 60 sn'den uzun beklemeye uymadığı
    için istek asılı kalmaz, birkaç saniye içinde `rate_limited` olur.
  - Aynı `max_retries` zaman aşımı ve 5xx için de geçerlidir; bir model çağrısı en kötü durumda
    yaklaşık 4 × 60 sn sürebilir.
- **Kimlik ya da yapılandırma hatası (401 / 403 / 404):** Yeniden denenmez (`groq` SDK'sı 401, 403
  ve 404'ü yeniden denemez) ve `no_finding`'e çevrilmez. `review_rule` bu hatayı yeniden fırlatır;
  inceleme `failed` olur, API 502 döner. Bu sınıfın (`config`) dört üyesi vardır: Groq 401
  (`groq.AuthenticationError`, geçersiz anahtar), Groq 403 (`groq.PermissionDeniedError`, örneğin
  model organizasyon ya da proje düzeyinde engelli), Groq 404 (`groq.NotFoundError`, örneğin yanlış
  `LLM_MODEL`) ve Ollama 404 (`ollama.ResponseError`, `status_code == 404`, model çekilmemiş).
  "Bir kuralın model çağrısı hatası incelemeyi çökertmez" kuralının tek istisnasıdır (model
  çağrısının dışından gelen hatalar zaten bulguya çevrilmez; `agent_design.md` §6): bu durumlarda
  hiçbir kural çalışamaz. Bağlantı reddi (Ollama konteyneri henüz
  açılıyor olabilir; `httpx.ConnectError` ya da `ConnectionError`), zaman aşımı, 5xx ve 400 bu sınıfa
  girmez; `llm_error` olarak kalır.

## Gözlemlenebilirlik & kalite
- Kayıt (asıl kaynak): sistemin kendi audit log'u (SQLite)
- Tracing: LangSmith — isteğe bağlı, yalnızca `LANGSMITH_API_KEY` varsa açılır
- Eval aracı: LangSmith `evaluate()` + kod tabanlı evaluator'lar; anahtar yoksa `upload_results=False`
- Lint/format: ruff
- Typecheck: mypy (`mypy src evals`)
- Test: pytest

## Deploy
- Ortamlar: yalnızca yerel (dev)
- Hedef: `docker compose up` — servisler:
  - `app` — her iki modda çalışır.
  - `ollama` — yalnızca çevrimdışı modda çalışır (`offline` compose profili); sohbet modelini sunar.
  - `ollama-pull` — yalnızca çevrimdışı modda çalışır (`offline` compose profili); sohbet modelini
    indirir (~7 GB). `app` imajıyla çalışır (Ollama imajında Python yok); indirilecek model adını
    `python -m src.config` basar ve indirme `ollama` Python istemcisiyle yapılır.
- Çevrimdışı modda `app`, `ollama-pull` bitene kadar bekler (`depends_on`, `required: false`;
  varsayılan modda yok sayılır). Port yalnızca `127.0.0.1`'de yayınlanır.
- Varsayılan modda yalnızca `app` başlar; Ollama kapsayıcısı çalışmaz, imajı ve modeli inmez.
- Çevrimdışı moda geçiş `.env`'de iki değişkendir: `LLM_PROVIDER=ollama` ve
  `COMPOSE_PROFILES=offline`; komut yine `docker compose up`.
- Varsayılan modda GPU gerekmez. Çevrimdışı modda Windows'ta GPU için host'taki Ollama
  kullanılabilir (`OLLAMA_BASE_URL=http://host.docker.internal:11434`).
- `app` servisinde `restart` tanımlanmaz. Anahtar eksikse `app` hatayı compose çıktısına yazıp
  çıkar; döngüye girmez. `python -m evals.run` aynı `config.py` kontrolünden geçer.

## Ortam değişkenleri (sadece isimler, değer YAZMA)
Tanımsız, boş ve yalnızca boşluk içeren değer aynıdır ("boş").
- `LLM_PROVIDER` — `groq` (varsayılan; boşsa `groq`) ya da `ollama`; başka değer başlangıç hatasıdır
- `LLM_MODEL` — boşsa sağlayıcıya göre: `groq` → `openai/gpt-oss-120b`, `ollama` → `qwen3.5:9b`.
  Sağlayıcı varsayılanları tek yerde, `src/config.py`'dedir; `.env.example`'da boş bırakılır ki
  yalnızca `LLM_PROVIDER` değiştirilince yanlış model adı kalmasın.
- `GROQ_API_KEY` — `LLM_PROVIDER=groq` iken zorunlu
- `COMPOSE_PROFILES` — çevrimdışı modda `offline` (`ollama` ve `ollama-pull` servislerini açar);
  varsayılan modda boş
- `OLLAMA_BASE_URL` — yalnızca Ollama; boşsa Compose dışında `http://localhost:11434`, Compose
  içinde `http://ollama:11434` (`docker-compose.yml` ayarlar). Host'taki Ollama için:
  `http://host.docker.internal:11434`
- `LLM_NUM_CTX` — yalnızca Ollama: bağlam uzunluğu (varsayılan 4096 çok küçük; 16384 kullanılır)
- `MAX_CONCURRENCY` — aynı anda çalışan kural agent'ı sayısı. Varsayılan iki sağlayıcıda da 1.
  Groq'ta 1 olmasının sebebi düşük limitli anahtardır (8K TPM): 4 ile örnek sözleşmede 5 kuraldan
  3'ü `rate_limited` oldu (Adım 12). Yüksek limitli hesapta (250K TPM) 4 yapılabilir; inceleme
  yaklaşık iki kat hızlanır. Verilmişse 1 ya da daha büyük
  tam sayı olmalıdır (`LLM_NUM_CTX` de öyle); değilse başlangıç hatasıdır.
- `TENANT_API_KEYS` — API anahtarı → `tenant_id` + `role` (`lawyer` / `viewer`) + `name` eşlemesi.
  Tek satır JSON nesnesi: `{"<api-anahtarı>": {"tenant_id": "…", "role": "…", "name": "…"}}`.
  `tenant_id` yalnızca küçük harf, rakam ve `_` içerir; kayıtta başka alan olamaz; aynı anahtar iki kez yazılamaz. Hatalı kayıt
  başlangıç hatasıdır; mesaj kaydı sırasıyla söyler, anahtarı yazmaz.
- `DATA_DIR` — boşsa `data`
- `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, `LANGSMITH_PROJECT`

## KULLANMA
- Groq'ta `llama-3.3-70b-versatile`, `llama-3.1-8b-instant` (Enterprise) ve preview modeller.
- `langchain-community`, `langchain-classic` — arşivlendi / eski API. `BM25Retriever`, `EnsembleRetriever` yok.
- Eski Chain API'leri ve `langgraph.prebuilt.create_react_agent` — `create_agent` kullanıyoruz.
- `HumanInTheLoopMiddleware` — insan onayı agent döngüsünde değil, graph'ta tek `interrupt()`.
- `PIIMiddleware` — maskeleme hazırlık katmanında yapılıyor; geri döndürülebilir eşleme tutmuyor.
- Presidio / spaCy — Türkçe için ağır; regex + checksum yeterli.
- Embedding / vektör araması (`OllamaEmbeddings`, `InMemoryVectorStore`, bulut embedding servisi,
  Chroma, pgvector, Qdrant) — arama yalnızca BM25'tir.
- Postgres/Redis — gerek yok.
- LLM-as-judge — tüm metrikler etiketlerden kodla hesaplanır.
- ORM (SQLAlchemy) — üç tablo için `sqlite3` yeterli.
- Elle yazılmış yeniden deneme / bekleme döngüleri — yalnızca modelin `max_retries`'ı.

## Sürüm sabitleri / kısıtlar
- `langchain>=1.4,<2`, `langgraph>=1.2,<2`, `langgraph-checkpoint-sqlite>=3.1`
- `langchain-groq>=1.1`, `langchain-ollama>=1.1`, `langsmith>=0.14`, `rank-bm25>=0.2.2`
- pydantic v2 zorunlu
- Her iki modda: `temperature=0`; model tool calling desteklemeli. `ChatGroq` `temperature=0`'ı
  API isteğine `1e-8` olarak yazar; yani "0", istekteki değerin en fazla `1e-8` olması demektir.
- Sağlayıcıya özel parametreler `src/llm.py`'de yalnızca o sağlayıcı seçiliyken verilir:
  - Groq: `max_retries=3`, `max_tokens=2048`, `reasoning_effort="low"`, zaman aşımı 60 sn.
    `reasoning_format` verilmez.
  - Ollama: `num_ctx`, `reasoning=False`, sabit `seed`, zaman aşımı 120 sn (`client_kwargs` ile).
    `ChatOllama`'da `max_retries` alanı yoktur; yerelde hız limiti de yoktur.
  - Model kurucusunun isteğe bağlı `http_client` parametresi yalnızca Groq içindir (testte sahte HTTP
    taşıyıcısı vermek için). `ollama` ile `None` olmayan değer `ValueError` fırlatır: `ChatOllama`
    onu sessizce yok sayar ve istek gerçek ağa çıkar. Yalnızca senkron çağrıları kapsar; graph
    senkron olduğu için sorun değildir. Verilse de Groq'un istek başına 60 sn zaman aşımı geçerlidir.
