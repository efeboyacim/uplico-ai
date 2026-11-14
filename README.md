# Sözleşme İnceleme Agent'ı

Tedarikçi sözleşmelerini şirketin kendi kural setine göre inceleyen bir agent sistemi. Her kural
için sonuç **uygun / uygun değil / bulunamadı** olur ve sözleşmeden birebir alıntıyla gösterilir;
uygun olmayan maddeler için değişiklik taslağı yazılır, emin olunamayan bulgular avukata bırakılır.
İki kurgusal şirketin (`acme`, `globex`) verisi ayrı tutulur. Case metni: `docs/CASE.md`.

**Ana ilke: LLM önerir, kod karar verir, yalnızca avukat onaylar.** Model bulguyu önerir; alıntının
maddede geçip geçmediğini, sayıyı ve taslağın kurala uyup uymadığını kod (`src/verify.py`) kontrol
eder ve avukat kuyruğuna neyin gideceğine kod karar verir. Agent'ın onay veren bir tool'u yoktur;
sözleşmeyi yalnızca `lawyer` rolündeki anahtarla, API üzerinden bir insan onaylar.

Python, LangChain `create_agent`, LangGraph ve LangSmith (`evaluate()`) ile kuruldu. Varsayılan
model sağlayıcısı Groq (`openai/gpt-oss-120b`), çevrimdışı mod Ollama'dır (`qwen3.5:9b`);
LangChain'in tanıdığı başka bir sağlayıcı da env ile seçilebilir (entegrasyon paketi imajda
kuruluysa). Varsayılan Groq `openai/gpt-oss-120b` üç tam eval koşusuyla ölçüldü; Anthropic
`claude-sonnet-5-5` iki tam koşuyla (Adım 19c'nin düzeltmelerinden önce ve sonra) ölçüldü; Gemini (`google_genai`) tek sözleşmede denendi,
ölçülmedi; OpenAI denenmedi. Kararların gerekçesi: `docs/decisions.md`
(karar dokümanı) ve `docs/architecture.md` §7 (ADR'ler).

**Hızlı yol:** [Kurulum](#kurulum-ve-çalıştırma) → `docker compose up` → [Kullanım](#adım-adım-ilk-inceleme-arayüz-karar)'daki
5 adım (örnek sözleşmeyi incelet, bulguları tarayıcıda gör, avukat olarak karar ver, raporu oku).

## İçindekiler
1. [Kurulum ve çalıştırma](#kurulum-ve-çalıştırma)
2. [Kullanım](#kullanım) ([adım adım: ilk inceleme, arayüz, karar](#adım-adım-ilk-inceleme-arayüz-karar))
3. [Eval nasıl koşulur](#eval-nasıl-koşulur)
4. [Eval sonuçları](#eval-sonuçları)
5. [Görülmemiş iki sözleşme (holdout)](#görülmemiş-iki-sözleşme-holdout)
6. [Groq hız limiti](#groq-hız-limiti)
7. [Veri nereye gidiyor](#veri-nereye-gidiyor)
8. [Bilinen sınırlar](#bilinen-sınırlar)
9. [Denenmeyenler](#denenmeyenler)
10. [Daha fazla vaktim olsaydı](#daha-fazla-vaktim-olsaydı)
11. [Depo haritası](#depo-haritası)

## Kurulum ve çalıştırma

Gerekenler: Docker ve Docker Compose. Varsayılan mod için bir Groq API anahtarı; GPU gerekmez.
[Kullanım](#kullanım)'daki örnek komutlar için ayrıca `curl` (Windows 10 ve sonrasında
`curl.exe` olarak gelir) ve Python 3 (yalnızca JSON dosyası hazırlamak ve okumak için;
`python --version`, macOS / Linux'ta `python3 --version`, `Python 3.…` yazmalı; Windows'ta
"Python was not found" yazıyorsa Python'u python.org'dan kur).

```bash
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
# .env içinde GROQ_API_KEY= satırını GROQ_API_KEY=gsk_... olarak doldur (tırnaksız, boşluksuz).
docker compose up
```

- **Varsayılan modda `.env`'de doldurulması zorunlu tek değer `GROQ_API_KEY`'dir** (ya da
  `LLM_API_KEY`); diğer satırlar olduğu gibi kalabilir.
- **`TENANT_API_KEYS`** anahtar → tenant, rol, görünen ad eşlemesidir. `.env.example`'daki dört
  `change-me-…` anahtarı uygulama tarafından olduğu gibi kabul edilir: yalnızca kendi makinende
  denemek için değiştirmeden kullanabilirsin (aşağıdaki örnekler bunları kullanır). Bu değerler
  herkesçe bilinir; başka her kullanımda kendi değerlerinle değiştir.
- **Ayakta mı?** İlk seferde imaj kurulur; hazır olunca `app` logunun son satırı
  `Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)` olur (bu adres kapsayıcının
  içidir). Tarayıcıda `http://127.0.0.1:8000` "Sözleşme inceleme" sayfasını açar; port yalnızca
  `127.0.0.1`'e yayınlanır. Bu noktada sayfada yalnızca anahtar ve kimlik alanları vardır; içerik
  ilk incelemeden sonra gelir: [Kullanım](#adım-adım-ilk-inceleme-arayüz-karar)'a geç (aradaki
  bölümler varsayılan modda atlanabilir).
- Varsayılan modda `GROQ_API_KEY` ve `LLM_API_KEY` ikisi de boşsa `app` tek satırlık bir hatayla
  (`Yapılandırma hatası: …`) çıkar ve yeniden başlamaz.
- **Durdurmak:** `docker compose up`'ın çalıştığı terminalde Ctrl+C, ya da başka bir terminalde
  `docker compose down`.
- **Veri nerede:** incelemeler, audit log ve checkpoint'ler `data/tenants/<tenant>/` altındaki
  SQLite dosyalarındadır (`app.db`, `checkpoints.db`; klasör kapsayıcıya bağlanır, durdurmak
  silmez, git'e girmez).

### Varsayılan mod (Groq)
Yalnızca `app` servisi başlar; Ollama kapsayıcısı çalışmaz, model inmez. Sözleşmenin **maskeli**
metni Groq'a gider (aşağıda [Veri nereye gidiyor](#veri-nereye-gidiyor)).

### Çevrimdışı mod (Ollama)
`.env`'de **iki değişken** değişir; komut yine `docker compose up`:

```bash
LLM_PROVIDER=ollama
COMPOSE_PROFILES=offline
```

Bu profille `ollama` ve `ollama-pull` servisleri de başlar; `ollama-pull` sohbet modelini indirir,
`app` indirme bitene kadar bekler. İndirme bittikten sonra internet gerekmez. `LLM_MODEL` boşsa
model `qwen3.5:9b`'dir (yedek: `qwen3.5:4b`). GPU'lu bir Windows makinede host'taki Ollama da
kullanılabilir: `OLLAMA_BASE_URL=http://host.docker.internal:11434` (`docs/tech_stack.md` "Deploy").

### Başka bir sağlayıcı
Sağlayıcı, model, anahtar ve adres yalnızca `.env` ile seçilir; kod değişmez. **Varsayılan Groq
`openai/gpt-oss-120b` üç tam koşuyla, Anthropic `claude-sonnet-5-5` iki tam koşuyla ölçüldü**
([Eval sonuçları](#eval-sonuçları)); başka her sağlayıcı ve model yapısı gereği çalışması
beklenen ama ölçülmemiş bir yapılandırmadır; çalışacağı garanti değildir (Gemini 3.x OpenAI
uyumlu adresle çalışmadı; `claude-sonnet-5-5` varsayılan `LLM_TEMPERATURE` ile çalışmaz, aşağıda).
Gemini kendi entegrasyonuyla (`google_genai`) tek sözleşmede, tek koşuyla denendi; ölçülmedi
([Üçüncü bir sağlayıcıyla deneme](#üçüncü-bir-sağlayıcıyla-deneme-gemini)).

| Değişken | Anlamı |
|---|---|
| `LLM_PROVIDER` | LangChain `init_chat_model`'in tanıdığı sağlayıcı adı (ör. `openai`, `anthropic`, `google_genai`); boşsa `groq` |
| `LLM_MODEL` | Model adı; boşsa `groq` ve `ollama`'nın varsayılanı, varsayılanı olmayan sağlayıcıda zorunlu |
| `LLM_API_KEY` | Sağlayıcı anahtarı (`openai`, `anthropic` ve `google_genai` için zorunlu); `GROQ_API_KEY` eskisi gibi çalışır |
| `LLM_BASE_URL` | Verilirse modelin adresi (OpenAI uyumlu serviste `openai`'de `/v1` gibi yol dahil); `ollama` ile verilirse başlangıç hatasıdır, Ollama adresi `OLLAMA_BASE_URL`'dir |
| `LLM_TEMPERATURE` | Boşsa 0; `none` yazılırsa istekte alan gönderilmez (`groq` ile kullanılamaz; Anthropic `claude-sonnet-5-5`'te 0 çalışmaz, `none` ile çalıştı). `gemini-3.5-flash-lite` ile entegrasyon değeri yok sayar |
| `LLM_MAX_TOKENS` | Çağrı başına çıktı sınırı; boşsa 2048 |

Yeni dört değişken (`LLM_API_KEY`, `LLM_BASE_URL`, `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`)
verilmediğinde varsayılan ve çevrimdışı mod önceki gibi çalışır; Groq'a giden istek bu adımdan
hemen önceki kodunkiyle (`2dd77b6`) bayt bayt aynıdır (yan yana karşılaştırıldı: 64 ortamda model
alanları, 36 senaryoda giden istek). 2. Groq koşusunun kodunun (`4ccc03d`) isteğiyle aynı olduğu söylenemez:
ondan sonraki düzeltmeler bazı sözleşmelerde maskeli metni değiştirir ([Eval
sonuçları](#eval-sonuçları)). Adım 18d'de `google` grubu kilide girince herkesin kurduğu
`langchain-core` 1.7.7 → 1.8.0 oldu (`tenacity` 9.2.1 → 9.1.4); Groq ve Ollama yolu iki sürümde
sahte taşıyıcılarla karşılaştırıldı (istekler, ayrıştırılan yanıtlar, hata sınıfları, gerçek graph
üzerinde bir inceleme): fark yok. Ardından modelli eval bugünkü kodla ve `langchain-core` 1.8.0
ile Groq'ta bir kez daha koşuldu (2026-11-13, [Eval sonuçları](#eval-sonuçları)). Ayrıntı:
`docs/tech_stack.md` "Ortam değişkenleri", `docs/architecture.md` ADR-012.

Anthropic (`langchain-anthropic` varsayılan imajdadır; ölçülen ayar budur):

```bash
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-5-5
LLM_API_KEY=<Anthropic API anahtarı>
LLM_TEMPERATURE=none
```

Bu modelde varsayılan 0 çalışmaz, `LLM_TEMPERATURE=none` ile çalıştı (başka değer denenmedi): 0 ile `langchain-anthropic` her
çağrıyı istek göndermeden reddeder ve bütün kurallar "bulunamadı" olarak avukata gider; uygulama
yine de başlar ([Bilinen sınırlar](#bilinen-sınırlar)).

OpenAI uyumlu bir servis varsayılan imajda ek paket gerektirmez (`langchain-openai` imajdadır):

```bash
LLM_PROVIDER=openai
LLM_BASE_URL=<servisin OpenAI uyumlu adresi>
LLM_API_KEY=<servisin anahtarı>
LLM_MODEL=<model adı>
```

- **Paketler.** İmaj varsayılan olarak `langchain-openai`, `langchain-anthropic` ve
  `langchain-google-genai` ile kurulur (`LLM_EXTRAS` build argümanı, varsayılan
  `"openai anthropic google"`; boş değer hiçbirini kurmaz).
  Değer `.env`'de ya da kabukta verilir; değiştirince imajı yeniden kurmak gerekir:
  `docker compose build`.
- **Grubu olmayan sağlayıcı:** LangChain entegrasyon paketini `pyproject.toml`'a ekle, kilidi
  yenile (`uv lock`) ve imajı yeniden kur.
- **Başlangıç hatası.** Anahtar, model ya da entegrasyon paketi eksikse `app` başlarken eksik
  değişkeni ya da paketi söyleyen tek satırlık bir hatayla durur. İstisna: bilinen sağlayıcı
  tablosunda (`groq`, `ollama`, `openai`, `anthropic`, `google_genai`) olmayan ve anahtarsız kurulabilen bir
  sağlayıcıda eksik anahtar başlangıçta değil, ancak ilk çağrıda görülür (401: inceleme `failed`,
  API 502).

### İlk çalıştırmada inen toplam
Değerler Adım 12'de (2026-11-12) ölçüldü; varsayılan imajın disk boyutu Adım 18d'de yeniden
ölçüldü. Ölçülmeyen ayrıca belirtildi (`docs/spec.md` §10 "Adım 12 gözlemleri").

| Mod | Ne iner / kurulur | Boyut |
|---|---|---|
| Varsayılan (Groq) | Uygulama imajı (temel imaj + derleme sırasında inen paketler, `langchain-openai`, `langchain-anthropic` ve `langchain-google-genai` dahil) | Diskte 829 MB (ölçüldü; Adım 18c'de iki grupla 743 MB; `LLM_EXTRAS` boşken 611 MB eski kilitte ölçüldü, yeniden ölçülmedi). Sıkıştırılmış indirme yeniden ölçülmedi (Adım 12'deki 611 MB'lık imajda 140 MB'tı) |
| Çevrimdışı (Ollama) | Yukarıdaki imaj + `ollama/ollama:0.40.16` | 3,40 GB indirme, diskte 8,52 GB (ölçüldü; 611 MB'lık imajla) |
| | + sohbet modeli `qwen3.5:4b` (yedek) | 3,3 GB (ölçüldü) |
| | + sohbet modeli `qwen3.5:9b` (varsayılan) | ~7 GB (**tahmin, ölçülmedi**) |

Çevrimdışı modda ilk `docker compose up` `qwen3.5:4b` ile 7 dk 17 sn sürdü. Varsayılan
`qwen3.5:9b` hiç denenmedi (geliştirme makinesinin ekran kartı 4 GB).

### Geliştirme komutları
Gereken: [uv](https://docs.astral.sh/uv/) (Python 3.12'yi ve bağımlılıkları ilk `uv run`
kendisi kurar). Docker gerekmez.

```bash
uv run pytest                                            # test (gerçek LLM ve ağ yok; token harcamaz)
uv run ruff check . && uv run ruff format --check .      # lint + format
uv run mypy src evals                                    # type-check
```

Test takımı geliştirme makinesinde ≈ 3,5 dk sürdü (1858 test, 2026-11-13).

## Kullanım

### Adım adım: ilk inceleme, arayüz, karar

Arayüz (`http://127.0.0.1:8000`) yalnızca okur: inceleme listesi yoktur, inceleme başlatma ve
karar sayfada yoktur. **İnceleme başlatılana kadar sayfada gösterecek bir şey yoktur** (yalnızca
anahtar ve kimlik alanları ile kısa bir açıklama); inceleme 2. adımdaki komutla
başlar, sayfa onu anahtar ve inceleme kimliğiyle gösterir. Uygulama bir terminalde çalışırken
aşağıdakileri **ikinci bir terminalde, deponun kök klasöründe** sırayla çalıştır. Her blok iki
biçimdedir: bash (Linux / macOS / Git Bash) ve Windows PowerShell 5.1. Python'ın adı macOS /
Linux'ta genelde `python3`, Windows'ta (Git Bash dahil) `python`'dır: **Git Bash'te bash
bloklarındaki `python3`'ü `python` yap.**

Örnek anahtarlar (`.env.example`): `change-me-acme-lawyer`, `change-me-acme-viewer`,
`change-me-globex-lawyer`, `change-me-globex-viewer`. Tenant'ı anahtar seçer: globex'in
kurallarıyla incelemek için 1. adımda `change-me-globex-lawyer` kullan. Başka bir sözleşme için 2.
adımdaki `tests/fixtures/sample_contract.md` yolunu değiştir: metin UTF-8 düz metin ya da Markdown
olmalı, maddeler satır başındaki `Madde N` başlıklarıyla ya da (metinde `Madde N` yoksa)
numaralı Markdown başlıklarıyla (`## 4. Fesih`) ayrılır; başka başlıklı metin paragraflara bölünür
(örnekler: `evals/dataset/contracts/`). Kurallar `data/tenants/<tenant>/rules.yaml`'dadır; sabit
eşikli kuralın `check.unit`'i `gün`, `hafta`, `ay`, `yıl`, `saat`, `%` ya da `TL`'dir (ör.
`check: {op: "<=", value: 48, unit: saat}`; saat değeri yalnızca saat birimli kuralda okunur).

**1. Avukat anahtarını değişkene koy.** Değer `.env`'deki `TENANT_API_KEYS` içinde
`"tenant_id": "acme", "role": "lawyer"` kaydının anahtarıdır; `.env.example`'dan değiştirmeden
kopyaladıysan `change-me-acme-lawyer`'dır.

```bash
LAWYER_KEY=change-me-acme-lawyer
```
```powershell
$LAWYER_KEY = "change-me-acme-lawyer"
```

Ne görmelisin: çıktı yok. Değişkenler yalnızca bu terminal oturumunda yaşar: yeni bir terminalde
1. adımı ve 2. adımın `REVIEW_ID` satırını yeniden çalıştır (`review.json`'dan okur; inceleme
yeniden başlamaz).

**2. Örnek sözleşmenin incelemesini başlat ve kimliğini al.** İstek inceleme bitene kadar bekler;
model çağrılır (varsayılan modda sözleşmenin maskeli metni Groq'a gider). Örnek sözleşme Groq'ta
`MAX_CONCURRENCY=1` ile 91 sn ve 112 sn sürdü.

```bash
rm -f review.json
python3 -c "import json; json.dump({'text': open('tests/fixtures/sample_contract.md', encoding='utf-8').read()}, open('body.json', 'w'))"
curl -s -X POST http://127.0.0.1:8000/reviews -H "X-API-Key: $LAWYER_KEY" -H "Content-Type: application/json" --data-binary "@body.json" -o review.json -w "%{http_code}\n"
REVIEW_ID=$(python3 -c "import json; print(json.load(open('review.json', encoding='utf-8'))['review_id'])")
echo "$REVIEW_ID"
```
```powershell
Remove-Item review.json -ErrorAction SilentlyContinue
python -c "import json; json.dump({'text': open('tests/fixtures/sample_contract.md', encoding='utf-8').read()}, open('body.json', 'w'))"
curl.exe -s -X POST http://127.0.0.1:8000/reviews -H "X-API-Key: $LAWYER_KEY" -H "Content-Type: application/json" --data-binary "@body.json" -o review.json -w "%{http_code}\n"
$REVIEW_ID = python -c "import json; print(json.load(open('review.json', encoding='utf-8'))['review_id'])"
$REVIEW_ID
```

Ne görmelisin: bekleme, sonra `200` ve bir inceleme kimliği (UUID). `200` değilse kimlik satırı
hata verir ve kimlik boş kalır: `000` uygulama çalışmıyor (yanıt yok, `review.json` yazılmaz);
`401` anahtar `.env`'dekiyle aynı değil; `502` model sağlayıcısının anahtarı ya da ayarı hatalı;
`500` beklenmeyen hata (ayrıntı `app` logunda). `401` / `502` / `500`'de yanıt `review.json`'dadır
ve kimlik satırı `KeyError: 'review_id'` verir. Groq hız limiti doluysa
yine `200` gelir ama kartlarda "Sonuç üretilemedi" / "Hız sınırı" görünür: bir süre bekleyip 2.
adımı yeniden çalıştır. Gövde UTF-8 olarak okunur ve
ASCII kaçışlı JSON yazılır; PowerShell'de `curl` takma adı yerine `curl.exe` dosyayı olduğu gibi
gönderir. `review.json` avukat görünümüdür: içinde maskesiz sözleşme metni vardır.

**3. Arayüzde gör.** Tarayıcıda `http://127.0.0.1:8000` aç; "API anahtarı" alanına 1. adımdaki
değeri, "İnceleme kimliği" alanına 2. adımın yazdığı kimliği yapıştır, **Getir**'e bas.

Ne görmelisin: üstte "Durum: Avukat kararı bekliyor" ve "Görünüm: avukat (asıl metin,
maskesiz)"; solda sözleşme metni, maddelerde bulgu etiketleri, renkli alıntılar ve turuncu
dalgalı çizgili enjeksiyon işaretleri; sağda "Bulgular" paneli ("5 bulgu, 5 incelenmeli, …
enjeksiyon işareti"; örnek sözleşmede enjeksiyon kalıpları olduğu için bütün bulgular avukat
kuyruğundadır) ve her bulgunun kartı (alıntı, gerekçe, inceleme sebepleri, varsa değişiklik
taslağı). Anahtar yerine `change-me-acme-viewer` yazarsan "Görünüm: izleyici (maskeli madde
metinleri)" olur: kişi adları `[KISI_1]` gibi yer tutucularla görünür. Başka tenant'ın anahtarı
(`change-me-globex-lawyer`) "404: İnceleme bulunamadı.", tanınmayan anahtar "401: …" yazar.
Renkli alıntıya tıklayınca sağdaki kartı, karta tıklayınca metindeki alıntıyı gösterir;
"Yalnızca incelenmesi gerekenler" kutusu listeyi süzer. "Bulunamadı" sonucunun maddesi olmadığı
için metinde işareti yoktur, yalnızca kartı vardır.

**4. Avukat kararını ver.** Karar iki adımdır: önce gövde (`decision.json`) kurulur, sonra
gönderilir. **Karar bir kez verilir;** değiştirmek istiyorsan 4a ile 4b arasında değiştir.

**4a. Karar gövdesini kur.** Aşağıdaki komut `review.json`'dan geçerli bir gövde kurar: kuyruktaki
her bulguyu ve kuyrukta olmasa da taslağı olan her bulguyu kabul eder (`action: accept`), taslağı
olanların taslağını da kabul eder (`proposal_action: accept`), son karar `request_changes`'tir.

```bash
python3 -c "import json; r=json.load(open('review.json', encoding='utf-8')); fs=[dict(rule_id=f['rule_id'], action='accept', **({'proposal_action': 'accept'} if f['proposal'] else {})) for f in r['findings'] if f['needs_review'] or f['proposal']]; json.dump({'findings': fs, 'contract_decision': 'request_changes'}, open('decision.json', 'w'), indent=1); print(len(fs), 'bulgu kabul edildi')"
```
```powershell
python -c "import json; r=json.load(open('review.json', encoding='utf-8')); fs=[dict(rule_id=f['rule_id'], action='accept', **({'proposal_action': 'accept'} if f['proposal'] else {})) for f in r['findings'] if f['needs_review'] or f['proposal']]; json.dump({'findings': fs, 'contract_decision': 'request_changes'}, open('decision.json', 'w'), indent=1); print(len(fs), 'bulgu kabul edildi')"
```

Ne görmelisin: `5 bulgu kabul edildi` (sayı kuyruktaki ve taslağı olan bulguların sayısıdır). İstersen şimdi
`decision.json`'u bir metin düzenleyicide aç ve girdileri değiştir ([Avukat kararının
alanları](#avukat-kararının-alanları)): örneğin bir bulgunun sonucunu düzeltmek için
`"action": "correct", "status": "compliant"`, taslağı değiştirmek için `"proposal_action": "edit",
"edited_text": "…"`, sözleşmeyi onaylamak için `"contract_decision": "approve"`. Girdileri silme:
kuyruktaki her bulgu listede kalmalı.

**4b. Kararı gönder.**

```bash
curl -s -X POST "http://127.0.0.1:8000/reviews/$REVIEW_ID/decision" -H "X-API-Key: $LAWYER_KEY" -H "Content-Type: application/json" --data-binary "@decision.json" -o decided.json -w "%{http_code}\n"
```
```powershell
curl.exe -s -X POST "http://127.0.0.1:8000/reviews/$REVIEW_ID/decision" -H "X-API-Key: $LAWYER_KEY" -H "Content-Type: application/json" --data-binary "@decision.json" -o decided.json -w "%{http_code}\n"
```

Ne görmelisin: `200`. `422` ise ayrıntı `decided.json`'dadır ve hiçbir şey değişmemiştir:
`decision.json`'u düzelt ve 4b'yi yeniden çalıştır. Sayfada **Getir**'e yeniden basınca "Durum:
Değişiklik istendi" (onayladıysan "Onaylandı"), "Son karar", "Karar veren" ve kartlarda "Avukat
kararı" görünür. Karar bir kez verilir: aynı istek bir daha `409` alır.

**5. Raporu ve audit kaydını oku.**

```bash
curl -s "http://127.0.0.1:8000/reviews/$REVIEW_ID/report" -H "X-API-Key: $LAWYER_KEY" -o report.json -w "%{http_code}\n"
curl -s "http://127.0.0.1:8000/reviews/$REVIEW_ID/audit" -H "X-API-Key: $LAWYER_KEY" -o audit.json -w "%{http_code}\n"
python3 -c "import json; r=json.load(open('report.json', encoding='utf-8')); print(r['status'], [(x['rule_id'], x['final_status']) for x in r['rule_results']])"
python3 -c "import json; a=json.load(open('audit.json', encoding='utf-8')); print('audit', len(a), a[0]['event'], '->', a[-1]['event'])"
python3 -c "import json, sys; sys.stdout.reconfigure(encoding='utf-8'); r=json.load(open('report.json', encoding='utf-8')); [print(c['rule_id'], 'Madde', c['clause_no'], '->', c['text']) for c in r['requested_changes']]"
```
```powershell
curl.exe -s "http://127.0.0.1:8000/reviews/$REVIEW_ID/report" -H "X-API-Key: $LAWYER_KEY" -o report.json -w "%{http_code}\n"
curl.exe -s "http://127.0.0.1:8000/reviews/$REVIEW_ID/audit" -H "X-API-Key: $LAWYER_KEY" -o audit.json -w "%{http_code}\n"
python -c "import json; r=json.load(open('report.json', encoding='utf-8')); print(r['status'], [(x['rule_id'], x['final_status']) for x in r['rule_results']])"
python -c "import json; a=json.load(open('audit.json', encoding='utf-8')); print('audit', len(a), a[0]['event'], '->', a[-1]['event'])"
python -c "import json, sys; sys.stdout.reconfigure(encoding='utf-8'); r=json.load(open('report.json', encoding='utf-8')); [print(c['rule_id'], 'Madde', c['clause_no'], '->', c['text']) for c in r['requested_changes']]"
```

Ne görmelisin: iki kez `200`; `changes_requested` ve her kuralın son sonucu
(`compliant` / `non_compliant` / `not_found`); `audit <satır sayısı> review_started ->
review_closed` (satır sayısı model ve tool çağrılarının sayısına göre değişir); istenen
değişiklikler: kural, madde ve tedarikçiye gidecek metin (kabul ya da düzenlenen her taslak için
bir satır). Rapor ve audit sayfada görünmez, yalnızca bu uçlardan okunur. Rapor karardan
önce `409` döner. Aynı okumalar `viewer` anahtarıyla maskeli döner; başka tenant'ın anahtarıyla
`404` döner.

Komutlar çalışma klasörüne `body.json`, `review.json`, `decision.json`, `decided.json`,
`report.json` ve `audit.json` yazar; içlerinde maskesiz sözleşme metni olabilir, işin bitince sil
(bash: `rm body.json review.json decision.json decided.json report.json audit.json`; PowerShell:
`Remove-Item body.json, review.json, decision.json, decided.json, report.json, audit.json`).

### Roller ve uçlar

Her istek `X-API-Key` başlığı taşır. Tenant ve rol **yalnızca** anahtardan çözülür
(`TENANT_API_KEYS`); istekteki `tenant`, `role` gibi alanlar yok sayılır. İki rol vardır:

- `lawyer`: inceleme başlatır, karar verir, maskesiz metni görür.
- `viewer`: yalnızca okur; kişisel veriler maskeli görünür; inceleme ve rapor yanıtlarında asıl
  sözleşme metni ve konumlar yoktur (audit kaydındaki enjeksiyon işareti satırları konumu iki
  role de gösterir).
  `GET` dışındaki her uç `viewer`'a 403 döner.

| Uç | İş | Rol |
|---|---|---|
| `POST /reviews` | Sözleşme metnini (`{"text": "…"}`) alır, inceler, bulguları döner | `lawyer` |
| `POST /reviews/{id}/decision` | Bulgu ve taslak kararları + son karar, tek istekte | `lawyer` |
| `GET /reviews/{id}` | Durum, maddeler, enjeksiyon işaretleri, bulgular, taslaklar | ikisi |
| `GET /reviews/{id}/report` | Son karardan sonra JSON rapor (önce 409) | ikisi |
| `GET /reviews/{id}/audit` | İncelemenin audit kayıtları (iki rolde de maskeli) | ikisi |
| `GET /` | Bulguları sözleşme metni üzerinde gösteren sayfa (yalnızca okur) | — |

Ayrıntı: `docs/architecture.md` §3. Arayüz yalnızca `GET /reviews/{id}` çağırır.

`POST /reviews` yanıtında `review_id`, `status` (`awaiting_lawyer`) ve her kural için bir bulgu
vardır: `status`, `quote`, `clause_id`, `proposal` (taslak ve `code_check`), `needs_review` ve
`review_reasons`.

### Avukat kararının alanları

Kuyruktaki (`needs_review: true`) her bulgu için `action` zorunludur: `accept` (modelin sonucu
kalır) ya da `correct` + `status` (`compliant` / `non_compliant` / `not_found`). Taslağı olan
kuyruktaki her bulgu için ayrıca `proposal_action` zorunludur: `accept` / `edit` + `edited_text` /
`reject`. Son karar `approve` ya da `request_changes`'tir; uygun olmayan bulgu varken `approve`
kabul edilir ve raporda `approved_with_noncompliant: true` olur. Kuyrukta olmayan bulgu listeye
yazılmak zorunda değildir; taslağı olmayan bulguda `proposal_action` 422'dir. Eksik ya da geçersiz
gövde 422 alır ve hiçbir şeyi değiştirmez.

Aşağıdaki iki girdi **gönderilebilir bir gövde değildir**, yalnızca girdi biçimidir: olduğu gibi
gönderilirse kuyruktaki diğer bulgular için `422` (`missing_action`) gelir. Kendi kararını vermek
için 4a'nın kurduğu `decision.json`'daki ilgili girdileri bu biçimde değiştir:

```json
{"findings": [{"rule_id": "insurance_minimum", "action": "accept",
               "proposal_action": "edit", "edited_text": "<düzenlenmiş madde metni>"},
              {"rule_id": "governing_law", "action": "correct", "status": "compliant"}],
 "contract_decision": "approve"}
```

## Eval nasıl koşulur

7 sentetik sözleşme (`evals/dataset/`), 35 kural kararı: 20 uygun, 12 uygun değil, 3 bulunamadı.
Metrikler etiketlerden **kodla** hesaplanır; LLM-as-judge yoktur. Tanımlar ve gerekçeleri:
`docs/evals.md` §4.

Tek komut; önce/sonra tablosunu basar ve sonucu `evals/results/` altına yazar. **Ücretsiz Groq
anahtarıyla** önce aşağıdaki `--limit 3` komutunu kullan; tam koşu limite takılabilir:

```bash
docker compose run --rm app python -m evals.run
```

"Önce" ve "sonra" aynı model çıktısından hesaplanır: model sözleşme başına bir kez çalışır,
doğrulama (`verify`) aynı ham bulgular üzerinde kapalı ve açık iki kez koşar. Groq'ta tam koşu
2026-11-12'de 206.387 ve 241.576 token (576 ve 813 sn), 2026-11-13'te 246.114 token (727 sn) tuttu.

**Ücretsiz Groq anahtarıyla** tam eval limite takılabilir ([Groq hız limiti](#groq-hız-limiti)).
Çalışacak komut ilk üç sözleşmeyi koşar (2. koşuda bu üç sözleşme ≈ 105K token tuttu):

```bash
docker compose run --rm -e MAX_CONCURRENCY=1 app python -m evals.run --limit 3
```

Kısmi koşuda tablo "3/7" yazar; koşulmayan sözleşmelere bağlı metrikler (enjeksiyon: sözleşme 6–7)
"ölçülmedi" görünür. Diğer seçenekler: limitsiz tam koşu için çevrimdışı mod (`LLM_PROVIDER=ollama`;
eval bu modda denenmedi) ya da hiç koşmadan `evals/results/` altındaki sonuç dosyaları.

Koşucu seçenekleri (`uv run python -m evals.run --help` model çağırmaz):

- **Çıkış kodları:** `0` tamam, sert eşikler tuttu · `1` yapılandırma ya da koşu hatası (argüman
  hatası dahil) · `2` en az bir kural hız limitine takıldı: tablo ve sonuç dosyası yazılmaz ·
  `3` koşu tamam, bir sert eşik tutmadı.
- **`--resume`:** tamamlanan her sözleşme sonuç klasörünün `.cache/` alt klasörüne yazılır
  (varsayılan `evals/results/.cache/`; `--results-dir` verilirse onun altında). Limit (`2`)
  sonrası aynı komut `--resume` ile kaldığı yerden devam eder. Önbellek varken bayraksız komut
  model çağırmadan `1` ile durur (unutulan bir `--resume` kotayı yakmasın); `--fresh` önbelleği
  silip baştan koşar. Önbellek içerik özetine bağlıdır: `src/`, `evals/run.py`,
  `evals/metrics.py`, `evals/cache.py`, `uv.lock`, sağlayıcı, model ya da sözleşmenin kendisi
  (metin, etiket kaydı, kural seti) değişirse ilgili kayıt geçersiz olur; doküman değişikliği
  etkilemez.
- **`--prepare-only`:** model çağırmaz; yalnızca maskeleme (PII) ve enjeksiyon taraması ölçülür.
- **`--dataset-dir`, `--results-dir`, `--commit`:** başka bir veri kümesi (holdout), sonuç klasörü
  ve sonuç dosyasına yazılacak commit.
- **`--keep-drafts FILE`:** taslağı olan her bulgunun maskeli taslak metnini ve kod denetiminin
  sonucunu verilen JSONL dosyasına ekler (sonuç klasörünün dışında olmalı; Docker'da
  `/app/evals/drafts/` altında; `evals/drafts/` commit edilmez). Sonuç dosyası yine metinsizdir.
  Ayrıntı ve sınırları: `docs/evals.md` §6.
- `src/` değiştiyse imaj yeniden kurulur: `docker compose build app` (imaj `src/`'yi kopyalar).
- `LANGSMITH_API_KEY` verilirse tam koşu dataset'i ve `before` / `after` experiment'lerini
  LangSmith'e yükler (sözleşme metni, alıntı ve PII gitmez; `docs/evals.md` §7). Bu yol gerçek
  sunucuya karşı **denenmedi**; bütün koşular `langsmith: "local"` ile yapıldı.

## Eval sonuçları

Groq `openai/gpt-oss-120b`, Docker, `MAX_CONCURRENCY=1`, 7 / 7 sözleşme, 35 karar.
Aşağıdaki ilk tablolar 2. koşudandır (2026-11-12):
`evals/results/20261112-200616_groq_openai-gpt-oss-120b.json` (kod `4ccc03d`, `langchain-core`
1.7.7). Ayrıntı: `docs/evals.md` §4–§5.

Bu koşudan sonra üç düzeltme yapıldı (2026-11-13: `verify`'da alıntı dışındaki değerlerin
okunması, alan kodu parantezli telefonun maskelenmesi, BOM'dan sonraki ilk başlık), ardından
sağlayıcı seçimi (Adım 18c / 18d) ve `langchain-core` 1.8.0 geldi. Bugünkü kod 2026-11-13'te
Groq'ta bir kez daha koşuldu ([Üçüncü Groq koşusu](#üçüncü-groq-koşusu-bugünkü-kod)); aynı kod
Anthropic `claude-sonnet-5-5` ile de bir kez koşuldu (ayrı bölüm; başka model, Groq sayılarıyla
karşılaştırılmaz). Modelsiz ölçüm (`--prepare-only`) düzeltmelerden sonra da aynıdır: garanti
edilen PII sızıntısı 0 / 52, garanti edilmeyen 2 / 5.

2026-11-14'te Adım 19c üç hatayı düzeltti (kodun denetleyemediği taslak artık avukata gider;
taslak denetimi "taslakta kuralı çiğneyen değer yok" oldu; ad keşfine iki yol eklendi) ve
`claude-sonnet-5-5` bir kez daha koşuldu ([Adım 19c'den sonra](#adım-19cden-sonra-claude-sonnet-5-5-yeniden)).
Groq bu kodla koşulmadı. Aşağıdaki Groq tabloları ve ilk Sonnet koşusu bu değişikliklerden
öncedir; sayıları yeniden yazılmadı.

### Önce/sonra: doğrulama katmanı kapalı → açık

| Metrik | Önce | Sonra |
|---|---|---|
| Güvensiz hata (yanlış sonuç, avukata gitmeden) | 2 / 35 | 0 / 35 |
| Geçersiz alıntıyla kabul edilen bulgu | 0 | 0 |
| Enjeksiyon: avukata gitmeyen yanlış "uygun" | 0 | 0 |
| Avukata yönlendirme | 0 / 35 | 12 / 35 |
| Gereksiz yönlendirme | — (kuyruk boş) | 10 / 12 |

**Bu tablo bu koşuda ne gösteriyor (dürüst not).** Güvensiz hatadaki 2 → 0 farkının **tamamı**
sözleşme 4'teki iki yanlış "bulunamadı" satırından geliyor (arama iki maddeyi kaçırdı; aşağıda
[Bilinen sınırlar](#bilinen-sınırlar)). Doğrulama kapalıyken bu iki satır sessizce geçiyor; açıkken
her "bulunamadı" avukata gittiği için kuyruğa düşüyor. Model bu koşuda alıntıda ve sayıda hata
yapmadı. Yani tablo bu koşuda alıntı / sayı kontrolünün değerini göstermiyor; bedelini gösteriyor
(35 kararın 12'si avukata gidiyor).

**Gereksiz yönlendirme dökümü.** Bu metrik "boşa giden iş" değildir: kuyruktaki satırlardan
`status`'ü avukat değiştirmeden kalacak olanları sayar. 2. koşunun kuyruğu (12 satır):

| Satır | Sayı | Neden kuyrukta |
|---|---|---|
| Yanlış "bulunamadı" (sözleşme 4: `termination_notice`, `confidentiality`) | 2 | Arama kaçışı; gerekli yönlendirme |
| Beklenen "bulunamadı" (02 `governing_law`, 03 `payment_term`, 03 `confidentiality`) | 3 | Tasarım gereği: her "bulunamadı" avukata gider |
| Enjeksiyon şüphesi (sözleşme 6'nın beş kuralı) | 5 | Tasarım gereği: tarama işaret koyduysa bütün bulgular |
| `ambiguous_value` (sözleşme 4 `payment_term`) | 1 | Haklı: maddede gerçekten 30 ve 75 gün var |
| `ambiguous_value` (sözleşme 4 `price_increase_cap`) | 1 | Gürültü: "yüzde on beşini" (çekimli sayı sözcüğü) |

"Gereksiz" sayılan 10 satırın 8'i tasarım gereğidir (3 beklenen "bulunamadı" + 5 enjeksiyon), 2'si
doğrulama sinyalidir (biri haklı, biri gürültü).

### Metrik özeti (2. koşu, "sonra")

| Metrik | Değer | Eşik |
|---|---|---|
| Güvensiz hata oranı | 0 / 35 | ≤ %5 |
| Kaçırılan uygunsuzluk | 1 / 12 | ≤ %20 |
| Yanlış bulgu | 0 / 11 | ≤ %20 |
| Etiket doğruluğu | 33 / 35 | raporlanır |
| Alıntı geçerliliği (avukata gitmeyen alıntılı kararlar; bekçi, aşağıda) | 23 / 23 | %100 |
| Alıntı geçerliliği, ham oran (alıntılı bütün kararlar) | 30 / 30 | raporlanır |
| Doğru madde | 30 / 30 | raporlanır |
| Arama isabeti | 30 / 32 | raporlanır |
| Taslak oranı (uygun değil → taslak var) | 11 / 11 | raporlanır |
| Öneri uygunluğu (kodun doğruladığı taslaklar) | 10 / 10 (1 taslak doğrulanamadı) | raporlanır |
| Kritik kural yakalama | 1 / 1 | raporlanır |
| Enjeksiyon: tarama (sözleşme 6) | evet | evet |
| Enjeksiyon: model kandı mı / sonuç (sözleşme 6–7) | 0 / 0 | sonuç 0 |
| PII sızıntısı (garanti edilen değerler) | 0 / 52 | 0 |
| Garanti edilmeyen PII sızıntısı | 2 / 5 | raporlanır |
| Token (girdi + çıktı) | 230.861 + 10.715 = 241.576 | raporlanır |
| Süre (toplam / sözleşme ortalaması) | 813,4 sn / 116,2 sn | raporlanır |
| Tahmini maliyet | $0,0411 | raporlanır |

Bütün sert eşikler üç Groq koşusunda da tuttu; eşikler sonuca göre değiştirilmedi (küçük örneklem:
bir satır 2,9 puan). Kaçırılan tek uygunsuzluk sözleşme 4 `termination_notice`'tir: arama maddeyi
bulamadı, satır "bulunamadı" olarak avukata gitti. Sözleşme 7'deki (tarama kalıbında olmayan)
gizli talimatı tarama yakalamadı, model de kanmadı.

**Alıntı geçerliliği eşiği ölçüm değil bekçidir.** Alıntısı maddede bulunmayan bulgu
`quote_not_found` ile avukata gittiği için, avukata gitmeyenlerde oran yapı gereği %100'dür; eşik
yalnızca bu bağı bozan bir kod yolunu yakalar. Modelin alıntı kalitesini ham oran gösterir
(alıntılı bütün kararlar, kuyruğa bakılmadan; komutun tablosunda yoktur, sonuç dosyasındaki
satırların `quote_valid` alanından sayılır): 2. koşuda 30 / 30, 1. koşuda 29 / 30. 1. koşudaki tek
geçersiz satırın olası sebebi o günkü `verify` kusurudur, kesin değildir (`docs/evals.md` §4).

**PII.** Maskelenmesi garanti edilen değerlerde sızıntı 0 / 52; bu sayı yapı gereği 0'dır
(maskeleme deterministiktir, sayı yalnızca maskelemeyi atlayan bir kod yolunu yakalar).
Maskeleyicinin kapsamını ikinci sayı gösterir: garanti edilmeyen 5 değerden 2'si modele gitti;
ikisi de kişi adıdır (sözleşme gövdesindeki ad ve etiketsiz imza satırındaki ad). Adım 19c'nin
iki ad keşfi yolundan sonra 0 / 5'tir (modelsiz ölçüm ve 2026-11-14 Sonnet koşusu); bu iki yol
da en iyi çabadır, adlar "garanti edilmeyen" listesinde kaldı.

### İkincil tablo (a): düzeltmelerin etkisi, 1. koşu → 2. koşu
1. koşu (`evals/results/20261112-173317_groq_openai-gpt-oss-120b.json`, kod `9b6ca9a`) iki hata
gösterdi; ikisi kendi commit'iyle düzeltildi: `2a6fbe3` (`verify`'da cümle sınırını aşan birim,
cümle sonunda biten doğru alıntıyı reddediyordu) ve `4ccc03d` ("uygun değil" bulgudan sonra
`submit_finding`'in yanıtı modelden taslak ister).

| Metrik ("sonra") | 1. koşu | 2. koşu |
|---|---|---|
| Taslak oranı | 1 / 11 | 11 / 11 |
| Avukata yönlendirme | 19 / 35 | 12 / 35 |
| Alıntı geçerliliği (bekçi; parantezde ham oran) | 16 / 16 (29 / 30) | 23 / 23 (30 / 30) |
| Gereksiz yönlendirme | 17 / 19 | 10 / 12 |
| Token | 206.387 | 241.576 |

Neyi göstermez: iki koşu aynı günün, tek modelin iki koşusudur. 35 kararın `status`'ü iki koşuda
aynıdır; fark taslaklardan ve kuyruktan gelir (1. koşuda kuyruğun 10 satırı taslaksız "uygun
değil", `missing_proposal`). Alıntı geçerliliğinin paydası kuyruk küçüldüğü için büyüdü (metrik
yalnızca avukata gitmeyen alıntılı kararları sayar ve yapı gereği %100'dür; karşılaştırılabilir
olan parantezdeki ham orandır). Taslak farkının ne kadarının tool yanıtından,
ne kadarının prompt değişikliğinden geldiği ayrı ölçülmedi; başka modelde denenmedi. Taslak
yazımı yönlendirmedir, kodla zorlanmaz.

### İkincil tablo (b): maskeleyici düzeltmesi (Adım 17b)
Model çağırmayan ölçüm (`--prepare-only`), iki dosya:
`evals/results/20261112-144542_groq_openai-gpt-oss-120b_prepare-only.json` (önce) ve
`evals/results/20261112-160910_groq_openai-gpt-oss-120b_prepare-only.json` (sonra).

| Metrik | Önce | Sonra |
|---|---|---|
| Garanti edilmeyen PII sızıntısı | 5 / 5 | 2 / 5 |
| PII sızıntısı (garanti edilen) | 0 / 52 | 0 / 52 |

Düzeltme boşluklu TCKN'yi, serbest gruplu IBAN'ı ve 3-3-2-2 yazılmış öneksiz cep telefonunu
ekledi; kalan ikisi adlardır (ad tanıma kapsam dışı). Sonuç deterministiktir. Düzeltme
dataset'teki bilinen boşluklara (3 örnek) bakılarak yazıldı; tablonun gösterdiği şey eval → test →
düzeltme döngüsüdür, maskelemenin genel kapsamı değil. Üç değer, kayıt görünür kalsın diye
etikette "garanti edilmeyen" listesinde bırakıldı (`docs/evals.md` §3).

### Üçüncü Groq koşusu (bugünkü kod)
`evals/results/20261113-204859_groq_openai-gpt-oss-120b.json`: 2026-11-13, commit etiketi
`51d9c44` (bugünkü `main` kaynağı), `langchain-core` 1.8.0 ile ilk koşu, koşu boyunca 429 yok.
Tek koşu. Ayrıntı: `docs/evals.md` §4 "Üçüncü Groq koşusu".

| Metrik | 2. koşu (`4ccc03d`) | 3. koşu (`51d9c44`) |
|---|---|---|
| Güvensiz hata, önce → sonra | 2 / 35 → 0 / 35 | 2 / 35 → 0 / 35 |
| Kaçırılan uygunsuzluk / yanlış bulgu | 1 / 12 · 0 / 11 | 1 / 12 · 0 / 11 |
| Etiket doğruluğu / arama isabeti | 33 / 35 · 30 / 32 | 33 / 35 · 30 / 32 |
| Alıntı geçerliliği (bekçi; parantezde ham oran) | 23 / 23 (30 / 30) | 21 / 21 (30 / 30) |
| Taslak oranı / öneri uygunluğu | 11 / 11 · 10 / 10 | 11 / 11 · 9 / 10 |
| Avukata yönlendirme / gereksiz | 12 / 35 · 10 / 12 | 14 / 35 · 12 / 14 |
| Token / süre / maliyet | 241.576 · 813,4 sn · $0,0411 | 246.114 · 726,8 sn · $0,0418 |

Status'ler 2. koşuyla aynı; iki yanlış yine sözleşme 4'teki iki `not_found` (arama kaçışı). Kuyruğa
iki satır eklendi: 02 `late_penalty_cap`'in taslağı kod kontrolünden geçmedi
(`proposal_noncompliant`; taslak metni saklanmadığı için sebebi bu koşuda gösterilemedi) ve 03 `termination_notice` bulgudan sonraki bir
model hatasıyla kesildi (`run_interrupted`, `llm_error`; status doğru, sebebi bilinmiyor). Bütün
sert eşikler tuttu. Tek koşudur: farklar `langchain-core` 1.8.0'a ya da koda bağlanmaz; aynı
sağlayıcı `temperature=0`'da da koşudan koşuya farklı çıktı verebilir.

### Başka bir model: Anthropic claude-sonnet-5-5
`evals/results/20261113-223738_anthropic_claude-sonnet-5-5.json`: 2026-11-13, commit etiketi
`a9ad2ed` (bugünkü `main` kaynağı), `LLM_TEMPERATURE=none`, `MAX_CONCURRENCY=1`, `max_tokens` 2048.
Tek koşu. **Başka model: Groq sayılarıyla karşılaştırılmaz**, `langchain-core` hakkında da bir şey
söylemez. Ayrıntı: `docs/evals.md` §4 "Başka bir model".

| Metrik (Anthropic `claude-sonnet-5-5`) | Önce | Sonra |
|---|---|---|
| Güvensiz hata | 0 / 35 | 0 / 35 |
| Avukata yönlendirme | 3 / 35 | 13 / 35 |
| Gereksiz yönlendirme | 3 / 3 | 13 / 13 |

"Sonra": etiket doğruluğu 35 / 35, kaçırılan uygunsuzluk 0 / 12, yanlış bulgu 0 / 12, arama
isabeti 32 / 32 (sözleşme 4'teki iki dolaylı madde bulundu), doğru madde 32 / 32, alıntı
geçerliliği 22 / 22 (ham oran 32 / 32), taslak oranı 12 / 12, öneri uygunluğu 8 / 11 (1 taslak
doğrulanamadı), enjeksiyon sonucu 0, PII 0 / 52 ve 2 / 5. Token 593.334 + 23.774 = 617.108;
277,3 sn (sözleşme başına 39,6 sn); maliyet bilinmiyor (sağlayıcı tablosunda birim fiyat yok).
Kuyruğun 13 satırında da status doğru: 3 beklenen "bulunamadı", 5 enjeksiyon şüphesi, 3 taslağı
kod kontrolünden geçmeyen bulgu (02 ve 07 `late_penalty_cap`, 04 `payment_term`), 1
`ambiguous_value`, 1 modelin kendi devrettiği bulgu. Bu koşuda doğrulamanın yakaladığı yanlış
sonuç yok; görünen yalnızca bedeli. Bütün sert eşikler tuttu. Bu koşunun gösterdiği üç hata
(kod kontrolünden kalan üç taslak, maskelenmeyen iki ad, kodun denetleyemediği taslağın
avukata gitmemesi) Adım 19c'de düzeltildi; aşağıda.

### Adım 19c'den sonra: claude-sonnet-5-5 yeniden
`evals/results/20261114-032329_anthropic_claude-sonnet-5-5.json`: 2026-11-14, commit etiketi
`7f7cbf9` (Adım 19c'nin kodu, `step-19c` dalı), ayarlar yukarıdakiyle aynı (`LLM_TEMPERATURE=none`,
`MAX_CONCURRENCY=1`), `--fresh`. Tek koşu; taban yukarıdaki koşudur. Ayrıntı ve satır satır
karşılaştırma: `docs/evals.md` §4 "Adım 19c'den sonra".

| Metrik ("sonra") | Önceki Sonnet koşusu (`a9ad2ed`) | Adım 19c'den sonra (`7f7cbf9`) |
|---|---|---|
| Güvensiz hata | 0 / 35 | 0 / 35 |
| Etiket doğruluğu / arama isabeti | 35 / 35 · 32 / 32 | 35 / 35 · 32 / 32 |
| Alıntı geçerliliği (bekçi; parantezde ham oran) | 22 / 22 (32 / 32) | 24 / 24 (32 / 32) |
| Avukata yönlendirme / gereksiz | 13 / 35 · 13 / 13 | 11 / 35 · 11 / 11 |
| Öneri uygunluğu (doğrulanamayan taslak) | 8 / 11 (1 / 12) | 11 / 11 (1 / 12) |
| Garanti edilmeyen PII sızıntısı | 2 / 5 | 0 / 5 |
| Token / süre | 617.108 · 277,3 sn | 630.660 · 253,4 sn |

Status ve madde 35 satırın hiçbirinde değişmedi. Kuyruktan üç satır çıktı (02 ve 07
`late_penalty_cap`, 04 `payment_term`: taslakları artık kod kontrolünden geçiyor), bir satır girdi
(05 `governing_law`: kodun denetleyemediği taslak, sebep `proposal_unverifiable`). Bu dört satır
değişikliğe bağlanabildi: saklanan taslak eski denetimle kalıyor, yenisiyle geçiyor (okunan
değerler %1 ve %10; 30 ve 45 gün); 05'te taslak var ve kod doğrulayamadı. Taslaklar elle okundu:
üst sınır %10 ve ithal süresi 45 gün gerçekten yazılmış; kod kontrolünden kalan taslak yok. Önceki
koşunun taslak metinleri saklanmadığı için orada modelin aynı metni yazıp yazmadığı bilinmiyor.
Kalan farkların çoğu (altı satırda arama sayısı, sözleşme 6'da bir satırın `agent_escalated`
sebebinin düşmesi) hiçbir değişikliğin etkileyemeyeceği satırlardadır: koşu oynaklığı. 05
`governing_law`'daki arama sayısı farkı belirsizdir (sözleşme 5'in maskeli metni değişti). Bütün sert eşikler tuttu. İki koşu da tek koşudur: bu tablo
düzeltmelerin beklenen satırlarda çalıştığını gösterir, yönlendirmenin genel olarak düştüğünü
ölçmez.

## Görülmemiş iki sözleşme (holdout)

Adım 18'de prompt ve `verify` 7 sözleşmeye bakılarak değiştirildi; o set artık geliştirme setidir.
Bu yüzden projeyi görmeyen ayrı bir oturuma yalnızca `docs/CASE.md` ve iki tenant'ın kural setleri
verilerek 2 sözleşme ve beklenen sonuçları yazdırıldı (`evals/holdout/`: sözleşmeler,
`labels.json`, `expected.md`; 2 × 5 = 10 kural kararı). Kurallar:

- **Tek atış.** Koşu bir kez yapılır, sonuç olduğu gibi yazılır; bu sonuca bakılarak kod, prompt,
  etiket ya da sözleşme değiştirilmez. Limit yüzünden yarıda kalan koşunun `--resume` ile
  tamamlanması ikinci atış sayılmaz.
- **Metin değişmez.** Sözleşmeler bayt bayt kopyalandı; sha256'ları testle denetlenir
  (`tests/test_holdout.py`).
- **Ayrı tutulur.** Sonuç `evals/results/holdout/` altına yazılır; eşiklere ve yukarıdaki önce/sonra
  tablosuna girmez.

Komut:
```bash
docker compose run --rm app python -m evals.run --dataset-dir evals/holdout \
  --results-dir evals/results/holdout --commit <holdout etiketlerinin commit'i>
```

**Modelsiz ön kontrol**
(`evals/results/holdout/20261112-215127_groq_openai-gpt-oss-120b_prepare-only.json`, commit
`aa1e40e`): garanti edilen PII sızıntısı 0 / 18; garanti edilmeyen 4 / 4 (dördü de kişi
adı); iki sözleşmede de enjeksiyon taraması işaret koymadı.

**Sonuç** (`evals/results/holdout/20261113-085836_groq_openai-gpt-oss-120b.json`, commit
`aa1e40e`; Groq `openai/gpt-oss-120b`). Koşu iki güne yayıldı: 1. sözleşme 2026-11-12'de
tamamlandı, 2. sözleşme Groq limitine takıldı (çıkış kodu 2) ve 2026-11-13'te `--resume` ile
koşuldu. İlk sözleşme yeniden koşulmadı; araya kod, prompt ya da etiket değişikliği girmedi.

| Metrik | Önce (doğrulama kapalı) | Sonra |
|---|---|---|
| Etiket doğruluğu | 10 / 10 | 10 / 10 |
| Güvensiz hata | 0 / 10 | 0 / 10 |
| Avukata yönlendirme | 0 / 10 | 4 / 10 |
| Gereksiz yönlendirme | — (kuyruk boş) | 4 / 4 |

Diğer değerler ("sonra"): doğru madde 8 / 8, arama isabeti 8 / 8, alıntı geçerliliği 6 / 6 (ham
oran 8 / 8), taslak oranı 4 / 4 (dördü de kod kontrolünden geçti), kaçırılan uygunsuzluk 0 / 4,
yanlış bulgu 0 / 4, garanti edilen PII 0 / 18, garanti edilmeyen 4 / 4. Token 72.058 + 3.508 =
75.566; 364,2 sn; $0,0129.

Kuyruğa düşen 4 satır:

- 2 satır beklenen "bulunamadı" (`governing_law`, `confidentiality`): tasarım gereği.
- 2 satır `numeric_mismatch` (1. sözleşme: `termination_notice` ve `late_penalty_cap`): sonuç
  ve madde doğru, alıntı maddede geçiyor. Bu sebep iki yoldan gelebilir: modelin `value` /
  `unit` alanına yazdığı değer kodun alıntıdan okuduğuyla uyuşmamıştır, ya da model maddenin
  değeri içermeyen bir parçasını alıntılamıştır. Modelin alıntısı ve yazdığı değer kayıtlı
  olmadığı için hangisi olduğu bilinmiyor. Kod iki maddenin tamamından değeri doğru okuyor
  (2 hafta; %8). Tek atış kuralı gereği düzeltilmedi.

**Ne gösterir, ne göstermez.** Taslak yönlendirmesi ve alıntı / madde bulma, ayar yapılırken
görülmemiş iki sözleşmede de çalıştı; dolaylı yazılmış değerler ("iki hafta", "üç ay", "18 ay",
"bedelin iki katı") doğru sonuçlandı. Ama bu 10 karar, tek model, tek koşudur; bir oran değil,
bir yoklamadır. Sözleşmeler yine bir LLM'e yazdırıldı ve madde başlıkları kuralın sözcüklerini
kullanıyor ("Fesih", "Garanti", "Sigorta"): aramanın bilinen sınırını (kural sözcüklerini
kullanmayan madde) ve enjeksiyonu sınamıyor. Doğrulama katmanı burada hiçbir hatayı yakalamadı
(yakalanacak yanlış sonuç yoktu); yalnızca bedeli görünüyor (10 kararın 4'ü avukata gitti).

## Groq hız limiti

Gözlemler (2026-11-12 ve 2026-11-13, aynı anahtar):

- Gün içinde sırasıyla tek sözleşmelik deneme (≈ 25K token), 1. tam koşu (206.387 token, 576 sn)
  ve 2. tam koşu (241.576 token, 813 sn) hiç 429 almadan bitti; ham hız dakikada ≈ 18–21K token,
  toplam ≈ 473K token.
- Ardından holdout koşusunda 1. sözleşme tamamlandı, 2. sözleşmenin 5 kuralının 5'i hız limitine
  takıldı (≈ 22:04 UTC). Koşucu çıkış kodu 2 ile durdu, tablo ve sonuç dosyası yazmadı; tamamlanan
  sözleşme önbellekte kaldı. Çıkış kodu 2 gerçek limitte ilk kez burada görüldü ve tasarlandığı
  gibi çalıştı. Ertesi gün (≈ 09:00 UTC) aynı komut `--resume` ile kalan sözleşmeyi koşup
  tamamladı; ilk sözleşme önbellekten alındı.
- 22:51 UTC'de yanıt başlıkları: `x-ratelimit-limit-requests` 1000 (günlük istek),
  `x-ratelimit-limit-tokens` 8000 (dakikalık token). Günlük token sınırı başlıklarda yok. O anda
  küçük istekler başarılı dönüyordu; istek sayacı sürekli dolan bir kova gibi davranıyordu (kalan
  919 / 1000).
- 2026-11-13: üçüncü tam koşu (246.114 token, 727 sn) 429 almadan bitti. ≈ 22:00 UTC'de başka bir
  tam koşu (`--fresh`; [geri alınan madde listesi denemesinin](#daha-fazla-vaktim-olsaydı) kodu)
  1. sözleşmeyi tamamladı, 2. sözleşmenin 5 kuralının 5'i hız limitine takıldı: çıkış kodu 2,
  tablo ve sonuç dosyası yok. Hemen ardından yoklama: durum 200,
  `x-ratelimit-remaining-tokens` 4490 / 8000, `remaining-requests` 835 / 1000; günlük token
  sınırı başlıklarda yine yok. O gün anahtarda ≈ 365K ham token harcanmıştı (önceki gün limit
  ≈ 510K'da gelmişti). Hangi sınırın devreye girdiği bilinmiyor.

Bilinmeyenler: limite hangi sınırın (dakikalık ya da günlük token) yol açtığı; ham token toplamının
belgelenen 8K TPM / 200K TPD'yi neden aşabildiği (olası açıklama, önbelleğe alınan girdi
token'larının limite sayılmaması, **doğrulanmadı**); günlük sınırın geçerli değeri ve ne zaman
dolduğu. Groq konsolunun Limits / Usage sayfası bu oturumda kontrol edilmedi.

Sonuç: ücretsiz katmanda tam eval limite takılabilir. Rehber: `MAX_CONCURRENCY=1` ve `--limit 3`;
limitte çıkış kodu 2 gelirse aynı komut `--resume` ile; ya da çevrimdışı mod. Yüksek limitli bir
hesapta `MAX_CONCURRENCY=4` verilebilir (`docs/tech_stack.md` "Hangi plan?").

## Veri nereye gidiyor

Maskeleme graph'tan önce yapılır; model, indeks, checkpoint, audit log ve trace yalnızca maskeli
metni görür. Kişi adları (taraf / imza bloğundaki), TCKN, IBAN, telefon ve e-posta yer tutucuyla
değiştirilir; eşleme makinede, tenant'ın veritabanında kalır ve maske yalnızca `lawyer` yanıtında
açılır. Tutarlar, süreler, tarihler ve şirket adları maskelenmez (kurallar bunlara ihtiyaç duyar).

- **Varsayılan mod:** maskeli madde metni, kural metni ve agent mesajları Groq'a gider; tutarlar ve
  ticari şartlar da. Maskeleyicinin kaçırdığı kimlik bilgisi (ör. gövdede geçen ad) Groq'a
  gidebilir. Groq'un saklama beyanı (30 güne kadar log, Zero Data Retention ile kapanır, eğitimde
  kullanmaz) **yalnızca Groq içindir**.
- **Başka bir sağlayıcı:** aynı veri (maskeli metin ve maskelemenin kaçırdıkları) seçilen
  sağlayıcıya gider; onun saklama ve kullanım koşulları kullanıcının sorumluluğundadır. Örnek:
  Gemini'nin ücretsiz katmanında Google gönderilen içeriği ürünlerini geliştirmede kullanabilir ve
  insan değerlendiriciler okuyabilir. OpenAI uyumlu bir adreste audit'in `review_started` satırı
  `openai`'yi ve modeli yazar, adresi yazmaz: isteğin gerçekte gittiği yer kayıtta görünmez.
- **Çevrimdışı mod:** model yereldir; veri makineden çıkmaz.
- **LangSmith:** çevrimdışı kurulumda LangSmith değişkenleri verilmez; uygulamada açılırsa gerçek
  sözleşmelerin maskeli trace'i dışarı gider.

Tam tablo: `docs/architecture.md` §6 "Veri nereye gidiyor".

## Bilinen sınırlar

- **Kanıtlı ama yanlış "uygun".** Model alakasız ama sözleşmede gerçekten geçen bir alıntıyla
  "uygun" derse ve kuralda sabit eşik (`check`) yoksa bulgu avukatsız geçer. Kod alıntının varlığını
  doğrular, konuyla ilgisini doğrulayamaz. Her "bulunamadı"yı kuyruğa almak bunu kapatmaz.
- **Arama yalnızca BM25'tir.** Groq koşularında arama isabeti 30 / 32: sözleşme 4'te madde 9 ("Sır Saklama") ve
  madde 11 ("Sözleşmenin Sona Ermesi … bir ay önceden … haber vererek") kuralın sözcüklerini
  kullanmıyor ve bulunamadı; iki kural "bulunamadı" döndü ve avukata gitti. Dataset, tokenizer ve
  prompt buna göre ayarlanmadı (madde 11 için bu karar sonuçlar görüldükten sonra alındı;
  `docs/evals.md` §3 "Sonuç"). Groq'ta aynı iki kaçış üçüncü kez görüldü (2026-11-13, sözleşme 4
  tek başına); audit'teki sorgular "fesih bildirim süresi", "fesih", "bildirim süresi" ve
  "gizlilik", "gizli bilgi", "confidential", "gizlilik yükümlülüğü"ydü: arama yalnızca anahtar
  sözcükle çalışır, model aynı kökü değiştirerek dener. Anthropic `claude-sonnet-5-5` iki maddeyi
  de buldu (başka model, tek koşu; Groq'la karşılaştırılmaz).
- **Taslak yazımı kodla zorlanmıyor.** "Uygun değil" bulgudan sonra taslağı tool yanıtı, prompt ve
  tool açıklaması ister; model yazmazsa bulgu `missing_proposal` ile avukata gider.
- **`verify` taslağın maddesini bulgunun maddesiyle karşılaştırmıyor.** Model fikir değiştirip başka
  madde için bulgu bildirirse eski taslak yeni bulguya ekli kalır.
- **Cümle sınırı düzeltmesi (`2a6fbe3`).** Kabul edilen kayıp: cümle sayıyla bitip sonraki cümle
  büyük harfli bir birim sözcüğüyle başlıyorsa ("Süre 30. Gün hesabı …") ya da sıra sayısından
  sonra birim büyük harfle yazılmışsa ("45. İş gününde") değer okunmaz. Kalan sınır: tamamı büyük
  harfli metinde ("FİYAT 5. AYRICA X.") o cümleyi kesen alıntı eşleşmez. Son `verify` sürümü
  bağımsız bir incelemeden geçmedi (yalnızca eski sürümle 30.000 rastgele girdide karşılaştırıldı).
  Ayrıntı: `docs/agent_design.md` §5.
- **Ad keşfi en iyi çabadır.** Ad üç yoldan bulunur: etiket ("Ad Soyad:", "Yetkili:",
  "temsilen"), aynı satırdaki e-postanın yerel kısmı (`elif.soylu@` → "Elif Soylu"; Adım 19c) ve
  hemen altı "İmza" olan etiketsiz satır (Adım 19c); bulunan ad bütün metinde aranır. Kaçanlar:
  baş harfli ya da tek sözcüklü e-posta (`a.yilmaz@`, `info@`), e-postası başka satırda ya da
  farklı yazılmış ad, "İmza"dan önce boş satır, satırında başka metin olan ad, gövdede 3+ kez
  geçen (tanımlı terim sayılan) ad, hiçbir yolun yakalamadığı gövde adı. Fazla maskeleme: imza
  satırında şirket eki taşımayan şirket adı ("Acme Teknoloji"), listede olmayan birim ya da rol
  sözcükleri, işlev posta kutuları ("Müşteri Hizmetleri" + `musteri.hizmetleri@`); tireli soyad
  yarım maskelenir. Sözleşme 5'in iki adı bu yollarla maskelenir ama "garanti edilmeyen"
  listesinde kaldı. Tam liste: `docs/agent_design.md` "PII maskeleme" → "Kişi adı".
- **Ada boşluksuz yapışık değer.** Ad ile e-posta boşluksuz yapışık yazılırsa
  (`Elif Soylu-elif@ornek.com`) e-posta artık her durumda maskelenir (Adım 19d; etiketli adda,
  maddede ve avukatın düzenlediği taslakta da): ad, e-postaya ve öteki katı değerlere yol verir.
  Bedeli: adın e-postaya girmeyen kısmı o geçişte ham kalır (`Elif [EPOSTA_1]`; e-posta kalıbı
  yalnızca ASCII harf aldığından `Kemal Uçar-kemal@ornek.com` → `Kemal Uç[EPOSTA_1]`), ve
  e-postaya da yapışık zayıf bir değer ham kalabilir. Önceden var olan, değişmeyen iki sınır:
  etiketli ad `TR` ile bitip ardından boşluklu serbest gruplu IBAN geldiğinde
  (`Yetkili: Ahmet TR 33 0006 …`) IBAN'ın rakamları ham kalır; bir ada boşluksuz yapışık IBAN
  (`Elif SoyluTR330006100519786457841326`) hiç maskelenmez, ne ad ne IBAN (açık soru,
  `docs/spec.md` §10).
- **Maskelenmeyen diğer kimlik bilgileri.** Satıra
  bölünmüş, tireli ya da çift boşluklu IBAN, ayraçsız öneksiz cep numarası, öneksiz sabit hat,
  öneksiz yazılmış parantezli alan kodu (`(532) 123 45 67`), `+90 (0) 212 555 12 34` yazımı ve
  binlik gibi gruplanmış TCKN maskelenmez. Önekli telefon, alan kodu parantez içinde yazılsa da maskelenir
  (`0 (532) 123 45 67`, `(0212) 555 12 34`).
- **Maskeleyicinin bilinen yanlış yakalamaları.** `5` ile başlayan her 3-3-2-2 sayı telefon sayılır
  (`500 000 00 00`); tek boşlukla ayrılmış ayrı sayılar 11 haneye denk gelip checksum'ı tutarsa
  (yaklaşık yüzde bir) TCKN sayılır; parantez içinde `0` + üç hane ve ardından 3-2-2 hane telefon
  sayılır (`(0347) 3284041 TL`). Telafi: "uygun" bulgunun maddesinde TCKN ya da telefon yer
  tutucusu varsa bulgu avukata gider (`masked_value_in_clause`); değer başka maddedeyse ya da
  bulgu "uygun değil" ise bu telafi çalışmaz.
- **Madde bölme iki başlık biçimini tanır.** `Madde N` ve (metinde `Madde N` yoksa) numaralı
  Markdown başlığı (`## 4. Fesih`, Adım 19e). Roma rakamlı (`## IV.`) ya da işaretsiz
  (`4. Fesih`) başlıklı sözleşme paragraflara bölünür; o zaman "4.1." ve "4.2." ayrı parçadır ve
  `verify` alıntı dışındaki, kuralı çiğneyen değeri komşu paragrafta göremez: yanlış bir "uygun"
  avukata gitmeden geçebilir (koşularak gösterildi). Numaralı başlık bölmesinin bir geçişi de
  güvensiz yöndedir (kabul edildi): önünde boş satır olmayan başlık yeni madde açar ve Adım
  19e'den önce aynı paragrafta kaldığı için rastlantıyla görülen, önceki bölümdeki çiğneyen değer
  artık görülmez (`Madde N` başlıklı aynı sözleşmedeki davranışla aynı). Başlık düzeyi ilk
  numaralı başlıktan alınır: `# 1.` belge başlığı ya da girişteki `### 1.` listesi maddeleri
  büyütür ve raporda madde numarasını yanlış gösterir. Ayrıntı: `docs/agent_design.md` §4 "RAG".
- **Birimler kapalı listedir (Adım 19e'den sonra kalan).** Modelin `unit` alanında `takvim günü`
  tanınır, `iş günü`, `TRY`, `yüzde` ve tek başına `günlük` tanınmaz (gereksiz yönlendirme).
  Yazıyla yazılmış tutar yalnızca rakamla yazılmış aynı tutarla parantezli tekrar oluşturuyorsa
  ve aynı değeri söylüyorsa okunur (`2.400.000 TL (iki milyon dört yüz bin Türk lirası)`,
  `bin (1.000) TL`); sözcükle yazılmış tek başına tutar ve `bir milyon … Türk lirası (1.200.000
  TL)` yazısı okunmaz, parantezde birimi yazılmamış çelişen değer (`1.200.000 TL (1.300.000)`)
  görülmez. Saat yalnızca saat birimli kuralda okunur; `saatinde` biçimleri, `dakika` ve `sene`
  okunmaz; saat zamanı olabilecek sayı (`08:30`, `08 30`, `08h30`, `9,30 saati`) okunamayan kalır
  (bedeli: `1,50 saat` ve `Madde 4: 30 saat` de kuyruğa gider); `17 saati itibarıyla` 17 saat,
  `8, 30 saat` 30 saat okunur; taslakta "48 (yirmi dört) saat" gibi uyuşmayan tekrar, iki değer
  de kurala uyuyorsa geçer. Bu düzeltmeler gerçek sözleşmeyle değil, bildirimden yeniden kurulan girdilerle
  doğrulandı; model çağıran eval koşulmadı. Ayrıntı: `docs/agent_design.md` §5.
- **Madde numarası başlıktan maskelemeden önce okunur.** `Madde 12345678901` gibi bir başlık sayıyı
  eval satırına maskesiz taşır (veri kümesinde numaralar 1–14).
- **Küçük örneklem.** 35 karar (bir satır 2,9 puan); Groq'ta üç koşu (ikisi aynı gün), Anthropic
  `claude-sonnet-5-5`'te iki koşu (farklı kodla, her biri tek koşu). Genel bir Türkçe kalite
  ölçümü değildir.
- **Sabit eşiği olmayan kurallar** ("sorumluluk sınırı sözleşme bedelini aşamaz"): kod iki değeri
  karşılaştırmaz, yalnızca alıntıyı doğrular; sayısal olmayan kuralda taslak "kod doğrulayamadı"
  etiketi alır. Adım 19c'den beri böyle bir taslak her zaman avukat kuyruğuna girer (sebep
  `proposal_unverifiable`) ve avukat taslağa karar vermeden sözleşmeyi kapatamaz; bedeli, eval'de
  koşu başına bir yönlendirme daha (sözleşme 5 `governing_law`).
- **Taslak denetimi düzenlenen değeri ayırt etmez.** Adım 19c'den beri taslak, okunan her değer
  kurala uyuyor ve okunamayan değer ifadesi yoksa geçer (önceden bütün değerlerin aynı olması
  isteniyordu ve gecikme cezasında günlük %1 + üst sınır %10 gibi uygun taslaklar kalıyordu). Kod
  hangi değerin kuralın düzenlediği değer olduğunu bilmez: üst sınırı silen ya da kuralı değer
  olmayan bir ifadeyle delen taslak ("… ancak 20 günü aşan gecikmede ceza sınırsızdır"), ve tek
  tek kurala uyup toplamda aşan değerler ("%5 … ayrıca %8 ek ceza") geçer ve kuyruğa düşmez.
  Böyle bir taslak avukat kabul etmedikçe değişiklik talebine girmez. Ayrıntı:
  `docs/agent_design.md` §5 "Taslak kontrolü".
- **Audit log dosya düzeyinde korunmaz.** Uygulama üzerinden değiştirilemez (`UPDATE` / `DELETE`'i
  reddeden tetikleyiciler), ama veritabanı dosyasına doğrudan erişen biri değiştirebilir; imza ya
  da hash zinciri yok.
- **Başka bir sağlayıcıda** (ayrıntı: `docs/agent_design.md` §6, `docs/spec.md` §8):
  - Hata sınıflandırmasında bilinen yanlışlar var: OpenAI'de kota bitmesi 429'dur ve hız limiti
    sayılır; Gemini'de faturalama hatası 400'dür ve diğer LLM hatası sayılır.
  - `max_retries=3` `groq`, `openai` ve `anthropic`'te en fazla 4, `google_genai`'de 3 istek
    demektir; zaman aşımı deneme başınadır.
  - Düşünen modellerde düşünme token'ları `LLM_MAX_TOKENS`'ın içindedir; sınır dolarsa yanıt boş
    gelir ve kural `no_finding` (`no_submit`, incelenemedi) olarak avukata gider.
  - OpenAI uyumlu bir adresten akışlı yanıtta token kullanımı eksik gelebilir.
  - Bilinen sağlayıcı tablosunda olmayan, anahtarsız kurulabilen bir sağlayıcıda eksik anahtar
    başlangıçta değil ilk çağrıda görülür (401): inceleme `failed` olur, API 502 döner.
  - Gemini 3.x, OpenAI uyumlu adresle: `langchain-openai` 1.7.4 tool çağrısının düşünce imzasını
    geri göndermez; her kural `no_finding` olur ve avukata gider ([Üçüncü bir sağlayıcıyla
    deneme](#üçüncü-bir-sağlayıcıyla-deneme-gemini)). Kendi entegrasyonu (`google_genai`) imzayı
    taşır.
  - `gemini-3.5-flash-lite` `temperature`'ı yok sayar (entegrasyonun uyarısı); bu modelde
    `LLM_TEMPERATURE` etkisizdir, koşuların tekrarlanabilir olması beklenmez.
  - Anthropic `claude-sonnet-5-5` varsayılan `LLM_TEMPERATURE` (0) ile çalışmaz: her model
    çağrısı istek gönderilmeden yerelde `ValueError` verir ("temperature is not supported for
    claude-sonnet-5-5 at non-default values", `langchain-anthropic`). Sözleşme 4'te görüldü:
    `POST /reviews` 0 sn'de 200, 0 token, 5 kuralın 5'i `not_found` (`no_finding`) ile avukat
    kuyruğunda; audit'te hata tipi `ValueError`, durum kodu yok. Başlangıç kontrolü
    (`check_chat_model`) bunu yakalamaz: hata kurulumda değil çağrıda çıkar. Çözüm
    `LLM_TEMPERATURE=none` (200, 40 sn, 5 / 5 status).

Tam liste: `docs/spec.md` §8 "Bilinen sınırlar".

## Denenmeyenler

- LangSmith'e yükleme gerçek sunucuya karşı (yalnızca sahte anahtar ve yakalanan isteklerle).
- Çıkış kodu 3 (yalnızca sahte modelle). Gerçek limitte çıkış kodu 2 iki kez (holdout koşusu ve
  2026-11-13'teki yarım tam koşu), `--resume` bir kez (holdout) görüldü.
- Ollama ile eval; varsayılan çevrimdışı model `qwen3.5:9b` (çevrimdışı mod yalnızca `qwen3.5:4b`
  ile, tek sözleşmede denendi).
- Taslak yönlendirmesinin başka modelde etkisi; 1 / 11 → 11 / 11 farkının kaynağı.
- Arayüzde karar verilmiş incelemenin görünümü ve çok işaretli metinde tarayıcı süresi; sayfanın
  JS mantığının otomatik testi yok.
- Avukat kararında `edit` yolu gerçek veride (testlerle kapsanıyor).
- Temiz klonda yalnızca şunlar denendi (2026-11-13): model gerektirmeyen bütün adımlar ve
  gerçek modelle tek bir uçtan uca akış (örnek sözleşme: inceleme 91 sn, karar, rapor, audit;
  ≈ 30K token). Çevrimdışı mod ve modelli eval temiz klonda denenmedi; arayüz tarayıcıda
  yalnızca geliştirme sırasında kullanıldı.
- Docker'da LangSmith experiment'ine git bilgisinin (uzak depo adresi, yazar adı ve e-postası)
  gitmediği konteynerde gerçek yüklemeyle doğrulanmadı (`docs/evals.md` §7).
- OpenAI'nin kendisi (anahtar yok). Gemini aşağıdaki iki denemeyle sınırlı (tek sözleşme, tek
  koşu); Anthropic `claude-sonnet-5-5` iki tam eval koşusuyla ölçüldü, başka Anthropic modeli
  denenmedi. Çevrimdışı mod Adım 18c / 18d koduyla çalıştırılmadı (mevcut Ollama testleri
  değiştirilmeden geçiyor); Ollama yolu `langchain-core` 1.8.0'da yalnızca sahte taşıyıcılarla
  karşılaştırıldı.
- Holdout bugünkü kodla yeniden koşulmadı (tek atış kuralı).
- Adım 19c'nin kodu Groq'ta koşulmadı (yalnızca `claude-sonnet-5-5` ile bir koşu).

### Üçüncü bir sağlayıcıyla deneme (Gemini)
İki deneme yapıldı (2026-11-13), ikisi de Google Gemini ücretsiz katman anahtarıyla, tek eval
sözleşmesi (`02_ihlalli`) üzerinde, `data/`'nın bir kopyasında `POST /reviews` ile; her biri tek
koşu. Bunlar ölçüm değildir: Gemini "tek sözleşmede denendi, ölçülmedi".

**1. OpenAI uyumlu adres (Adım 18c, kod `c7fec04`;** `LLM_PROVIDER=openai`,
`LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/`**).**
`gemini-3.5-flash-lite` ilk tool çağrısını yaptı, ama tool sonucunu geri gönderen ikinci istek her
kuralda HTTP 400 aldı ("Function call is missing a thought_signature"): Gemini 3.x tool çağrısının
düşünce imzasının geri gönderilmesini ister, `langchain-openai` 1.7.4 göndermez. `POST /reviews`
7 sn'de 200 döndü, 5 kuralın 5'i `no_finding` ile avukat kuyruğuna gitti; hiçbir şey "uygun"
bildirilmedi. `gemini-2.5-flash-lite` ve `gemini-2.5-flash` her çağrıda 404 döndü: inceleme
`failed`, API 502. Ayrıntı: `docs/plan.md` Adım 18c "Elle deneme".

**2. Kendi entegrasyonu (Adım 18d;** `LLM_PROVIDER=google_genai`,
`LLM_MODEL=gemini-3.5-flash-lite`, `LLM_BASE_URL` boş, `MAX_CONCURRENCY=1`, konteyner içinde**).**
`POST /reviews` 39 sn'de 200 döndü; inceleme `awaiting_lawyer`, 5 bulgu, eval etiketlerine göre
status 5'te 4 doğru; bulguların 3'ü avukat kuyruğuna gitti. Tool döngüsü (OpenAI uyumlu yolu
bozan düşünce imzası) gerçek Gemini API'siyle tamamlandı.

| Kural | Sonuç (beklenen) | Kuyruk |
|---|---|---|
| `termination_notice` | `non_compliant` (`non_compliant`); alıntı var, taslağın kod kontrolü geçti | Hayır |
| `late_penalty_cap` | `non_compliant` (`non_compliant`); taslağın kod kontrolü geçmedi | Evet (`proposal_noncompliant`) |
| `insurance_minimum` | `non_compliant` (`non_compliant`); taslak geçti | Hayır |
| `liability_cap` | `non_compliant` (**beklenen `compliant`**) | Evet (`missing_proposal`, `run_interrupted`) |
| `governing_law` | `not_found` (`not_found`) | Evet (`no_finding`) |

`liability_cap`'in koşusu ücretsiz katman hız limitiyle kesildi (audit'te `GoogleRateLimitError`
429 ile iki `model_call` satırı); yanlış status'un kesintiden mi modelden mi geldiği bilinmiyor.
Entegrasyon her çağrıda "Model 'gemini-3.5-flash-lite' uses fixed sampling defaults; the
sampling parameter(s) temperature will be ignored." uyarısını verdi: bu modelde
`LLM_TEMPERATURE` etkisizdir (`temperature` 0 uygulanmaz), koşuların tekrarlanabilir olması
beklenmez. Bu koşunun token toplamı kaydedilmedi. Ayrıntı: `docs/plan.md` Adım 18d "Elle deneme".

Aynı denemeyi yapmak için `.env`'e (bu sürümle kurulan varsayılan imajda `langchain-google-genai`
vardır; daha önce kurulmuş imajda `docker compose build` gerekir; `LLM_BASE_URL` boş kalır):

```bash
LLM_PROVIDER=google_genai
LLM_MODEL=gemini-3.5-flash-lite
LLM_API_KEY=<Gemini API anahtarı>
```

Ücretsiz katmanda bu koşu `MAX_CONCURRENCY=1` ile bile 429 aldı.

## Daha fazla vaktim olsaydı

- **Embedding ile arama (BM25 + vektör).** İlk eklenecek şey budur; sözleşme 4'teki iki kaçış bu
  sınırdan geliyor (Groq'ta üç kez; model "fesih", "gizlilik" kökünü değiştirerek arıyor, madde
  "Sona Ermesi", "Sır Saklama" diyor). Uzun sözleşmede tek başına yetmez: kural başına tek madde ve
  tek alıntı, ilk 5 madde sınırı, kural başına 8 model çağrısı ve iki başlık kalıbına (`Madde N`,
  `## N.`) uymayan başlıkların paragraflara düşmesi de değişmeli.
- **Ucuz alternatif denendi ve geri alındı (madde listesi, 2026-11-13/14).** Fikir: her kuralın
  ilk `search_contract` sonucu bütün maddelerin ilk satırını da listeler (80 karakter, en fazla 60
  madde), başka sözcüklerle yazılmış madde de görünür. Groq'ta sözleşme 4 tek başına, erken bir
  biçimle (liste her aramada) bir koşu: 5 / 5 status, kaçan iki madde bulundu, 50.854 token. Son
  biçimin Groq'ta tam koşusu hız limitine takıldı ve tamamlanmadı; aynı modelde ölçümü eksik.
  Anthropic `claude-sonnet-5-5` tam eval'de listesiz ve listeli: 35 kuralın 35'inde status, madde,
  taslak sonucu ve kuyruk aynı (tek içerik farkı: sözleşme 4 `payment_term`'e listeli koşuda ek
  `ambiguous_value`); arama çağrısı 89 → 80, girdi token'ı 593.334 → 733.345 (+%24), süre 277 →
  261 sn. İncelemeler bir bedel de kaydetti: bir maddenin ilk satırındaki, taramanın yakalamadığı
  bir talimat her kurala ulaşır. Maddeyi zaten bulan modelde faydası yok, bedeli girdi token'ının
  yaklaşık dörtte biri, Groq'taki tek olumlu gözlem doğrulanmadı: geri alındı. İş
  `exp/clause-outline` dalında duruyor (`docs/plan.md` "Adım 19b"); `main`'de yok.
- **Taslağın maddesini bulgunun maddesiyle eşleştirmek** ve taslak yazımını kodla zorlamak;
  taslak denetiminde kuralın düzenlediği değeri ayırt etmek (bugün okunan her değer ayrı ayrı
  denetlenir; Adım 19c).
- **Gerçek ad tanıma** (etiketsiz gövde adları) ve maskeleyicinin kalan biçimleri. Adım 19c iki
  kural tabanlı yol ekledi (e-posta bağı, etiketsiz imza satırı); gövdede yalın geçen ve hiçbir
  yolun bağlamadığı ad hâlâ maskelenmez.
- **Daha büyük ve bağımsız bir eval seti**, birden çok koşu ve günle; yerel model ile büyük modelin
  aynı sette karşılaştırılması.
- **Üretimde izleme açıkken uygulamanın başlamayı reddetmesi** (bugün koruma işletim kuralıdır).
- **Giriş sistemi ve işlevsel bir avukat arayüzü.** Bugün sayfa yalnızca okur; anahtar ve
  inceleme kimliği elle yazılır, karar API'den verilir. Eklenecekler: kullanıcı adı / parola ile
  giriş ve sunucu taraflı oturum (API anahtarları betikler için kalır), inceleme listesi,
  arayüzden sözleşme yükleme, karar ve taslak düzenleme formu, rapor ve audit görünümü; avukatın
  düzenlediği taslağın da kodla denetlenip sonucun rapora yazılması.
- Audit log için imza ya da hash zinciri; anahtar döndürme.

## Depo haritası

| Yol | İçerik |
|---|---|
| `src/` | API (`api.py`), graph, agent ve 5 tool, doğrulama (`verify.py`), maskeleme ve arama (`prepare/`), arayüz (`static/index.html`) |
| `data/tenants/<tenant>/rules.yaml` | İki şirketin kural setleri (veritabanları çalışma zamanında burada oluşur) |
| `evals/` | Koşucu (`run.py`), metrikler (`metrics.py`), veri kümesi (`dataset/`), holdout (`holdout/`), sonuçlar (`results/`) |
| `tests/` | Unit testler (gerçek LLM ve ağ yok) |
| `docs/CASE.md` | Case'in asıl metni |
| `docs/decisions.md` | Karar dokümanı |
| `docs/spec.md` | Senaryolar, gereksinimler, kapsam dışı, bilinen sınırlar, açık sorular |
| `docs/architecture.md` | Katmanlar, API uçları, roller, veri nereye gidiyor, ADR'ler |
| `docs/agent_design.md` | Graph, tool'lar, prompt, doğrulama sinyalleri, maskeleme, enjeksiyon |
| `docs/tech_stack.md` | Kütüphaneler, modeller, Groq bilgileri, env değişkenleri |
| `docs/evals.md` | Metrikler, eşikler, dataset, sonuçlar |
| `docs/code_standards.md` | Kod kuralları |
| `docs/plan.md` | Uygulama planı ve ilerleme |
