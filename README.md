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
model sağlayıcısı Groq (`openai/gpt-oss-120b`), çevrimdışı mod Ollama'dır (`qwen3.5:9b`); başka bir
sağlayıcı yalnızca env ile seçilir. Kararların gerekçesi: `docs/decisions.md` (karar dokümanı) ve
`docs/architecture.md` §7 (ADR'ler).

## İçindekiler
1. [Kurulum](#kurulum)
2. [Kullanım](#kullanım)
3. [Eval nasıl koşulur](#eval-nasıl-koşulur)
4. [Eval sonuçları](#eval-sonuçları)
5. [Veri nereye gidiyor](#veri-nereye-gidiyor)
6. [Bilinen sınırlar](#bilinen-sınırlar)
7. [Daha fazla vaktim olsaydı](#daha-fazla-vaktim-olsaydı)
8. [Depo haritası](#depo-haritası)

## Kurulum

**Gerekenler**

- Docker ve Docker Compose. GPU gerekmez.
- Bir model sağlayıcısının API anahtarı: varsayılan modda Groq (ücretsiz katman yeterlidir).
- [Kullanım](#kullanım)'daki örnek komutlar için `curl` (Windows 10 ve sonrasında `curl.exe`
  olarak gelir) ve Python 3 (yalnızca JSON dosyası hazırlamak ve okumak için).

**Adımlar**

```bash
git clone <depo adresi>
cd case-uplico
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
# .env içinde GROQ_API_KEY= satırını GROQ_API_KEY=gsk_... olarak doldur (tırnaksız, boşluksuz).
docker compose up
```

- **Zorunlu tek değer `GROQ_API_KEY`'dir** (ya da `LLM_API_KEY`); diğer satırlar olduğu gibi
  kalabilir. İkisi de boşsa `app` tek satırlık bir hatayla (`Yapılandırma hatası: …`) çıkar.
- **`TENANT_API_KEYS`** anahtar → tenant, rol, görünen ad eşlemesidir. `.env.example`'daki dört
  `change-me-…` anahtarı olduğu gibi kabul edilir; kendi makinende denemek için değiştirmeden
  kullanabilirsin (aşağıdaki örnekler bunları kullanır). Bu değerler herkesçe bilinir; başka her
  kullanımda değiştir.
- **Ayakta mı?** İlk seferde imaj kurulur (diskte 829 MB). Hazır olunca `app` logunun son satırı
  `Uvicorn running on http://0.0.0.0:8000` olur; tarayıcıda `http://127.0.0.1:8000` "Sözleşme
  inceleme" sayfasını açar (port yalnızca `127.0.0.1`'e yayınlanır).
- **Durdurmak:** Ctrl+C ya da başka bir terminalde `docker compose down`.
- **Veri nerede:** incelemeler, audit log ve checkpoint'ler `data/tenants/<tenant>/` altındaki
  SQLite dosyalarındadır; klasör kapsayıcıya bağlanır, durdurmak silmez, git'e girmez.

### Çevrimdışı mod (Ollama)
`.env`'de iki değişken değişir; komut yine `docker compose up`:

```bash
LLM_PROVIDER=ollama
COMPOSE_PROFILES=offline
```

Bu profille `ollama` ve `ollama-pull` servisleri de başlar; model iner, `app` indirme bitene kadar
bekler, sonrasında internet gerekmez. `LLM_MODEL` boşsa model `qwen3.5:9b`'dir (~7 GB, tahmin;
yedek `qwen3.5:4b` 3,3 GB). Ollama imajı 3,40 GB iner. Çevrimdışı mod yalnızca `qwen3.5:4b` ile
denendi (ilk `docker compose up` 7 dk 17 sn); `qwen3.5:9b` hiç denenmedi (geliştirme makinesinin
ekran kartı 4 GB). Host'taki Ollama için: `OLLAMA_BASE_URL=http://host.docker.internal:11434`.

### Başka bir sağlayıcı
Sağlayıcı, model, anahtar ve adres yalnızca `.env` ile seçilir; kod değişmez. İmajda
`langchain-openai`, `langchain-anthropic` ve `langchain-google-genai` kuruludur.

| Değişken | Anlamı |
|---|---|
| `LLM_PROVIDER` | LangChain `init_chat_model`'in tanıdığı sağlayıcı adı (`openai`, `anthropic`, `google_genai` …); boşsa `groq` |
| `LLM_MODEL` | Model adı; `groq` ve `ollama` dışında zorunlu |
| `LLM_API_KEY` | Sağlayıcı anahtarı; `GROQ_API_KEY` eskisi gibi çalışır |
| `LLM_BASE_URL` | Verilirse modelin adresi (OpenAI uyumlu servisler için) |
| `LLM_TEMPERATURE` | Boşsa 0; `none` yazılırsa istekte gönderilmez |
| `LLM_MAX_TOKENS` | Çağrı başına çıktı sınırı; boşsa 2048 |

Ölçülen ikinci ayar Anthropic'tir:

```bash
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-5-5
LLM_API_KEY=<Anthropic API anahtarı>
LLM_TEMPERATURE=none
```

`LLM_TEMPERATURE=none` bu modelde zorunludur: varsayılan 0 ile her çağrı yerelde reddedilir,
bütün kurallar "bulunamadı" olarak avukata gider ve uygulama yine de başlar.

Ölçülenler: Groq `openai/gpt-oss-120b` üç, Anthropic `claude-sonnet-5-5` dört tam eval koşusu.
Gemini (`google_genai`) tek sözleşmede denendi, ölçülmedi; OpenAI denenmedi. Ölçülmemiş her
sağlayıcının çalışması beklenir ama garanti değildir. Ayrıntı: `docs/tech_stack.md` "Ortam
değişkenleri".

### Geliştirme komutları
Gereken: [uv](https://docs.astral.sh/uv/) (Python 3.12'yi ve bağımlılıkları ilk `uv run` kurar).
Docker gerekmez.

```bash
uv run pytest                                            # test (gerçek LLM ve ağ yok)
uv run ruff check . && uv run ruff format --check .      # lint + format
uv run mypy src evals                                    # type-check
```

Test takımı 2425 testtir ve geliştirme makinesinde ≈ 5 dk sürer.

## Kullanım

Arayüz (`http://127.0.0.1:8000`) yalnızca okur: inceleme API'den başlatılır, karar API'den
verilir. Uygulama bir terminalde çalışırken aşağıdakileri **ikinci bir terminalde, deponun kök
klasöründe** sırayla çalıştır. Bloklar bash içindir (Linux / macOS / Git Bash; Git Bash'te
`python3` yerine `python` yaz). Windows PowerShell'de üç şey değişir: `curl` yerine `curl.exe`,
`python3` yerine `python`, ve değişken atamaları `$LAWYER_KEY = "change-me-acme-lawyer"` ile
`$REVIEW_ID = python -c "…"` biçiminde yazılır.

**1. İncelemeyi başlat.** İstek inceleme bitene kadar bekler (örnek sözleşme Groq'ta ≈ 90–110 sn).

```bash
LAWYER_KEY=change-me-acme-lawyer
python3 -c "import json; json.dump({'text': open('tests/fixtures/sample_contract.md', encoding='utf-8').read()}, open('body.json', 'w'))"
curl -s -X POST http://127.0.0.1:8000/reviews -H "X-API-Key: $LAWYER_KEY" -H "Content-Type: application/json" --data-binary "@body.json" -o review.json -w "%{http_code}\n"
REVIEW_ID=$(python3 -c "import json; print(json.load(open('review.json', encoding='utf-8'))['review_id'])")
echo "$REVIEW_ID"
```

Beklenen: `200` ve bir inceleme kimliği. `401` anahtar `.env`'dekiyle aynı değil; `502` model
sağlayıcısının anahtarı ya da ayarı hatalı; `000` uygulama çalışmıyor. Groq hız limiti doluysa
yine `200` gelir ama kartlarda "Sonuç üretilemedi" görünür: bekleyip yeniden çalıştır.

**2. Arayüzde gör.** `http://127.0.0.1:8000` aç; "API anahtarı" alanına `change-me-acme-lawyer`,
"İnceleme kimliği" alanına yukarıdaki kimliği yaz, **Getir**'e bas. Solda sözleşme metni, renkli
alıntılar ve enjeksiyon işaretleri; sağda her kuralın kartı (alıntı, gerekçe, avukata gitme
sebepleri, varsa değişiklik taslağı) görünür. `change-me-acme-viewer` anahtarıyla kişisel veriler
`[KISI_1]` gibi yer tutucularla görünür; başka tenant'ın anahtarı `404` alır.

**3. Avukat kararını ver.** İlk komut `review.json`'dan geçerli bir karar gövdesi kurar (kuyruktaki
her bulguyu ve taslağını kabul eder, son karar `request_changes`); göndermeden önce
`decision.json`'u düzenleyebilirsin. Karar bir kez verilir; ikinci istek `409` alır.

```bash
python3 -c "import json; r=json.load(open('review.json', encoding='utf-8')); fs=[dict(rule_id=f['rule_id'], action='accept', **({'proposal_action': 'accept'} if f['proposal'] else {})) for f in r['findings'] if f['needs_review'] or f['proposal']]; json.dump({'findings': fs, 'contract_decision': 'request_changes'}, open('decision.json', 'w'), indent=1)"
curl -s -X POST "http://127.0.0.1:8000/reviews/$REVIEW_ID/decision" -H "X-API-Key: $LAWYER_KEY" -H "Content-Type: application/json" --data-binary "@decision.json" -o decided.json -w "%{http_code}\n"
```

Karar alanları: kuyruktaki her bulgu için `action` (`accept` ya da `correct` + `status`); taslağı
varsa `proposal_action` (`accept` / `edit` + `edited_text` / `reject`); son karar `approve` ya da
`request_changes`. Eksik ya da geçersiz gövde `422` alır ve hiçbir şeyi değiştirmez.

**4. Raporu ve audit kaydını oku.**

```bash
curl -s "http://127.0.0.1:8000/reviews/$REVIEW_ID/report" -H "X-API-Key: $LAWYER_KEY" -o report.json -w "%{http_code}\n"
curl -s "http://127.0.0.1:8000/reviews/$REVIEW_ID/audit" -H "X-API-Key: $LAWYER_KEY" -o audit.json -w "%{http_code}\n"
```

Bu komutların yazdığı JSON dosyalarında maskesiz sözleşme metni olabilir; işin bitince sil.

**Kendi sözleşmen ve kuralların.** 1. adımdaki dosya yolunu değiştir: metin UTF-8 düz metin ya da
Markdown olmalı. Maddeler `Madde N` başlıklarıyla, yoksa numaralı Markdown başlıklarıyla
(`## 4. Fesih`), o da yoksa hemen altında kendi alt maddesi (`7.1`) olan büyük harfli düz
başlıklarla (`7. SORUMLULUK`) ayrılır; başka metin paragraflara bölünür. Kurallar
`data/tenants/<tenant>/rules.yaml`'dadır; tenant'ı anahtar seçer (`change-me-globex-lawyer`).

### Örnek şablon: en iyi çalışan kural ve sözleşme biçimi

Kural (`data/tenants/<tenant>/rules.yaml`): tek konu, tek sabit eşik.

```yaml
- id: termination_notice
  text: "Fesih bildirim süresi en az 30 gün olmalıdır."
  severity: critical                        # ya da yazılmaz (normal)
  check: {op: ">=", value: 30, unit: gün}   # birimler: gün, hafta, ay, yıl, saat, %, TL
```

Sözleşme (UTF-8 düz metin ya da Markdown):

```markdown
# TEDARİK SÖZLEŞMESİ

## Madde 1 - Fesih
Taraflardan her biri, 30 (otuz) gün önceden yazılı bildirimde bulunarak Sözleşmeyi feshedebilir.

## Madde 2 - Ödeme
Faturalar, tebliğ tarihinden itibaren 45 gün içinde ödenir.

İmzalar
Adı Soyadı: Ahmet Yılmaz
TCKN: 12345678950
```

Neden bu biçim:

- **`Madde N` başlığı ve her maddede tek konu:** madde numarası rapora doğru geçer, kod kuralı
  çiğneyen değeri aynı maddede arar.
- **Başlıkta ve metinde kuralın sözcükleri ("fesih", "bildirim"):** arama anahtar sözcükle çalışır.
- **Değer rakamla ve birimiyle ("30 gün", "%10", "1.000.000 TL"):** kod alıntıdaki sayıyı
  `check` ile kendisi karşılaştırır. `check`'siz (metin) kuralda yalnızca alıntı doğrulanır.
- **Kişisel veri etiketiyle ("Adı Soyadı:", "TCKN:") ve imza bloğunda:** maskeleme garantisi
  etiketli değerlerde en güçlüdür.

### Roller ve uçlar

Her istek `X-API-Key` başlığı taşır. Tenant ve rol **yalnızca** anahtardan çözülür; istekteki
hiçbir alandan okunmaz. `lawyer` inceleme başlatır, karar verir, maskesiz metni görür; `viewer`
yalnızca okur ve maskeli görür.

| Uç | İş | Rol |
|---|---|---|
| `POST /reviews` | Sözleşme metnini (`{"text": "…"}`) alır, inceler, bulguları döner | `lawyer` |
| `POST /reviews/{id}/decision` | Bulgu ve taslak kararları + son karar, tek istekte | `lawyer` |
| `GET /reviews/{id}` | Durum, maddeler, enjeksiyon işaretleri, bulgular, taslaklar | ikisi |
| `GET /reviews/{id}/report` | Son karardan sonra JSON rapor (önce 409) | ikisi |
| `GET /reviews/{id}/audit` | İncelemenin audit kayıtları (iki rolde de maskeli) | ikisi |
| `GET /` | Bulguları sözleşme metni üzerinde gösteren sayfa | — |

Ayrıntı: `docs/architecture.md` §3.

## Eval nasıl koşulur

7 sentetik sözleşme (`evals/dataset/`), 35 kural kararı: 20 uygun, 12 uygun değil, 3 bulunamadı.
Metrikler etiketlerden **kodla** hesaplanır; LLM-as-judge yoktur. "Önce" ve "sonra" aynı model
çıktısından hesaplanır: model bir kez çalışır, doğrulama (`verify`) aynı ham bulgular üzerinde
kapalı ve açık iki kez koşar.

```bash
docker compose run --rm app python -m evals.run                               # tam koşu
docker compose run --rm -e MAX_CONCURRENCY=1 app python -m evals.run --limit 3   # ücretsiz Groq anahtarıyla
docker compose run --rm app python -m evals.run --prepare-only                # model çağırmaz: yalnızca PII ve enjeksiyon taraması
```

- Tam koşu Groq'ta ≈ 206K–246K token ve 10–14 dk, Sonnet'te ≈ 620K–640K token ve 4–5 dk tutar.
- **Ücretsiz Groq katmanında tam koşu hız limitine takılabilir** (aynı gün ≈ 365K–510K token'dan
  sonra görüldü; hangi sınırın devreye girdiği bilinmiyor). Limitte koşucu çıkış kodu 2 ile durur;
  aynı komut `--resume` ile kaldığı yerden devam eder, `--fresh` baştan koşar.
- Çıkış kodları: `0` sert eşikler tuttu · `1` yapılandırma ya da koşu hatası · `2` hız limiti ·
  `3` bir sert eşik tutmadı.
- `src/` değiştiyse önce `docker compose build app`.
- Sonuç dosyaları `evals/results/` altındadır ve metin tutmaz. Diğer seçenekler:
  `uv run python -m evals.run --help`. Tanımlar ve gerekçeler: `docs/evals.md`.

## Eval sonuçları

Bütün sert eşikler yedi tam koşunun yedisinde de tuttu; eşikler sonuca göre değiştirilmedi.
Örneklem küçüktür (35 karar; bir satır 2,9 puan) ve her tablo tek koşudur. Ayrıntı, satır satır
dökümler ve sonuç dosyalarının adları: `docs/evals.md` §4–§5.

### Varsayılan model: Groq `openai/gpt-oss-120b`

2. koşu (2026-11-12, kod `4ccc03d`), doğrulama katmanı kapalı → açık:

| Metrik | Önce | Sonra |
|---|---|---|
| Güvensiz hata (yanlış sonuç, avukata gitmeden) | 2 / 35 | 0 / 35 |
| Geçersiz alıntıyla kabul edilen bulgu | 0 | 0 |
| Enjeksiyon: avukata gitmeyen yanlış "uygun" | 0 | 0 |
| Avukata yönlendirme | 0 / 35 | 12 / 35 |

**Bu tablo ne gösteriyor.** 2 → 0 farkının tamamı sözleşme 4'teki iki yanlış "bulunamadı"dan
geliyor: arama, kuralın sözcüklerini kullanmayan iki maddeyi ("Sır Saklama", "Sözleşmenin Sona
Ermesi") bulamadı. Doğrulama açıkken her "bulunamadı" avukata gittiği için bu iki satır kuyruğa
düşüyor. Model bu koşuda alıntıda ve sayıda hata yapmadı; yani tablo alıntı / sayı kontrolünün
değerini değil, bedelini gösteriyor (35 kararın 12'si avukata gidiyor). Kuyruktaki 12 satırın 8'i
tasarım gereğidir: 3 beklenen "bulunamadı" ve enjeksiyon işareti konan sözleşmenin 5 kuralı.

| Metrik ("sonra") | 2. koşu | 3. koşu (2026-11-13, `51d9c44`) | Eşik |
|---|---|---|---|
| Güvensiz hata | 0 / 35 | 0 / 35 | ≤ %5 |
| Kaçırılan uygunsuzluk | 1 / 12 | 1 / 12 | ≤ %20 |
| Yanlış bulgu | 0 / 11 | 0 / 11 | ≤ %20 |
| Etiket doğruluğu | 33 / 35 | 33 / 35 | — |
| Arama isabeti | 30 / 32 | 30 / 32 | — |
| Alıntısı maddede birebir geçen bulgu | 30 / 30 | 30 / 30 | — |
| Taslak oranı / kod kontrolünden geçen taslak | 11 / 11 · 10 / 10 | 11 / 11 · 9 / 10 | — |
| Avukata yönlendirme | 12 / 35 | 14 / 35 | — |
| Enjeksiyon: taramanın yakalaması / yanlış sonuç | evet / 0 | evet / 0 | evet / 0 |
| PII sızıntısı (garanti edilen değerler) | 0 / 52 | 0 / 52 | 0 |
| Garanti edilmeyen PII sızıntısı | 2 / 5 | 2 / 5 | — |
| Token / süre / maliyet | 241.576 · 813 sn · $0,041 | 246.114 · 727 sn · $0,042 | — |

- İki yanlış satır üç koşuda da aynıdır (sözleşme 4, arama kaçışı); ikisi de avukata gitti.
- 1. koşu iki hata gösterdi ve ikisi düzeltildi: `verify` cümle sonunda biten doğru alıntıyı
  reddediyordu; model "uygun değil" bulgudan sonra taslak yazmıyordu (taslak oranı 1 / 11 →
  11 / 11).
- "PII sızıntısı 0 / 52" yapı gereğidir (maskeleme deterministiktir); maskeleyicinin kapsamını
  garanti edilmeyen değerler gösterir.
- Groq, Adım 19c–19f'nin koduyla koşulmadı.

### İkinci model: Anthropic `claude-sonnet-5-5`

Başka modeldir; Groq sayılarıyla karşılaştırılmaz. Dört koşu; ilk üçü aşağıdadır, her biri bir öncekinin gösterdiği
hatalar düzeltildikten sonra (`LLM_TEMPERATURE=none`, `MAX_CONCURRENCY=1`):

| Metrik ("sonra") | 1. koşu (`a9ad2ed`) | Adım 19c sonrası (`7f7cbf9`) | Adım 19f sonrası (`c3f3f69`) |
|---|---|---|---|
| Güvensiz hata | 0 / 35 | 0 / 35 | 0 / 35 |
| Etiket doğruluğu | 35 / 35 | 35 / 35 | 35 / 35 |
| Arama isabeti | 32 / 32 | 32 / 32 | 32 / 32 |
| Alıntısı maddede birebir geçen bulgu | 32 / 32 | 32 / 32 | 32 / 32 |
| Kod kontrolünden geçen taslak | 8 / 11 | 11 / 11 | 11 / 11 |
| Avukata yönlendirme (önce → sonra) | 3 → 13 / 35 | 2 → 11 / 35 | 3 → 11 / 35 |
| Garanti edilmeyen PII sızıntısı | 2 / 5 | 0 / 5 | 0 / 5 |
| Token / süre | 617.108 · 277 sn | 630.660 · 253 sn | 641.294 · 269 sn |

- Sonnet, Groq'un kaçırdığı iki maddeyi buldu. Dört koşuda da doğrulamanın yakaladığı yanlış sonuç
  yok; görünen yalnızca bedeli (kuyruktaki her satırda status doğru).
- 1. koşu üç hata gösterdi, Adım 19c düzeltti: uygun taslakların kod kontrolünden kalması,
  maskelenmeyen iki ad, kodun denetleyemediği taslağın avukata gitmemesi.
- Son koşu önceki koşuyla satır satır karşılaştırıldı: 35 satırın 34'ünde avukata gitme sebepleri
  aynı; tek fark modelin bir satırı ayrıca kendisinin devretmesi.
- 4. koşu (2026-11-14, kod `85362c7`: "bir önceki yıl" düzeltmesinden sonra) üçüncüyle satır satır
  aynıdır: 35 satırın 35'inde sonuç, madde, avukata gitme sebepleri ve taslak durumu değişmedi
  (639.873 token, 283 sn). Düzeltmenin eskiyi bozmadığını gösterir; veri kümesinde bu ifade
  yalnızca yüzdeyle denetlenen fiyat maddelerinde geçtiği için düzeltmenin kendisini ölçmez.

### Görülmemiş sözleşmeler

- **Holdout (tek atış).** Prompt ve `verify` 7 sözleşmeye bakılarak ayarlandığı için, projeyi
  görmeyen ayrı bir oturuma 2 sözleşme ve beklenen sonuçları yazdırıldı (`evals/holdout/`). Groq
  ile bir kez koşuldu, sonuca bakılarak hiçbir şey değiştirilmedi: 10 / 10 karar doğru, güvensiz
  hata 0, 4'ü avukata gitti. Bir oran değil, bir yoklamadır.
- **Dışarıdan verilen iki sözleşme** (depoda değildir). Birincisi (9 kural; Sonnet ve yerel
  `qwen3.5:4b`): iki modelde de 9 / 9 karar doğru; 4b'nin 8 alıntısının 7'si maddede birebir
  geçmediği için kod tarafından reddedildi ve avukata gitti. İkincisi (10 kural; Sonnet): 9 / 10,
  yanlış "uygun" yok, enjeksiyona uyulmadı. Bu iki sözleşme dokuz kod sınırı gösterdi (Adım 19e ve
  19f); aralarında başlık biçimleri, `saat` birimi, yazıyla tekrarlanan tutar ve yüzde, sağlama
  hanesi tutmayan etiketli TCKN, unvandan sonraki adlar ve alt maddeyi düşüren taslak var. Hepsi
  düzeltildi ve testle sabitlendi. İkinci sözleşme düzeltmelerden sonra Sonnet ile bir kez daha
  koşuldu: maskelenmeyen kişisel veri 6 / 6 → 0 / 6, maddeler kodun kendisiyle numaralandı, beş
  taslak alt maddelerini korudu; karar yine 9 / 10. Koşu bir sınır daha gösterdi:
  taslak artık maddenin tamamını içerdiği için, başka bir alt maddedeki "bir önceki yılın" ifadesi
  okunamayan değer sayılıyor ve uygun taslak kod kontrolünden kalıp avukata gidiyordu. Bu ifade
  düzeltildi (bu sözleşme o kodla yeniden koşulmadı); aynı türden başka ifadeler ("bir sonraki gün") bilerek
  avukata gitmeye devam ediyor. Birinci sözleşme yeniden koşulmadı.

## Veri nereye gidiyor

Maskeleme graph'tan önce yapılır; model, indeks, checkpoint, audit log ve trace yalnızca maskeli
metni görür. Kişi adları, TCKN, IBAN, telefon ve e-posta yer tutucuyla değiştirilir; eşleme
makinede, tenant'ın veritabanında kalır ve maske yalnızca `lawyer` yanıtında açılır. Tutarlar,
süreler, tarihler ve şirket adları maskelenmez (kurallar bunlara ihtiyaç duyar).

- **Varsayılan mod:** maskeli madde metni, kural metni ve agent mesajları Groq'a gider.
  Maskeleyicinin kaçırdığı bir kimlik bilgisi de gidebilir.
- **Başka bir sağlayıcı:** aynı veri seçilen sağlayıcıya gider; saklama ve kullanım koşulları
  kullanıcının sorumluluğundadır.
- **Çevrimdışı mod:** model yereldir; veri makineden çıkmaz.
- **LangSmith:** açılırsa gerçek sözleşmelerin maskeli trace'i dışarı gider.

Tam tablo: `docs/architecture.md` §6.

## Bilinen sınırlar

En önemlileri; tam liste `docs/spec.md` §8.

- **Kanıtlı ama yanlış "uygun".** Model alakasız ama sözleşmede gerçekten geçen bir alıntıyla
  "uygun" derse ve kuralda sabit eşik (`check`) yoksa bulgu avukatsız geçer. Kod alıntının
  varlığını doğrular, konuyla ilgisini doğrulayamaz.
- **Arama yalnızca BM25'tir.** Kuralın sözcüklerini kullanmayan madde bulunamayabilir (Groq'ta
  arama isabeti 30 / 32); o kural "bulunamadı" olarak avukata gider.
- **Ad maskeleme en iyi çabadır.** Ad; etiketten, imza satırından, e-posta bağından, listedeki bir
  unvandan sonra ya da etiketli kimlik numarasından hemen önce geçiyorsa bulunur. Listede olmayan
  bir unvandan sonra ya da gövdede yalın geçen ad modele maskesiz gider.
- **Madde bölme üç başlık biçimini tanır.** Roma rakamlı, küçük harfli ya da altında kendi alt
  maddesi olmayan düz başlıklı sözleşme paragraflara bölünür; o zaman kod, komşu paragraftaki
  kuralı çiğneyen değeri göremez.
- **Taslak denetimi sınırlıdır.** Kod taslaktaki her değeri kurala karşı ayrı ayrı denetler; hangi
  değerin düzenlenen değer olduğunu bilmez. Alt madde denetimi (`proposal_partial`) numaraya
  bakar: eksik alt maddeyi cümle başında anan taslak tam sayılır. Sayısal olmayan kuralda taslak
  denetlenemez ve her zaman avukata gider.
- **Taslak yazımı kodla zorlanmıyor.** Model yazmazsa bulgu `missing_proposal` ile avukata gider.
- **Audit log dosya düzeyinde korunmaz.** Uygulama üzerinden değiştirilemez (tetikleyiciler), ama
  veritabanı dosyasına doğrudan erişen biri değiştirebilir; imza ya da hash zinciri yok.
- **Küçük örneklem.** 35 karar, iki model, sentetik sözleşmeler; genel bir Türkçe kalite ölçümü
  değildir.
- **Denenmeyenler.** LangSmith'e gerçek yükleme; Ollama ile tam eval ve varsayılan `qwen3.5:9b`;
  OpenAI; arayüzün otomatik testi.

## Daha fazla vaktim olsaydı

Benim için bir projeyi ilk yaparken önemli olan doğruluk (accuracy). Bu yüzden son koşuları Sonnet
ile yaptım. Daha fazla zamanım olsaydı daha küçük bir model ve prompt caching gibi yöntemlerle
maliyet düşürülebilirdi, ama projenin ilk sürümünde doğruluğu hedefledim.

- **Embedding ile arama (BM25 + vektör).** İlk eklenecek şey budur: ölçülen tek yanlış sonuç
  (sözleşme 4'teki iki kaçış) bu sınırdan geliyor. Ucuz bir alternatif denendi ve geri alındı:
  ilk arama sonucuna bütün maddelerin ilk satırını eklemek. Sonnet'te hiçbir kararı değiştirmedi,
  girdi token'ını %24 artırdı ve bir maddenin ilk satırındaki talimatı her kurala taşıdı
  (`exp/clause-outline` dalı).
- **Gerçek ad tanıma.** Bugünkü ad keşfi kural tabanlıdır; gövdede yalın geçen adı bulamaz.
  Maskeleme sistemin en katı kuralı olduğu için yerel bir NER modeli sıradaki adımdır.
- **Taslak denetimini güçlendirmek.** Kuralın düzenlediği değeri ayırt etmek, taslağı bulgunun
  maddesiyle eşleştirmek, taslağı özgün maddeyle karşılaştırıp neyin değiştiğini avukata
  göstermek ve avukatın düzenlediği taslağı da kodla denetlemek.
- **Daha büyük ve bağımsız bir eval seti.** Gerçek sözleşmeler, birden çok koşu ve gün; yerel
  model ile büyük modelin aynı sette karşılaştırılması. Dışarıdan gelen iki sözleşmenin dokuz
  sınır göstermesi, sentetik setin kodun gerçek biçimlerle karşılaşmasını ölçmediğini gösterdi.
- **Madde bölmeyi biçime bağlı olmaktan çıkarmak.** Başlık kalıpları yerine belge yapısını okuyan
  bir bölücü; PDF ve Word girdisi.
- **Maskelemeden sonra sözleşmeyi bir LLM çağrısıyla işaretlemek.** Maskelemeden sonra ayrı bir
  LLM çağrısıyla sözleşmeyi kodun daha iyi işleyebileceği bir biçime getirmek (madde başlıklarını
  ve numaralarını işaretlemek gibi) düşündüğüm bir adımdı. Ancak bu yalnızca zaman meselesi
  değil, ölçümle verilecek bir karar: ek çağrının maliyeti ve süresi, azalttığı gereksiz avukat
  yönlendirmesiyle karşılaştırılmalı. Bu adımda model metni yeniden yazmamalı, yalnızca
  işaretlemeli; alıntı doğrulaması her zaman özgün metne karşı yapılmalı.
- **Giriş sistemi ve işlevsel bir avukat arayüzü.** Bugün sayfa yalnızca okur. Eklenecekler:
  kullanıcı girişi ve oturum, inceleme listesi, arayüzden yükleme, karar ve taslak düzenleme
  formu, rapor ve audit görünümü.
- **Audit log için imza ya da hash zinciri**, anahtar döndürme ve izleme açıkken üretimde
  başlamayı reddetme.

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
