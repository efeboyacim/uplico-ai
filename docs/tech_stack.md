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
İki mod vardır: varsayılan mod (Groq) ve çevrimdışı mod (Ollama). Sohbet modeli env ile seçilir;
kodda kapalı bir sağlayıcı listesi yoktur, `init_chat_model`'in tanıdığı başka bir sağlayıcı da env
ile seçilebilir (aşağıda "Sürüm sabitleri / kısıtlar", "Bilinen sağlayıcı tablosu"). Fark yalnızca
sohbet modelindedir; graph, tool'lar, maskeleme ve doğrulama her sağlayıcıda aynıdır. **Varsayılan
Groq `openai/gpt-oss-120b` üç tam eval koşusuyla, Anthropic `claude-sonnet-5-5` bir tam koşuyla
ölçüldü** (`evals.md` §4; Sonnet `LLM_TEMPERATURE=none` ister, aşağıda); diğerleri "çalışır,
ölçülmedi"dir; `google_genai` tek sözleşmede denendi (2026-11-13), ölçülmedi (`plan.md` Adım 18d
"Elle deneme").

| Konu | Seçim | Not |
|---|---|---|
| **Varsayılan mod** | Groq (`LLM_PROVIDER=groq`) | GPU gerektirmez, hızlı. **Maskeli sözleşme metni Groq'a gider.** |
| Ana model (Groq) | `openai/gpt-oss-120b` | Production model, tool calling, 131K bağlam. Ucuz alternatif: `openai/gpt-oss-20b` |
| **Çevrimdışı mod** | Ollama (`LLM_PROVIDER=ollama`) | LangSmith kapalıyken veri makineden çıkmaz. İnternetsiz (banka) kurulum budur. |
| Ana model (Ollama) | `qwen3.5:9b` | ≤ 8 GB VRAM'e sığar, tool calling. Yedek: `qwen3.5:4b` |
| Agent framework | LangChain v1 `create_agent` + LangGraph `StateGraph` | Kural agent'ı `create_agent`; akış LangGraph. |
| Tool protokolü | Native tool calling (`@tool`) | MCP yok. Tool'lar sırayla çağrılır; paralel tool çağrısına güvenilmez. |
| Diğer sağlayıcılar | `LLM_PROVIDER` = `init_chat_model` sağlayıcı adı (ör. `openai`, `anthropic`, `google_genai`) + `LLM_MODEL` | `openai` ve diğerleri: çalışır, ölçülmedi. `anthropic`: `claude-sonnet-5-5` bir tam eval koşusuyla ölçüldü (2026-11-13, `LLM_TEMPERATURE=none` ile; `evals.md` §4 "Başka bir model"), başka Anthropic modeli denenmedi. `google_genai`: tek sözleşmede denendi (2026-11-13), ölçülmedi (`plan.md` Adım 18d "Elle deneme"); `gemini-3.5-flash-lite` ile entegrasyon `temperature`'ı yok sayar (aşağıda `LLM_TEMPERATURE`). **Maskeli sözleşme metni seçilen sağlayıcıya gider**; saklama koşulları kullanıcının sorumluluğundadır. Gemini kendi entegrasyonuyla seçilir (`LLM_PROVIDER=google_genai` + `LLM_MODEL` + `LLM_API_KEY`; `google` paket grubu, Gemini Developer API). Google'ın OpenAI uyumlu adresiyle (`LLM_PROVIDER=openai` + `LLM_BASE_URL`) 2026-11-13 denemesinde inceleme tamamlanmadı (Gemini 3.x tool çağrısının düşünce imzasının geri gönderilmesini ister, `langchain-openai` 1.7.4 göndermez: her kuralın ikinci isteği HTTP 400, bütün kurallar avukata gitti; 2.5 modelleri 404 döndü); `google_genai` bu yüzden eklendi. Kendi entegrasyonuyla tek denemede inceleme avukat kuyruğuna ulaştı (`awaiting_lawyer`; bir kuralın koşusu ücretsiz katman hız limitiyle kesildi). |
| Model kurulumu | `init_chat_model(model, model_provider=...)` | Sağlayıcı değişimi tek yerde: `src/llm.py`. Sağlayıcı, model, anahtar ve adres ayrı env'lerdir. Model giriş noktalarında başlangıçta bir kez kurulur (aşağıda "Başlangıç ve hata davranışı"). |
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
  TPD** (konsolun Limits sayfası, 2026-11-12; yanıt başlıkları yalnızca 1K RPD ve 8K TPM
  değerlerini verir, günlük token sınırı başlıklarda yoktur). Burada daha önce "250K TPM teyit
  edildi" yazıyordu (2026-11-10); bu anahtar için yanlıştı. Tasarım başta ona göreydi (varsayılan
  `MAX_CONCURRENCY=4`): gerçek çağrıda 5 kuraldan 3'ü `rate_limited` oldu, varsayılan 1 yapıldı.
