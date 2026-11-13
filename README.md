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
model sağlayıcısı Groq (`openai/gpt-oss-120b`), çevrimdışı mod Ollama'dır (`qwen3.5:9b`).
Kararların gerekçesi: `docs/decisions.md` (karar dokümanı) ve `docs/architecture.md` §7 (ADR'ler).

## İçindekiler
1. [Kurulum ve çalıştırma](#kurulum-ve-çalıştırma)
2. [Kullanım](#kullanım)
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

```bash
cp .env.example .env
# .env içinde: GROQ_API_KEY'i yaz; TENANT_API_KEYS'teki dört "change-me-…" anahtarını
# kendi değerlerinle değiştir (anahtar → tenant, rol, görünen ad).
docker compose up
```

Uygulama `http://127.0.0.1:8000` adresinde açılır (port yalnızca `127.0.0.1`'e yayınlanır).
`GROQ_API_KEY` boşsa `app` tek satırlık bir hatayla çıkar ve yeniden başlamaz.

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

### İlk çalıştırmada inen toplam
Değerler Adım 12'de (2026-11-12) ölçüldü; ölçülmeyen ayrıca belirtildi (`docs/spec.md` §10
"Adım 12 gözlemleri").

| Mod | Ne iner / kurulur | Boyut |
|---|---|---|
| Varsayılan (Groq) | Uygulama imajı (temel imaj + derleme sırasında inen paketler) | Diskte 611 MB, sıkıştırılmış 140 MB (ölçüldü) |
| Çevrimdışı (Ollama) | Yukarıdaki imaj + `ollama/ollama:0.40.16` | 3,40 GB indirme, diskte 8,52 GB (ölçüldü) |
| | + sohbet modeli `qwen3.5:4b` (yedek) | 3,3 GB (ölçüldü) |
| | + sohbet modeli `qwen3.5:9b` (varsayılan) | ~7 GB (**tahmin, ölçülmedi**) |

Çevrimdışı modda ilk `docker compose up` `qwen3.5:4b` ile 7 dk 17 sn sürdü. Varsayılan
`qwen3.5:9b` hiç denenmedi (geliştirme makinesinin ekran kartı 4 GB).

### Geliştirme komutları
Gereken: [uv](https://docs.astral.sh/uv/) (Python 3.12'yi ve bağımlılıkları ilk `uv run`
kendisi kurar). Docker gerekmez.

```bash
uv run pytest                                            # test (gerçek LLM ve ağ yok)
uv run ruff check . && uv run ruff format --check .      # lint + format
uv run mypy src evals                                    # type-check
```

## Kullanım

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

Ayrıntı: `docs/architecture.md` §3. Aşağıdaki örneklerde `$LAWYER_KEY`, `.env`'deki
`TENANT_API_KEYS` içinde `acme` / `lawyer` kaydına verdiğin anahtardır.

**1. İnceleme başlat.** İstek inceleme bitene kadar bekler (örnek sözleşme Groq'ta
`MAX_CONCURRENCY=1` ile 112 sn sürdü). Windows'ta `python3` yerine `python` yaz.

```bash
python3 -c 'import json, sys
print(json.dumps({"text": open(sys.argv[1], encoding="utf-8").read()}))' \
  tests/fixtures/sample_contract.md > body.json
curl -s -X POST http://127.0.0.1:8000/reviews \
  -H "X-API-Key: $LAWYER_KEY" -H "Content-Type: application/json" --data-binary @body.json
```

Yanıtta `review_id`, `status` (`awaiting_lawyer`) ve her kural için bir bulgu vardır: `status`,
`quote`, `clause_id`, `proposal` (taslak ve `code_check`), `needs_review` ve `review_reasons`.
Örnek sözleşmede enjeksiyon kalıpları olduğu için bütün bulgular avukat kuyruğuna düşer.

**2. Avukat kararı.** Kuyruktaki (`needs_review: true`) her bulgu için `action` (`accept` ya da
`correct` + `status`), taslağı olan kuyruktaki her bulgu için `proposal_action` (`accept` / `edit`
+ `edited_text` / `reject`) zorunludur; son karar `approve` ya da `request_changes`'tir. Eksik ya
da geçersiz gövde 422 alır ve hiçbir şeyi değiştirmez.

```bash
curl -s -X POST http://127.0.0.1:8000/reviews/$REVIEW_ID/decision \
  -H "X-API-Key: $LAWYER_KEY" -H "Content-Type: application/json" \
  -d '{"findings": [{"rule_id": "termination_notice", "action": "accept",
        "proposal_action": "accept"}], "contract_decision": "request_changes"}'
```

(Bu gövde yalnızca biçimi gösterir: kuyruktaki her bulgu listede olmalı; taslağı olmayan bulguda
`proposal_action` 422'dir.)

**3. Rapor ve audit.**
```bash
curl -s http://127.0.0.1:8000/reviews/$REVIEW_ID/report -H "X-API-Key: $LAWYER_KEY"
curl -s http://127.0.0.1:8000/reviews/$REVIEW_ID/audit  -H "X-API-Key: $LAWYER_KEY"
```

Aynı okumalar `viewer` anahtarıyla maskeli döner; başka tenant'ın anahtarıyla 404 döner.

**Arayüz:** `http://127.0.0.1:8000/` sayfasına anahtar ve inceleme kimliği girilir; sayfa yalnızca
`GET /reviews/{id}` çağırır. `lawyer` anahtarıyla asıl metin üzerinde maddeler, alıntılar ve
enjeksiyon işaretleri vurgulanır; `viewer` anahtarıyla maskeli maddeler gösterilir. İnceleme
başlatma ve karar sayfada yoktur.

## Eval nasıl koşulur

7 sentetik sözleşme (`evals/dataset/`), 35 kural kararı: 20 uygun, 12 uygun değil, 3 bulunamadı.
Metrikler etiketlerden **kodla** hesaplanır; LLM-as-judge yoktur. Tanımlar ve gerekçeleri:
`docs/evals.md` §4.

Tek komut; önce/sonra tablosunu basar ve sonucu `evals/results/` altına yazar:

```bash
docker compose run --rm app python -m evals.run
```

"Önce" ve "sonra" aynı model çıktısından hesaplanır: model sözleşme başına bir kez çalışır,
doğrulama (`verify`) aynı ham bulgular üzerinde kapalı ve açık iki kez koşar. Tam koşu
2026-11-12'de 206.387 ve 241.576 token tuttu (576 ve 813 sn).

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
- `src/` değiştiyse imaj yeniden kurulur: `docker compose build app` (imaj `src/`'yi kopyalar).
- `LANGSMITH_API_KEY` verilirse tam koşu dataset'i ve `before` / `after` experiment'lerini
  LangSmith'e yükler (sözleşme metni, alıntı ve PII gitmez; `docs/evals.md` §7). Bu yol gerçek
  sunucuya karşı **denenmedi**; iki koşu da `langsmith: "local"` ile yapıldı.

## Eval sonuçları

Groq `openai/gpt-oss-120b`, Docker, `MAX_CONCURRENCY=1`, 7 / 7 sözleşme, 35 karar, 2026-11-12.
Aşağıdaki değerler 2. koşudandır: `evals/results/20261112-200616_groq_openai-gpt-oss-120b.json`
(kod `4ccc03d`). Ayrıntı: `docs/evals.md` §4–§5.

Bu koşudan sonra üç düzeltme yapıldı (2026-11-13: `verify`'da alıntı dışındaki değerlerin
okunması, alan kodu parantezli telefonun maskelenmesi, BOM'dan sonraki ilk başlık). Modelli eval
bu kodla **yeniden koşulmadı**; aşağıdaki sayılar düzeltmelerden önceki koda aittir. Modelsiz
ölçüm (`--prepare-only`) düzeltmelerden sonra da aynıdır: garanti edilen PII sızıntısı 0 / 52,
garanti edilmeyen 2 / 5.

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
| Alıntı geçerliliği (avukata gitmeyen alıntılı kararlar) | 23 / 23 | %100 |
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

Bütün sert eşikler iki koşuda da tuttu; eşikler sonuca göre değiştirilmedi (küçük örneklem: bir
satır 2,9 puan). Kaçırılan tek uygunsuzluk sözleşme 4 `termination_notice`'tir: arama maddeyi
bulamadı, satır "bulunamadı" olarak avukata gitti. Sözleşme 7'deki (tarama kalıbında olmayan)
gizli talimatı tarama yakalamadı, model de kanmadı.

**PII.** Maskelenmesi garanti edilen değerlerde sızıntı 0 / 52; bu sayı yapı gereği 0'dır
(maskeleme deterministiktir, sayı yalnızca maskelemeyi atlayan bir kod yolunu yakalar).
Maskeleyicinin kapsamını ikinci sayı gösterir: garanti edilmeyen 5 değerden 2'si modele gitti;
ikisi de kişi adıdır (sözleşme gövdesindeki ad ve etiketsiz imza satırındaki ad).

### İkincil tablo (a): düzeltmelerin etkisi, 1. koşu → 2. koşu
1. koşu (`evals/results/20261112-173317_groq_openai-gpt-oss-120b.json`, kod `9b6ca9a`) iki hata
gösterdi; ikisi kendi commit'iyle düzeltildi: `2a6fbe3` (`verify`'da cümle sınırını aşan birim,
cümle sonunda biten doğru alıntıyı reddediyordu) ve `4ccc03d` ("uygun değil" bulgudan sonra
`submit_finding`'in yanıtı modelden taslak ister).

| Metrik ("sonra") | 1. koşu | 2. koşu |
|---|---|---|
| Taslak oranı | 1 / 11 | 11 / 11 |
| Avukata yönlendirme | 19 / 35 | 12 / 35 |
| Alıntı geçerliliği | 16 / 16 | 23 / 23 |
| Gereksiz yönlendirme | 17 / 19 | 10 / 12 |
| Token | 206.387 | 241.576 |

Neyi göstermez: iki koşu aynı günün, tek modelin iki koşusudur. 35 kararın `status`'ü iki koşuda
aynıdır; fark taslaklardan ve kuyruktan gelir (1. koşuda kuyruğun 10 satırı taslaksız "uygun
değil", `missing_proposal`). Alıntı geçerliliğinin paydası kuyruk küçüldüğü için büyüdü (metrik
yalnızca avukata gitmeyen alıntılı kararları sayar). Taslak farkının ne kadarının tool yanıtından,
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

Diğer değerler ("sonra"): doğru madde 8 / 8, arama isabeti 8 / 8, alıntı geçerliliği 6 / 6, taslak
oranı 4 / 4 (dördü de kod kontrolünden geçti), kaçırılan uygunsuzluk 0 / 4, yanlış bulgu 0 / 4,
garanti edilen PII 0 / 18, garanti edilmeyen 4 / 4. Token 72.058 + 3.508 = 75.566; 364,2 sn;
$0,0129.

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
  gidebilir.
- **Çevrimdışı mod:** model yereldir; veri makineden çıkmaz.
- **LangSmith:** çevrimdışı kurulumda LangSmith değişkenleri verilmez; uygulamada açılırsa gerçek
  sözleşmelerin maskeli trace'i dışarı gider.

Tam tablo: `docs/architecture.md` §6 "Veri nereye gidiyor".

## Bilinen sınırlar

- **Kanıtlı ama yanlış "uygun".** Model alakasız ama sözleşmede gerçekten geçen bir alıntıyla
  "uygun" derse ve kuralda sabit eşik (`check`) yoksa bulgu avukatsız geçer. Kod alıntının varlığını
  doğrular, konuyla ilgisini doğrulayamaz. Her "bulunamadı"yı kuyruğa almak bunu kapatmaz.
- **Arama yalnızca BM25'tir.** Arama isabeti 30 / 32: sözleşme 4'te madde 9 ("Sır Saklama") ve
  madde 11 ("Sözleşmenin Sona Ermesi … bir ay önceden … haber vererek") kuralın sözcüklerini
  kullanmıyor ve bulunamadı; iki kural "bulunamadı" döndü ve avukata gitti. Dataset, tokenizer ve
  prompt buna göre ayarlanmadı (madde 11 için bu karar sonuçlar görüldükten sonra alındı;
  `docs/evals.md` §3 "Sonuç").
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
- **Maskelenmeyen kimlik bilgileri.** Gövdede geçen ad, etiketsiz imza satırındaki ad, satıra
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
- **Madde numarası başlıktan maskelemeden önce okunur.** `Madde 12345678901` gibi bir başlık sayıyı
  eval satırına maskesiz taşır (veri kümesinde numaralar 1–14).
- **Küçük örneklem.** 35 karar (bir satır 2,9 puan), tek model, aynı günün iki koşusu. Genel bir
  Türkçe kalite ölçümü değildir.
- **Sabit eşiği olmayan kurallar** ("sorumluluk sınırı sözleşme bedelini aşamaz"): kod iki değeri
  karşılaştırmaz, yalnızca alıntıyı doğrular; sayısal olmayan kuralda taslak "kod doğrulayamadı"
  etiketi alır.
- **Audit log dosya düzeyinde korunmaz.** Uygulama üzerinden değiştirilemez (`UPDATE` / `DELETE`'i
  reddeden tetikleyiciler), ama veritabanı dosyasına doğrudan erişen biri değiştirebilir; imza ya
  da hash zinciri yok.

Tam liste: `docs/spec.md` §8 "Bilinen sınırlar".

## Denenmeyenler

- LangSmith'e yükleme gerçek sunucuya karşı (yalnızca sahte anahtar ve yakalanan isteklerle).
- Çıkış kodu 3 (yalnızca sahte modelle). Gerçek limitte çıkış kodu 2 ve `--resume` holdout
  koşusunda bir kez görüldü.
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

## Daha fazla vaktim olsaydı

- **Embedding ile arama (BM25 + vektör).** İlk eklenecek şey budur; sözleşme 4'teki iki kaçış bu
  sınırdan geliyor. Uzun sözleşmede tek başına yetmez: kural başına tek madde ve tek alıntı, ilk 5
  madde sınırı, kural başına 8 model çağrısı ve `Madde N` kalıbına uymayan iç içe numaralar da
  değişmeli.
- **Taslağın maddesini bulgunun maddesiyle eşleştirmek** ve taslak yazımını kodla zorlamak.
- **Ad tanıma** (gövdedeki adlar) ve maskeleyicinin kalan biçimleri.
- **Daha büyük ve bağımsız bir eval seti**, birden çok koşu ve günle; yerel model ile büyük modelin
  aynı sette karşılaştırılması.
- **Üretimde izleme açıkken uygulamanın başlamayı reddetmesi** (bugün koruma işletim kuralıdır).
- Audit log için imza ya da hash zinciri; anahtar döndürme ve kişi bazlı kimlik.

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