- **Ölçülen** (Adım 12, örnek sözleşme, tek koşu): sözleşme başına ~25K token ve 15 model çağrısı,
  112 sn. Önce/sonra tablosu ikinci bir geçiş istemez: model sözleşme başına bir kez çalışır, iki
  satır aynı ham bulgulardan kodla hesaplanır (`evals.md` §5).
- **Ölçülen** (Adım 18, 7 sözleşmelik tam eval, iki koşu, 2026-11-12, `MAX_CONCURRENCY=1`):
  206.387 token / 576 sn (taslak 1 / 11) ve 241.576 token / 813 sn (taslak 11 / 11); sözleşme
  başına 23,5K–39,5K, ilk üç sözleşme ≈ 105K. Taslaklarla **koşunun ham token toplamı belgelenen 200K'yı geçti (206K ve 242K); iki koşu yine de
  429 almadan bitti**. Maliyet
  koşu başına $0,035–0,041 (birim fiyatla hesaplanan).
- **Limit gözlemi (2026-11-12):** Gün içinde bu anahtarla sırasıyla tek sözleşmelik deneme
  (≈ 25K token), 1. tam koşu (206.387 token, 576 sn) ve 2. tam koşu (241.576 token, 813 sn) 429
  almadan bitti (ham hız ≈ 18–21K token/dakika; toplam ≈ 473K). Ardından holdout koşusu (2
  sözleşme, `plan.md` "Adım 18b") limite takıldı: 1. sözleşme tamamlandı, 2. sözleşmenin 5
  kuralının 5'i `rate_limited` oldu (≈ 22:04 UTC); koşucu çıkış kodu 2 ile durdu, tablo ve sonuç
  dosyası yazmadı, tamamlanan sözleşme önbellekte kaldı. 22:51 UTC'de yanıt başlıkları
  `x-ratelimit-limit-requests` 1000 (günlük istek) ve `x-ratelimit-limit-tokens` 8000 (dakikalık
  token) verdi; günlük token sınırı başlıklarda yer almıyor. O anda küçük istekler 200 dönüyordu;
  istek sayacı sürekli dolan bir kova gibi davrandı (kalan 919 / 1000, tam dolum ≈ 1 sa 56 dk).
  **Bilinmeyenler:** limite hangi sınırın (dakikalık ya da günlük token) yol açtığı; ham token
  toplamının belgelenen 8K TPM / 200K TPD'yi neden aşabildiği (olası açıklama: önbelleğe alınan
  girdi token'larının limite sayılmaması, doğrulanmadı); günlük sınırın geçerli değeri ve ne zaman
  dolduğu. Konsolun Limits / Usage sayfası bu oturumda kontrol edilmedi. **Sonuç:** ücretsiz
  katmanda tam eval limite takılabilir; aşağıdaki rehber geçerlidir.
- **Maliyet hesabı:** eval'in birim fiyatları `src/config.py`'deki bilinen sağlayıcı tablosunda,
  sağlayıcının varsayılan modelinin yanında durur (`groq`: 1M token başına $0.15 girdi / $0.60
  çıktı; `ollama`: 0). Model `LLM_MODEL` ile ezilirse maliyet "bilinmiyor" yazılır; başka modelin
  fiyatı tahmin edilmez. Varsayılan modeli ve birim fiyatı olmayan sağlayıcılarda (`openai`,
  `anthropic`, `google_genai` ve tabloda olmayan her sağlayıcı) maliyet her zaman "bilinmiyor"dur.
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
- **Anahtar yoksa:** `LLM_PROVIDER=groq` iken ne `GROQ_API_KEY` ne `LLM_API_KEY` verilmişse uygulama
  **başlamaz**; hangi değişkenin eksik olduğunu ve çevrimdışı moda nasıl geçileceğini söyleyen tek
  satırlık hatayla, sıfır olmayan çıkış koduyla durur. `openai`, `anthropic` ve `google_genai`
  için `LLM_API_KEY` zorunludur. Kontrol `src/config.py`'dedir; kütüphanenin hatasına bırakılmaz.
  Aynı kontrol biçimi bozuk `LLM_PROVIDER`, varsayılan modeli olmayan sağlayıcıda boş `LLM_MODEL`,
  geçersiz `LLM_TEMPERATURE` / `LLM_MAX_TOKENS`, `groq` ile birlikte verilen `LLM_TEMPERATURE=none`,
  `ollama` ile birlikte verilen `LLM_BASE_URL`, `google_genai` seçiliyken ortamda açık
  `GOOGLE_GENAI_USE_VERTEXAI` / `GOOGLE_GENAI_USE_ENTERPRISE` ya da boş olmayan
  `GOOGLE_GENAI_CLIENT_MODE` (aşağıda "Ortam değişkenleri") ve
  boş `TENANT_API_KEYS` için de yapılır.
- **Model kurulum kontrolü:** iki giriş noktası (`src/api.py` ve `evals/run.py`'nin `main()`'i)
  sohbet modelini hizmete ya da koşuya başlamadan önce bir kez kurar; `--prepare-only` kurmaz.
  Entegrasyon paketi kurulu değilse (`ImportError`), sağlayıcı adını LangChain tanımıyorsa
  (`ValueError`) ya da entegrasyon kimlik bilgisini kurulumda reddederse hata `ConfigError`'a
  çevrilir ve süreç aynı tek satırlık mesajla durur; hata ilk incelemeye kalmaz. Eksik pakette
  mesaj paketin adını, varsa paket grubunu (`openai` / `anthropic` / `google`) ve `LLM_EXTRAS` build
  argümanını söyler. `langchain-groq` ve `langchain-ollama` çekirdek bağımlılıktır (grupları
  yoktur); onlardan biri eksikse mesaj ortamın kilitten kurulmadığını söyler (`uv sync` ya da
  imajı yeniden kur), `pyproject.toml`'a paket eklemeyi önermez. **Sınır:** tabloda olmayan ve anahtarsız da kurulabilen bir sağlayıcıda
  eksik anahtar ancak ilk çağrıda görülür (`ChatAnthropic` anahtarsız kurulur; `anthropic` tabloda
  olduğu için onda anahtarı config ister); 401 `config` sınıfıdır ve incelemeyi durdurur (aşağıda).
- **Hız limiti (429):** Groq modeli `max_retries=3` ile kurulur (toplam en fazla 4 istek); yeniden
  denemeyi SDK yapar, başka yerde yeniden deneme döngüsü yoktur. Diğer sağlayıcılar da
  `max_retries=3` ile kurulur; kaç istek demek olduğu sağlayıcıya göre değişir (aşağıda "Sürüm
  sabitleri / kısıtlar"). Denemeler tükenirse o kural
  `no_finding` (sebep `rate_limited`) olur ve avukat kuyruğuna düşer; inceleme çökmez. Eval'de tek
  bir `rate_limited` koşuyu geçersiz kılar (`evals.md` §6). HTTP 429 her sağlayıcıda bu sınıftır,
  Ollama dahil (uzak ya da vekil arkasındaki Ollama 429 dönebilir; `agent_design.md` §6).
  - Groq'a özgü: günlük limit dolduğunda `retry-after` saatler olabilir; `groq` SDK'sı 60 sn'den
    uzun beklemeye uymadığı için istek asılı kalmaz, birkaç saniye içinde `rate_limited` olur.
    Anthropic SDK'sı ise `retry-after` ne derse o kadar bekler (aşağıda "Sürüm sabitleri /
    kısıtlar").
  - Groq'a özgü: aynı `max_retries` zaman aşımı ve 5xx için de geçerlidir; bir Groq model çağrısı
    yaklaşık 4 × 60 sn sürebilir (`timeout` deneme başınadır; kesin üst sınır değildir).
- **Kimlik ya da yapılandırma hatası (401 / 403 / 404):** Yeniden denenmez (`groq` SDK'sı 401, 403
  ve 404'ü yeniden denemez; diğer SDK'larda doğrulanmadı) ve `no_finding`'e çevrilmez.
  `review_rule` bu hatayı yeniden fırlatır; inceleme `failed` olur, API 502 döner. Bu sınıf
  (`config`) sağlayıcıdan bağımsız kurulur (kural: `agent_design.md` §6): `langchain_core`'un
  kimlik, izin ve bulunamadı hataları ya da HTTP 401 / 403 / 404. Örnekler: Groq 401
  (`groq.AuthenticationError`, geçersiz anahtar), Groq 403 (`groq.PermissionDeniedError`, örneğin
  model organizasyon ya da proje düzeyinde engelli), Groq 404 (`groq.NotFoundError`, örneğin yanlış
  `LLM_MODEL`), Ollama 404 (`ollama.ResponseError`, `status_code == 404`, model çekilmemiş) ve
  Ollama 401 / 403 (kimlik doğrulamalı uzak Ollama).
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
  - `app` — her modda çalışır.
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
- `app` servisinde `restart` tanımlanmaz. Anahtar ya da sağlayıcı paketi eksikse `app` hatayı
  compose çıktısına yazıp çıkar; döngüye girmez. `python -m evals.run` aynı `config.py` kontrolünden
  ve (`--prepare-only` dışında) aynı model kurulum kontrolünden geçer.
- **İmaj ve paket grupları:** Dockerfile `LLM_EXTRAS` build argümanını alır (varsayılan
  `"openai anthropic google"`); listedeki her ad `--extra` olarak `uv sync --locked --no-dev`'e
  verilir. `--frozen` değil `--locked` kullanılır: `--frozen` bayat kilidi fark etmez. Compose
  `${LLM_EXTRAS-openai anthropic google}` değerini imajı kuran iki servise (`app`, `ollama-pull`)
  aynı verir; ikisi tek imajdır. Boş değer "grup yok" demektir. Grubu olmayan bir sağlayıcı için
  kullanıcı entegrasyon paketini `pyproject.toml`'a ekler, kilidi yeniler ve imajı yeniden kurar.

## Ortam değişkenleri (sadece isimler, değer YAZMA)
Tanımsız, boş ve yalnızca boşluk içeren değer aynıdır ("boş").
- `LLM_PROVIDER` — `init_chat_model`'e `model_provider` olarak verilir; boşsa `groq`. LangChain gibi
  normalleştirilir (`strip`, küçük harf, `-` → `_`; `Groq` → `groq`, `google-genai` →
  `google_genai`) ve ardından `[a-z][a-z0-9_]*` olmalıdır; değilse `LLM_PROVIDER`'ı söyleyen
  başlangıç hatasıdır. Kodda kapalı sağlayıcı listesi yoktur: LangChain'in tanımadığı ad başlangıçta
  `ConfigError` olur (model kurulum kontrolü). Bilinen sağlayıcılar `groq`, `ollama`, `openai`,
  `anthropic`, `google_genai`'dir (aşağıda "Sürüm sabitleri / kısıtlar", "Bilinen sağlayıcı
  tablosu"); bu tablo kapı değildir.
- `LLM_MODEL` — boşsa sağlayıcıya göre: `groq` → `openai/gpt-oss-120b`, `ollama` → `qwen3.5:9b`.
  Varsayılan modeli olmayan sağlayıcıda (`openai`, `anthropic`, `google_genai` ve tabloda olmayan
  her sağlayıcı)
  zorunludur; boşsa `LLM_MODEL`'i söyleyen başlangıç hatasıdır. Sağlayıcı varsayılanları tek yerde,
  `src/config.py`'dedir; `.env.example`'da boş bırakılır ki yalnızca `LLM_PROVIDER` değiştirilince
  yanlış model adı kalmasın.
- `GROQ_API_KEY` — `LLM_PROVIDER=groq` iken anahtar budur; boşsa `LLM_API_KEY` kullanılır, ikisi de
  boşsa başlangıç hatasıdır
- `LLM_API_KEY` — sağlayıcı anahtarı; verilmişse `api_key` olarak geçirilir (boş değer ya da
  kazara `None` geçirilmez: entegrasyon o zaman kendi env değişkenine sessizce düşerdi). `openai`,
  `anthropic` ve `google_genai` için zorunlu (`google_genai`'de ortamdaki `GOOGLE_API_KEY` /
  `GEMINI_API_KEY` yerine geçmez; verilen anahtar onlara üstün gelir); tabloda olmayan sağlayıcıda
  isteğe bağlı; `ollama` anahtar istemez:
  `LLM_API_KEY` verilmişse Ollama'ya geçirilmez ve hata değildir (çevrimdışı moda çevrilen bir
  `.env` onu hâlâ taşıyabilir). `GROQ_API_KEY` gibi loglanmaz ve `repr`'de görünmez.
- `LLM_BASE_URL` — verilmişse `base_url` olarak geçirilir (OpenAI uyumlu servisler `LLM_API_KEY`
  ile birlikte bununla kullanılır). Değerin anlamı entegrasyona göre değişir: `openai`'de `/v1`'i
  içerir (`http://host:port/v1`; istek `…/v1/chat/completions`'a gider), `groq`'ta host köküdür
  (`groq` SDK'sı `/openai/v1`'i kendisi ekler). `LLM_PROVIDER=openai` iken verilmişse ayrıca
  `use_responses_api=False` geçirilir: OpenAI uyumlu sunucular `/chat/completions`'ı uygular;
  `langchain-openai` adında `codex` geçen ya da `gpt-5-pro` ile başlayan modelleri yoksa
  `/responses`'a yönlendirir. `google_genai`'de host köküdür (SDK `v1beta/models/…`'i kendisi
  ekler). **Boşsa** `openai`, `anthropic` ve `google_genai`'ye sağlayıcının genel adresi açıkça
  verilir (`https://api.openai.com/v1`, `https://api.anthropic.com`,
  `https://generativelanguage.googleapis.com/`); bu varsayılanla `use_responses_api=False`
  verilmez. Sebep: adres verilmezse entegrasyon ve SDK onu kendi env değişkenlerinden okur
  (`OPENAI_BASE_URL`, `OPENAI_API_BASE`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_API_URL`;
  `google_genai`'de `GOOGLE_GEMINI_BASE_URL` (`google-genai` SDK'sı) ve `LANGSMITH_GATEWAY`
  (`langchain-core`'un LangSmith ağ geçidi; açıksa istek `…/gemini`'ye gider)); anahtar ve maskeli
  metin `LLM_BASE_URL`'in hiç söylemediği bir adrese gidebilirdi. Adrese kimlik bilgisi yazılmaz
  (anahtar `LLM_API_KEY`'e yazılır); değer yine de `Settings`'in `repr`'inde görünmez. `ollama` ile
  birlikte verilemez (başlangıç hatası, mesaj `OLLAMA_BASE_URL`'i söyler): compose
  `OLLAMA_BASE_URL`'i her zaman doldurduğu için değer sessizce yok sayılırdı. **Sınırlar:** Groq'a
  bu kural uygulanmaz; `LLM_BASE_URL` boşken adres `GROQ_API_BASE`'den (`langchain-groq`) ya da
  `GROQ_BASE_URL`'den (`groq` SDK'sı) okunabilir; bu, Groq'a giden istek değişmesin diye
  öncekiyle aynı bırakıldı. `openai`'de genel adres açıkça verildiği için `langchain-openai`
  akışlı yanıtta kullanım sayımını (`stream_usage`) varsayılan olarak açmaz; bu yalnızca akışlı
  isteği etkiler. Entegrasyonlar adres dışındaki kendi
  ikincil değişkenlerini okuyabilir (ör. `langchain-openai`'de organizasyon ve vekil:
  `OPENAI_ORG_ID`, `OPENAI_PROXY`). Tablonun dışındaki sağlayıcılar kendi değişkenlerini okur
  (aşağıda).
- **`google_genai` ve Vertex değişkenleri** (2026-11-13, sahte anahtar ve sahte taşıyıcıyla
  betikle bakıldı): `GOOGLE_GENAI_USE_VERTEXAI` (`langchain-google-genai` ve SDK) ya da
  `GOOGLE_GENAI_USE_ENTERPRISE` (yalnızca SDK) açıksa istek Gemini Developer API yerine Vertex'e
  döner (`base_url` verilmezse SDK `aiplatform.googleapis.com`'a gider; proje genel adresi her
  zaman verdiği için istek aynı host'ta Vertex yoluna gider). Bunu kapatan bir parametre yoktur:
  entegrasyonun `vertexai=False`'ı SDK'ya geçmez, SDK ortamı yine okur. Bu yüzden `google_genai`
  seçiliyken bu iki değişkenden biri boş, `false`, `0` ya da `no` dışında bir değerle verilmişse
  uygulama başlamaz (`ConfigError`, mesaj değişkenin adını söyler, değeri tekrarlamaz); bu sistem
  Vertex'i desteklemez. `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` tek başına isteği
  değiştirmez. Aynı yolla `GOOGLE_GENAI_CLIENT_MODE` boş olmayan herhangi bir değerle (`api`
  dahil; normal koşu için değer gerekmez) verilmişse uygulama başlamaz: `record` / `auto`'da
  SDK'nın kayıt istemcisi isteği anahtar başlığıyla (`x-goog-api-key`) birlikte stdout'a basar,
  `replay`'de yanıtlar modelden değil bir dosyadan gelir. `GOOGLE_GENAI_REPLAYS_DIRECTORY` ve
  `GOOGLE_GENAI_REPLAY_ID` `GOOGLE_GENAI_CLIENT_MODE` olmadan etkisizdir (betikle bakıldı:
  istek, adres, anahtar ve stdout aynı, dosya yazılmaz), reddedilmez. **Sınır:**
  `GOOGLE_GENAI_API_VERSION` istek yolundaki API sürümünü değiştirir (ör. `v1beta` → `v1`); host
  ve anahtar aynı kalır, önizleme modelleri `v1`'de bulunamayabilir (404 → `config`).
  `GOOGLE_GENAI_HTTP_CLIENT` SDK'nın HTTP kütüphanesini değiştirir (ör. `httpx2`); adres ve
  anahtar aynı kalır.
- `LLM_TEMPERATURE` — boşsa 0. `none` ise `temperature=None` **açıkça** geçirilir ve istekte
  `temperature` alanı olmaz (bazı modeller alanı reddeder); argümanı atlamak yetmez,
  `ChatGoogleGenerativeAI` atlanan değer yerine kendi varsayılanını (0,7) gönderir. Başka değer
  negatif olmayan bir sayı olmalıdır (`-0` 0 sayılır); değilse başlangıç hatasıdır. Her sağlayıcıya
  uygulanır; tek istisna `groq` ile `none`'dır: başlangıç hatasıdır (mesaj `LLM_TEMPERATURE`'ı
  söyler), çünkü `ChatGroq`'un "sıcaklık yok" değeri yoktur: `None` kurulumda doğrulamadan geçmez,
  argümanı atlamak ise `ChatGroq`'un kendi varsayılanını (0,7) gönderir. `google_genai` ile
  `gemini-3.5-flash-lite`'ta entegrasyon her çağrıda uyarır ("Model 'gemini-3.5-flash-lite' uses
  fixed sampling defaults; the sampling parameter(s) temperature will be ignored."): bu modelde
  `LLM_TEMPERATURE` etkisizdir, `temperature` 0 uygulanmaz ve koşuların tekrarlanabilir olması
  beklenmez (2026-11-13 elle denemesinde görüldü). `anthropic` ile `claude-sonnet-5-5`'te
  varsayılan 0 çalışmaz, `none` ile çalıştı (başka değer denenmedi): 0 ile her model
  çağrısı istek gönderilmeden yerelde `ValueError` verir ("temperature is not supported for claude-sonnet-5-5 at non-default values",
  `langchain-anthropic`); her kural `not_found` (`no_finding`) olur ve avukata gider, audit'te hata
  tipi `ValueError`, durum kodu yoktur (2026-11-13, sözleşme 4: 0 sn, 0 token). Başlangıçtaki
  `check_chat_model` bunu yakalamaz: hata kurulumda değil çağrıda çıkar. Eval bu modelde
  `LLM_TEMPERATURE=none` ile koşuldu.
- `LLM_MAX_TOKENS` — çağrı başına çıktı sınırı; pozitif tam sayı, boşsa 2048 (Groq'un bugünkü
  değeri). `groq`'a ve Ollama dışındaki her sağlayıcıya verilir; `ollama`'ya verilmez (`ChatOllama`
  yok sayar). Sebep: düşünen modellerde düşünme token'ları bu sınırın içindedir; sınır dolarsa yanıt
  boş gelir ve bu sistemde sağlayıcı hatası değil `no_finding` (`no_submit`) bulgusu olur.
- `LLM_API_KEY` verilmemişse bilinen tablonun dışındaki sağlayıcının (ve birden çok kimlik bilgisi
  isteyen her sağlayıcının; ör. Bedrock, Azure, Vertex) entegrasyonu kendi standart env
  değişkenlerini kendisi okur. Env'i `src/config.py`'den başka bir şeyin okuduğu yer budur ve
  yukarıda `LLM_BASE_URL`'deki sınırlardır (Groq'un adres değişkenleri, entegrasyonların ikincil
  değişkenleri); "env'e yalnızca `src/config.py` dokunur" kuralının yazılı istisnasıdır
  (`code_standards.md`). Bu değişkenler burada listelenmez.
- `LLM_EXTRAS` — çalışma anı değişkeni değil, Docker **build argümanıdır**: imaja kurulacak paket
  grupları, boşlukla ayrılmış (varsayılan `openai anthropic google`; boş değer grup yok demektir).
  Compose
  onu `.env`'den ya da kabuktan okur (yukarıda "Deploy").
- `COMPOSE_PROFILES` — çevrimdışı modda `offline` (`ollama` ve `ollama-pull` servislerini açar);
  varsayılan modda boş
- `OLLAMA_BASE_URL` — yalnızca Ollama; boşsa Compose dışında `http://localhost:11434`, Compose
  içinde `http://ollama:11434` (`docker-compose.yml` ayarlar). Host'taki Ollama için:
  `http://host.docker.internal:11434`
- `LLM_NUM_CTX` — yalnızca Ollama: bağlam uzunluğu (varsayılan 4096 çok küçük; 16384 kullanılır)
- `MAX_CONCURRENCY` — aynı anda çalışan kural agent'ı sayısı. Varsayılan her sağlayıcıda 1.
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
- İsteğe bağlı paket grupları (`[project.optional-dependencies]`): `openai` =
  `langchain-openai>=1.7`, `anthropic` = `langchain-anthropic>=1.7`, `google` =
  `langchain-google-genai>=4.5` (kilitte 4.5.0, `google-genai` 3.3.0). `langchain-openai` ve
  `langchain-google-genai` ayrıca `dev` grubundadır; `openai` sahte taşıyıcı testi ve
  `google_genai`'nin gerçek hata sınıflarıyla ve `client_args` taşıyıcısıyla koşan testleri yerelde
  atlanmadan koşar.
- **`google` grubunun bedeli (bilerek kabul edildi, Adım 18d; ADR-012):** `langchain-google-genai`
  4.x kilide girince, grup seçilmese de herkesin kurduğu iki paket değişir: `langchain-core` 1.7.7 →
  1.8.0 ve `tenacity` 9.2.1 → 9.1.4 (ölçülen Groq yolu dahil; kilitte `langchain-core` 1.8.0,
  `tenacity` 9.1.4). Sabitleme grubuyla iki çekirdek tutmak reddedildi. Sebep: Gemini Google'ın
  OpenAI uyumlu adresiyle 2026-11-13 denemesinde incelemeyi tamamlamadı (Gemini 3.x: düşünce imzası
  `langchain-openai` 1.7.4 ile geri gönderilmiyor, her kuralın ikinci isteği HTTP 400, bütün
  kurallar avukata gitti; 2.5 modelleri 404); yerel entegrasyon imzayı taşır (sahte taşıyıcıyla
  test edilir, `evals.md` §2; gerçek Gemini API'siyle tek denemede tool döngüsü tamamlandı). Gemini tek
  sözleşmede denendi (2026-11-13), ölçülmedi (yukarıda "Diğer sağlayıcılar", `plan.md` Adım 18d
  "Elle deneme"). İlk iki Groq koşusu `langchain-core` 1.7.7 ile, üçüncüsü (2026-11-13) 1.8.0
  ile yapıldı; sert eşikler yine tuttu (tek koşu; farklar sürüme bağlanmaz, `evals.md` §4).
- pydantic v2 zorunlu
- Varsayılan `temperature=0` her sağlayıcıda (`LLM_TEMPERATURE` ile değişir); model tool calling
  desteklemeli. `ChatGroq` `temperature=0`'ı API isteğine `1e-8` olarak yazar; yani "0", istekteki
  değerin en fazla `1e-8` olması demektir.
- **Bilinen sağlayıcı tablosu** (`src/config.py`; kapı değildir, yalnızca varsayılanları ve o
  sağlayıcıya özel parametreleri tutar). Sağlayıcıya özel parametreler `src/llm.py`'de yalnızca o
  sağlayıcı seçiliyken verilir:
  - Groq: varsayılan model, birim fiyat; `max_retries=3`, `max_tokens` (`LLM_MAX_TOKENS`, varsayılan
    2048), `reasoning_effort="low"` (yalnızca Groq'a), zaman aşımı 60 sn. `reasoning_format`
    verilmez. `LLM_TEMPERATURE=none` kabul edilmez (başlangıç hatası; `ChatGroq` sıcaklıksız
    istek göndermez).
  - Ollama: varsayılan model; `OLLAMA_BASE_URL`, `num_ctx`, `reasoning=False`, sabit `seed`, zaman
    aşımı 120 sn (`client_kwargs` ile). Anahtar yok; `max_tokens` verilmez. `ChatOllama`'da
    `max_retries` alanı yoktur; yerelde hız limiti de yoktur.
  - `openai`, `anthropic`, `google_genai`: varsayılan model yok (`LLM_MODEL` zorunlu),
    `LLM_API_KEY` zorunlu, birim fiyat yok (maliyet "bilinmiyor"), eşzamanlılık varsayılanı 1.
    Parametreleri ortak parametrelerdir (aşağıda); `LLM_BASE_URL` boşsa genel adresleri açıkça
    verilir (`https://api.openai.com/v1`, `https://api.anthropic.com`,
    `https://generativelanguage.googleapis.com/`; yukarıda "Ortam değişkenleri"). Eksik pakette
    mesaj `openai`, `anthropic` ve `google` grubunu söyler.
- **Tabloda olmayan sağlayıcılar ve `openai` / `anthropic` / `google_genai`** tam olarak şu ortak
  parametreleri alır: `max_tokens` (`LLM_MAX_TOKENS`), `timeout` (60 sn), `max_retries` (3),
  `temperature` (`LLM_TEMPERATURE`); verilmişse `api_key` (`LLM_API_KEY`) ve `base_url`
  (`LLM_BASE_URL`; `openai` ile birlikte `use_responses_api=False`). `LLM_BASE_URL` boşken
  `base_url` yalnızca `openai`, `anthropic` ve `google_genai`'ye, genel adresleriyle verilir;
  tabloda olmayan sağlayıcıya verilmez. Sağlayıcıya
  özel başka parametre verilmez. Tabloda
  olmayan sağlayıcıda `LLM_MODEL` zorunlu, `LLM_API_KEY` isteğe bağlıdır; eşzamanlılık varsayılanı
  1, maliyet "bilinmiyor"dur.
  - **OpenAI uyumlu servis:** `base_url` isteğin gittiği adrestir (`…/chat/completions`); çıktı
    sınırı istekte `max_completion_tokens` alanıyla gider. Gerçek bir OpenAI uyumlu sunucunun bu
    alanı kabul ettiği doğrulanmadı.
- **`max_retries` her sağlayıcıda aynı anlama gelmez:** `groq`, `openai` ve `anthropic`'te 3 → en
  fazla 4 istek; `google_genai`'de 3 → 3 istek. `timeout` deneme başınadır (httpx aşaması başına),
  toplam süre sınırı değildir; Anthropic SDK'sı `retry-after` başlığı ne kadar derse o kadar bekler.
  Bu farklar kodla eşitlenmez.
- Model kurucusunun isteğe bağlı `http_client` parametresi (testte sahte HTTP taşıyıcısı vermek
  için) yalnızca `groq` ve `openai`'de, senkron yolda kabul edilir. Diğer her sağlayıcıda `None`
  olmayan değer `build_chat_model`'de baştan `ValueError` fırlatır: Anthropic'te çağrı anında
  `TypeError` verirdi; Google ve Ollama onu sessizce yok sayar ve istek gerçek ağa çıkardı. Graph
  senkron olduğu için senkron yolla sınırlı olması sorun değildir. Verilse de Groq'un istek başına
  60 sn zaman aşımı geçerlidir.
