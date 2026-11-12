# Uygulama Planı — Sözleşme İnceleme Agent'ı

## Durum
- **Biten:** Adım 0 (`49a13c8`), Adım 1 (`fd4cf9b`). Dokümanlar planla uyumlu; iki karar değişikliği
  ve on üç netleştirme `docs/` altına yazıldı. Adım 2: proje iskeleti ve `src/config.py` yazıldı;
  bu sırada alınan kararlar (ayarları yalnızca giriş noktaları yükler, `Store(data_dir, tenant_id)`,
  `create_app(settings)`) `docs/` altına ve Adım 3 / 10 / 11 / 12'ye işlendi. Adım 3: modeller, iki
  tenant'ın kural seti ve `Store` yazıldı; bu sırada alınan kararlar (tek `Finding` modeli, JSON
  sütunları, koşullu durum geçişi, `INSERT OR REPLACE`'i reddeden üçüncü tetikleyici, `close_all()`)
  `architecture.md` §2 ve `code_standards.md`'ye işlendi. Adım 4: `src/text.py`, madde bölme ve
  enjeksiyon taraması yazıldı; bu sırada alınan kararlar (görünmez küme = Unicode `Cf`, baştaki BOM
  istisnası, imza bloğu sınırı, sıralı `c<n>` kimliği, işaretin alanları, kalıp listesinin sınırı)
  `agent_design.md` §4 ve §5'e işlendi. `scan_injection(text, clauses)` ve `split_clauses(text)`
  saf fonksiyonlardır; Adım 8 bunları çağırır. Adım 5: `src/prepare/pii.py` yazıldı
  (`mask(text, mapping) -> (maskeli metin, yeni eşleme)` ve `unmask(text, mapping)`, ikisi de saf);
  bu sırada alınan kararlar (ad dizisinin durma kuralları, unvan ayırma, temiz ayrılmayan dizi,
  yer tutucu türleri, birebir yazım eşitliği, tekrar çağrılabilirlik, fazla maskeleme sınırları,
  bilinen adın harf büyüklüğüne ve boşluk biçimine bakılmadan aranması, sözcük içi tire ve kesme
  işareti, dörtlü gruplarla yazılmış IBAN'ın kapsama alınması) `agent_design.md` §5'e işlendi;
  IBAN kapsamı ayrıca `architecture.md` §6, `spec.md` ve `evals.md`'deki listelere yansıtıldı. Adım 8 için not: adlar imza bloğunda bulunup tüm metinde
  değiştirildiğinden `prepare_contract` eşlemeyi önce tüm metinle kurar, sonra her maddeyi o
  eşlemeyle maskeler; maskeleme görünmez karakterleri çıkarılmış kopyada çalışır (araya gizli
  karakter konmuş TCKN kaçmasın).
  Adım 6: `src/verify.py` yazıldı (`verify(...)` saf; tek çıkarma fonksiyonu `extract`, tek süre
  çevirme fonksiyonu `align`); bu sırada alınan kararlar `agent_design.md` §5'e işlendi:
  `no_finding`'in temsili (`status=not_found` + `no_finding_reason`), sayı sözcükleri 1–999, kapalı
  ek listesi, **okunamayan değer ifadesi** (kod sayı içeren ifadeyi sessizce atmaz; okunan sayı
  yazılan sayının tamamıdır), önekli tutar, yer tutucuların sayı sayılmaması, `==` için aralık
  karşılaştırması, modelin `unit`'i, alıntının sözcüğü / sayıyı / değer ifadesini ortasından
  kesememesi, `compliant` bulguda maddenin alıntı dışında kalan değerlerinin de `check` ile
  karşılaştırılması (dar alıntı kuralı çiğneyen değeri gizleyemez), `enabled=False`'ta alıntının ve
  `code_check`'in dokunulmadan kalması. Yeni zorunlu
  test "Okunamayan değer atılmaz" `evals.md` §2'ye eklendi. `pii.py`'deki yer tutucu kalıbı
  `PLACEHOLDER` adıyla dışa açıldı (`verify.py` aynı kalıbı kullanır). Adım 18 için not: kapalı
  listeler gereksiz yönlendirme üretir ("5 ayrı", "onay günü"); oran eval'de görülecek.
  Adım 7: `src/llm.py` yazıldı (`build_chat_model(settings, *, http_client=None)` ve
  `classify_provider_error(exc)`); bu sırada alınan kararlar `tech_stack.md`, `agent_design.md` §6,
  `code_standards.md`, `spec.md` ve `evals.md` §2'ye işlendi: incelemeyi `failed` yapan hata sınıfı
  yalnızca 401 değil, `config` sınıfıdır (Groq 401 / 403 / 404, Ollama 404; bağlantı reddi, zaman
  aşımı, 5xx ve 400 `llm_error` kalır); `http_client` yalnızca Groq içindir, `ollama` ile
  `ValueError`; `ChatGroq` `temperature=0`'ı istekte `1e-8` yapar. Zorunlu test "Geçersiz sağlayıcı
  anahtarı"nın adı "Kimlik / yapılandırma hatası" oldu. `tests/conftest.py`'ye tüm testlerde
  LangSmith tracing'i kapatan fixture eklendi. Adım 10 için not: `review_rule` hatayı
  `classify_provider_error` ile ayırır (`config` → yeniden fırlat, `rate_limit` → `rate_limited`,
  `other` → `llm_error`). Listede olmayan bir Groq modeli `reasoning_effort="low"`'u reddederse
  (400) her kural `llm_error` olur.
  Adım 8: `src/prepare/index.py` (`tokenize`, `ClauseIndex.search` / `get`, `put_index` /
  `get_index` / `drop_index` / `clear_indexes`) ve `src/prepare/__init__.py`
  (`prepare_contract(store, text, *, provider, model) -> PreparedContract`) yazıldı; örnek sözleşme
  `tests/fixtures/sample_contract.md`'de. Bu sırada alınan kararlar dokümanlara işlendi
  (`c1fc71a` ve bu commit): `BM25Plus` + ortak anahtar şartı (`BM25Okapi` kısa sözleşmede 0 ve
  negatif skor verir); indeks kaydının anahtarı `(tenant_id, review_id)`; ham ve maskeli madde
  ayrı tipler (`RawClause` / `Clause` + `MaskedText`, ayrı commit `44fe5d8`); hazırlık yarıda
  kalırsa `prepare_contract` incelemeyi `failed` yapar ve `review_failed`'ı (`prepare_error`)
  kendisi yazar; yer tutucular indeks anahtarı üretmez; işaretin `phrase`'i tek başına
  maskelenmez, maskeli madde metninden alınır ve kesilen değerin yer tutucusunu bütün alacak
  kadar genişler (iki satıra bölünmüş adın soyadı ifadede yalın kalıyordu). Audit yükleri:
  `clauses_split {count}`, `pii_masked {counts: tür → adet}`, `index_built {count}`,
  `injection_flagged` = maskeli işaretin alanları. Adım 9 için not: indeksi `review_rule`
  `get_index(tenant_id, review_id)` ile alır ve tool'lara `ReviewContext.index` ile verir;
  `<sozlesme_maddesi>` etiketini indeks değil tool ekler; `ClauseIndex.get` maskeli maddeyi verir. Adım 8 sonrası düzeltme: `injection_flagged`
  satırları artık `Store.append_audit_many` ile tek transaction'da yazılır (ya hepsi ya hiçbiri;
  karar `code_standards.md`'de). Önce her işaret ayrı commit'ti ve süre işaret sayısıyla
  büyüyordu; dosya boyutu değil, sözleşmeyi yazanın belirlediği işaret sayısı belirleyiciydi.
  Tek maddede N tetik cümlesi, gerçek audit yazımıyla, geliştirme makinesinde, üç çalıştırmanın
  en iyisi (test değil, elle ölçüm): 500 işaret (10 KB) 3,03 sn → 0,12 sn; 4000 işaret (81 KB)
  25,9 sn → 0,58 sn. Süre testle doğrulanmaz (yavaş makinede sebepsiz kırılır); test edilen
  atomikliktir. Adım 12 için notlar: (1) `POST /reviews` gövdesine boyut sınırı genel önlem
  olarak konur (bellek, model maliyeti); bu sorunun çözümü o değildir. (2) İşaret sayısının
  kendisi hâlâ sınırsızdır: her işaret bir audit satırıdır ve avukatın gördüğü listeye girer;
  bir üst sınır ya da özet satırı orada değerlendirilecek. (3) Hazırlık hata verirse `prepare_contract` satırı zaten `failed` yapmıştır; API yalnızca hatayı
  yanıta çevirir.
  Adım 9: `src/tools.py` (`ReviewContext`, `RuleAgentState`, 5 tool), `src/agent.py`
  (`build_rule_agent(model)`, `run_rule_agent(agent, context) -> Finding`, `interrupted_finding`,
  `RuleRunInterrupted`, `RUN_LIMIT`), `src/prompts/rule_agent.md` ve betikli sahte model
  (`tests/conftest.py`: `ScriptedChatModel.single(...)` / `.keyed({kural metni: [...]})`) yazıldı.
  Tasarım kurulu sürümlere (langchain 1.4.10, langgraph 1.2.20) karşı script'lerle sınandı; bu
  sırada alınan kararlar `agent_design.md` §1, §2, §5, §6, §7, `evals.md` §2, `code_standards.md`
  ve `architecture.md`'ye işlendi: agent `checkpointer=False` ile kurulur (yoksa üst graph'ın
  `SqliteSaver`'ını devralır); kanıt hatası tool gövdesinden döner (şemadaki `model_validator`
  hatası modele boş metinle ulaşır); adım limiti sayaçsız, `AIMessage` sayısı > 8 ile ayırt
  edilir; **koşu kendiliğinden bitmediyse sonucu her zaman avukat görür**: bulgu bildirildikten
  sonra adım limiti ya da LLM hatası gelirse bulgu korunur, `Finding.interrupted_by` dolar ve
  `verify` yeni `run_interrupted` sebebiyle kuyruğa sokar (bunun için agent `invoke` değil
  senkron `stream` ile koşar); `no_finding` bulgusuna taslak ve devretme eklenmez; `tool_call`
  satırı `search_contract` için tüm `clause_ids`'i taşır; başarısız model çağrısı da `model_call`
  satırı yazar (`error`: sınıf adı + HTTP durum kodu). Bilinen sınırlar: taslağın maddesi
  bulgunun maddesiyle karşılaştırılmaz; sağlayıcının kendi hata kodu audit'e yazılmaz. Adım 10
  için notlar: (1) `review_rule` `RuleRunInterrupted`'ı yakalar, `__cause__`'u sınıflandırır,
  `config` ise onu yeniden fırlatır; `sqlite3.Error` sarmalanmadan gelir. (2) Sahte model kural
  metnine göre anahtarlıdır; paralel testte kuralların metni farklı olmalıdır. (3) Aynı model
  yanıtındaki tool çağrılarının audit satır sırası tamamlanma sırasıdır; testler sayıyı doğrular.
  Adım 12 için not: Groq'un bozuk tool çağrısında döndürdüğü hata gövdesine gerçek çağrıda
  bakılacak; kod ayırt etmeye değerse `src/llm.py`'ye bir yardımcı eklenir.
  Adım 10: `src/graph.py` (`START → review_rule × N (Send) → verify → human_review (interrupt)`,
  tenant başına derlenmiş ve önbelleğe alınmış graph, `close_graphs()`) yazıldı; `src/agent.py`'ye
  `ModelCallFailed` işareti eklendi. Bu sırada alınan kararlar `agent_design.md` §1, §4, §6, §7,
  `code_standards.md`, `spec.md`, `evals.md` §2 ve `tech_stack.md`'ye işlendi: state düz sözlük
  tutar (`model_dump(mode="json")`, düğümler girişte doğrular; izin listesi yok); hatalar tipine
  göre değil **kaynağına göre** ayrılır: yalnızca model çağrısının kendi hatası
  (`ModelCallFailed` → `RuleRunInterrupted`) bulguya çevrilir, başka her hata graph'tan fırlar ve
  incelemeyi `failed` yapar (yeni zorunlu test "Beklenmeyen kod hatası incelemeyi `failed` yapar");
  `finding` satırı `agent`, `verification` satırları `system` aktörlüdür, bulgu başına bir satır,
  tek transaction'da; `verify` düğümü bulguları kural sırasına dizer ve kural başına tek bulgu
  olduğunu denetler; graph indeksi silmez, `awaiting_lawyer` / `review_failed` yazmaz. Kurulu
  sürümlerle (langgraph 1.2.20, langgraph-checkpoint-sqlite 3.1.3) script'le sınananlar:
  (A) invoke config'teki `max_concurrency` `Send` dallarını gerçekten sınırlar (8 dal, sınır
  1 / 2 / 4 → en fazla 1 / 2 / 4 aynı anda); semafor yok, `spec.md` §10 sorusu kapandı.
  (B) Bir dal hata fırlatınca `graph.invoke` asıl hatayı yalnızca başlamış her dal bittikten sonra
  fırlatır; `invoke`'tan sonra yan etki yok. İlk sınama "başlamamış dallar başlamaz" demişti; bu
  yanlış çıktı: hata veren dalın boşalttığı iş parçacığı iptalden önce sıradaki dalı alabilir
  (hatadan sonra en fazla `max_concurrency` kadar dal daha başladı), yani `failed` incelemede
  başka kuralların da `model_call` / `finding` satırları olabilir. Böyle bir thread bir daha
  çağrılmaz. (C) Düz sözlük state checkpoint'e uyarısız yazılıp okunur; pydantic nesnesi okunurken
  "kayıtsız tip" uyarısı loglanır. `interrupt`'tan sonra `get_state(config).next ==
  ("human_review",)`. (E) `SqliteSaver.setup()` `checkpoints.db`'yi kendisi WAL'a alır; bu adımda
  dokunulmadı. Adım 11 için not: graph'tan çıkan, `config` sınıfı olmayan her hatada (kod hatası,
  `sqlite3.Error`) API de incelemeyi `failed` yapar, `review_failed` yazar ve 500 döner (`config`
  için 502 kalır); `review_failed` son satırdır (B); `failed` incelemenin thread'i bir daha
  çağrılmaz. Adım 12 için not: `checkpoints.db` host'tan bağlanan `./data` üzerinde WAL'da
  çalışır; gerçek Docker bind mount'unda denenecek. Sorun çıkarsa yedekler: bağlantı
  `SqliteSaver`'a verilmeden önce `PRAGMA locking_mode=EXCLUSIVE` (WAL `-shm` dosyasız; ikinci
  süreç dosyayı açamaz) ya da `setup()`'tan sonra `journal_mode=DELETE`'e dönmek.
  Adım 11: `src/api.py` (`create_app(settings)`, `get_principal`, `require_lawyer`,
  `POST /reviews`, `GET /reviews/{id}`, `GET /reviews/{id}/audit`, `python -m src.api` →
  `0.0.0.0:8000`) yazıldı; `Store.save_findings` değişti. Bu sırada alınan kararlar
  `architecture.md` §2–§3, `agent_design.md` §7, `code_standards.md`, `evals.md` §2 ve
  `tech_stack.md`'ye işlendi: (1) `save_findings` `awaiting_lawyer` durumunu ve audit satırını
  (`{finding_count, needs_review_count}`, `system`) **tek transaction'da** yazar; ayrı yazılsaydı
  ikinci yazımın hatası audit satırı olmayan, `failed` de yapılamayan bir inceleme bırakırdı.
  (2) Yanıtlar **sabit alan listesiyle** kurulur (`*View` modelleri; kayıt dökülüp alan silinmez);
  `lawyer` alanları açıkça eklenir, maske yalnızca `_unmask_lawyer_view`'de açılır, `viewer` için
  `get_pii_map` çağrılmaz. İnceleme yanıtında `provider`, `model` ve karar alanları yoktur.
  (3) `viewer` madde ofsetlerini de almaz (ofset ile maskeli metin farkı değer uzunluğunu verir).
  İşaret konumları ise gizli sayılmaz: `injection_flagged` audit satırında durur ve `viewer` onu
  okur; inceleme yanıtına konmamalarının sebebi asıl metin olmadan işe yaramamalarıdır.
  (4) Sözdizimi bozuk JSON kimlik kontrolünden önce 422 alır (FastAPI 0.162.4'te script'le
  sınandı; gövdesiz, şeması yanlış ya da JSON olmayan anahtarsız istek 401 alır); middleware
  yazılmadı, sınır dokümanda. (5) `review_failed` sebepleri: `prepare_error` / `provider_config`
  (502) / `internal_error` (500). Kurallar ve graph hazırlıktan önce alınır (hata `running` satır
  bırakmaz); indeks başarıda da hatada da silinir; yanıta ve loga hata metni girmez. (6) İstek
  modeli bilinmeyen alanı yok sayar; `text` boş ya da yalnızca boşluk olamaz. (7) `create_app`,
  anahtarlardaki her tenant'ın bilinen tenant olduğunu başlangıçta doğrular (`ConfigError`).
  Uygulamayı bir alt-agent yazdı; üç bağımsız inceleme (güvenlik değişmezleri, hata yolları,
  testlerin kanıt gücü) tek bulgu verdi (işaret konumlarının doğruluğu sınanmıyordu) ve test
  güçlendirildi. Sunucu `python -m src.api` ile elle başlatılmadı; Adım 12'de denenir.
  Adım 13 için notlar: karar alanları yanıta `*View` modellerine eklenerek girer; yeni metin
  alanı `_unmask_lawyer_view`'e de yazılır (unutulursa `lawyer` yer tutucu görür, sızıntı olmaz).
  Adım 15 için not: rota dolaşan iki test yalnızca `APIRoute`'ları sayar; arayüz `StaticFiles`
  ile `Mount` edilirse testlerin kapsamı dışında kalır, sayfa `GET /` rotası olarak eklenmelidir.
  Açık gözlemler: FastAPI'nin 422 gövdesi çağıranın kendi girdisini yansıtıyordu (Adım 12'de
  kapandı: 422 yanıtı yalnızca `type`, `loc`, `msg` taşır); değiştirilmeyen: anahtar araması düz sözlük aramasıdır (sabit zamanlı karşılaştırma yok).
  Adım 12: `Dockerfile`, `.dockerignore`, `docker-compose.yml` ve `python -m src.config` girişi
  yazıldı; ayrı commit'te (`a9f4ecd`) `POST /reviews` metnine 2.000.000 karakter sınırı (413) ve
  girdiyi geri yansıtmayan 422 yanıtı eklendi. Bu sırada alınan kararlar `tech_stack.md`,
  `architecture.md` §3 ve `spec.md`'ye işlendi: `ollama-pull` uygulama imajıyla çalışır ve modeli
  `ollama` Python istemcisiyle çeker; compose `OLLAMA_BASE_URL`'i boşsa `http://ollama:11434` yapar;
  `app` çevrimdışı modda `ollama-pull`'u bekler (`required: false`); port yalnızca `127.0.0.1`'de;
  enjeksiyon işareti sayısına sınır konmadı (bilinen sınır). Elle doğrulananlar ve ölçümler
  `spec.md` §10 "Adım 12 gözlemleri"nde: varsayılan modda yalnızca `app` başlar; anahtar boşken
  tek satır hata, çıkış kodu 1, yeniden başlama yok; `checkpoints.db` WAL bind mount'ta sorunsuz;
  örnek sözleşme Groq'ta ve `qwen3.5:4b`'de (host Ollama ve compose `offline` profili) bulgu
  döndürdü. **Planlanandan sapanlar:** (1) çevrimdışı deneme varsayılan `qwen3.5:9b` ile değil
  yedek `qwen3.5:4b` ile yapıldı (4 GB ekran kartı); (2) `.env`'deki Groq anahtarında ölçülen limit
  250K değil 8K TPM; varsayılan `MAX_CONCURRENCY=4` ile 5 kuraldan 3'ü `rate_limited` oldu.
  Adım 17–18 için notlar: tam eval bu anahtarda günlük limite büyük olasılıkla sığmaz;
  `gpt-oss-120b` `get_clause` ve `propose_change`'i atlıyor (prompt); `MAX_CONCURRENCY`
  varsayılanı açık soru.
- **Sıradaki:** Adım 13 — Avukat kararı ve finalize.
- Bu bölüm her adımın commit'inde güncellenir; sıradaki adım, aşağıdaki ilk işaretsiz kutudur.

## Bağlam
Repoda yalnızca dokümanlar var (`CLAUDE.md`, `docs/*.md`); kod yok. Bu plan, dokümanlardaki
kararlara uyarak (istisnalar aşağıdaki iki karar değişikliği) uygulamayı sıfırdan, küçük ve sırayla yapılan
adımlarla yazmak içindir. Her adımın sonunda çalışan ve testle doğrulanan bir parça olur ve adım tek
bir commit ile biter (yalnızca push olan Adım 20 hariç).
Plan onaylandı (2026-11-11); adımlar ilerledikçe burada işaretlenir.

Dokümanlar tarandı (çapraz çelişki, uygulayıcı gözüyle belirsizlik, deterministik kurallar,
LangChain / LangGraph / LangSmith / Groq API'lerinin güncel hali). **Kullanıcı kararı gerektiren
kritik bir çelişki çıkmadı.** Kod yazarken tahmin gerektirecek on üç nokta Adım 1'de dokümanlara
yazılır. `tech_stack.md`'deki sürüm sınırları PyPI'de birlikte çözülüyor.

**Plan sırasında alınan iki karar değişikliği** (ikisi de Adım 1'de dokümanlara yazılır):

**1. Arama** hibrit (BM25 + embedding) yerine **yalnızca BM25** olur. Sebep: kapsamdaki sözleşmeler 15–40 madde; bunun için Groq modunda ~4,6 GB'lık Ollama
imajı ve embedding modeli indirtmek orantısız. Bedeli (dolaylı ifadeyi kaçırma riski) gizlenmez:
"arama isabeti" metriğiyle ölçülür ve sözleşme 4 gerçek bir dolaylı ifade içerir.

**2. Her `not_found` avukat kuyruğuna gider.** Sebep: "bulunamadı" sistemdeki tek kanıtsız sonuçtur;
alıntı olmadığı için kodun doğrulayacağı bir şey yoktur. Eski iki sinyal (anahtar kelime, `critical`)
bunu dolaylı tahmin ediyordu ve dolaylı ifadeyle yazılmış bir maddede ikisi birden susuyordu.
Anahtar kelime sinyali ve `rules.yaml`'daki `keywords` alanı kalkar; `severity` yalnızca etiket olur.
Bedeli avukata giden bulgu sayısının artmasıdır; önce/sonra tablosundaki yönlendirme oranı ve
gereksiz yönlendirme satırları bunu gösterir.

## Her adım için ortak kurallar
- Adım 2'den itibaren adım "bitti" sayılmadan önce üçü de temiz olmalı (`docs/evals.md` §1):
  `uv run pytest` · `uv run ruff check . && uv run ruff format --check .` · `uv run mypy src`
- Adım 17'den itibaren agent ya da doğrulama davranışını değiştiren her commit'ten önce eval de
  koşulur (`docker compose run --rm app python -m evals.run`) ve `evals.md` §8 eşikleri korunur.
- Doğrulama geçince **tek commit** (İngilizce, conventional commits); aynı commit'te `docs/plan.md`
  içinde adımın kutusu işaretlenir.
- Dokümandaki bir karardan sapmak gerekirse önce ilgili doküman güncellenir, sonra kod yazılır.
- Unit testlerde gerçek LLM ve ağ yok. Gerçek model yalnızca Adım 12'de (elle deneme) ve 17–18'de (eval).
- `.env` dosyasını ve `GROQ_API_KEY` değerini kullanıcı kendisi koyar; anahtar repoya ve sohbete girmez.

## Adımlar

### [x] Adım 0 — Planı kaydet
- **Ne:** Onaylanan plan `docs/plan.md` olur; `CLAUDE.md`'deki doküman tablosuna satırı eklenir.
- **Dosyalar:** `docs/plan.md`, `CLAUDE.md`
- **Doğrulama:** Dosya repoda; adım kutuları işaretlenebilir halde.
- **Commit:** `docs: add implementation plan`

### [x] Adım 1 — Doküman netleştirmeleri ve arama kararı
- **Ne (A — netleştirmeler):** Kod yazılmadan önce aşağıdaki noktalar ilgili dokümana yazılır (karar
  değişmez; yazıldığı haliyle belirsiz ya da bir zorunlu testle çelişen yerler netleşir):
  1. `agent_design.md` §5 — Alıntı ve sayı sinyalleri yalnızca `compliant` / `non_compliant`
     bulgularda çalışır; sayı yalnızca doğrulanmış alıntıdan çıkarılır; `ambiguous_value` varsa
     `numeric_mismatch` eklenmez. (Yoksa `check`'li her `not_found` bulgusuna yanıltıcı bir
     `numeric_mismatch` sebebi eklenir.)
  2. `agent_design.md` §5 — Yüzde sayıdan önce yazılır (`%15`, `yüzde 15`); `%` ve `TL`'de nokta
     binlik, virgül ondalıktır; birimler sözcük sınırında ve yaygın eklerle eşleşir (`günlük`,
     `aylık`); `check.op`: `>=`, `<=`, `>`, `<`, `==`.
  3. `agent_design.md` §5 — Modelin `value` + `unit`'i kodun çıkardığı değerle aynı çevirme
     fonksiyonundan geçirilip karşılaştırılır (kural: madde 12).
  4. `architecture.md` §3 adım 2 — Hazırlık sırası: asıl metni sakla → böl (ofsetlerle; `Madde 1`
     öncesi ve son maddeden sonraki metin de birer maddedir) → görünmez karakterleri çıkarılmış
     kopyada tara, konumları asıl metne eşle → maskele → işaretteki ifadeyi (eşleşmenin geçtiği
     cümle) aynı eşlemeyle maskele → indeksle. Audit olayları yükleri maskelendikten sonra yazılır.
     Görünmez karakter kümesi tek yerde tanımlanır.
  5. `architecture.md` §3 — `POST /reviews` gövdesi `{"text": "…"}`; `review_id` UUID'dir; adım 3'teki
     cümle "maskeli maddeler, kurallar, enjeksiyon işareti ve kimliklerle" olarak düzeltilir;
     `viewer` için "konum yok" kuralı inceleme ve rapor yanıtlarına uygulanır (audit saklandığı gibi döner).
  6. `agent_design.md` §2 / §4 — Tool'lar beklenen hataları (ör. olmayan madde) fırlatmaz, hata metni
     olarak döner; `finding` / `proposal` / `escalation` alanları "son yazan geçerli" birleştiricisi
     taşır (model aynı mesajda iki çağrı yaparsa agent çökmez).
  7. `evals.md` §6 — "Düşük limitli hesaplar için koşucu `--limit N` ile yalnızca ilk N sözleşmeyi
     koşar. Sonuç dosyası ve tablo kaç sözleşmenin koşulduğunu yazar; koşulmayan sözleşmelere bağlı
     metrikler 'ölçülmedi' görünür ve kısmi koşu tam koşuyla karşılaştırılmaz."
  8. `evals.md` §4 ve §5 — yeni metrik **Gereksiz yönlendirme**: avukat kuyruğuna düşen bulgulardan
     `status`'ü etiketle aynı olanlar / kuyruğa düşen tüm bulgular (eşik yok, raporlanır). Önce/sonra
     tablosuna da satır olarak girer; yönlendirme oranının yanında bedelin ne kadarının boşa
     gittiğini gösterir. Not: enjeksiyon şüpheli sözleşmelerde tüm bulgular tasarım gereği kuyruğa
     gittiği için bu oran orada yüksek çıkar.
  9. `evals.md` §3 ve §4 — etiketteki `pii_freetext_names` listesi `pii_best_effort` olur ve
      genişler: yalnızca serbest metin adları değil, maskelenmesi **garanti edilmeyen** her kimlik
      bilgisi (gövdede geçen ad, öneksiz telefon, boşluklu TCKN / IBAN, etiketsiz imza satırındaki
      ad). Metrik "Garanti edilmeyen PII sızıntısı" adını alır: bu listeden modele gidenlerin sayısı
      ve toplamı (eşik yok, raporlanır). `pii_values` satırı için not: bu bir ölçüm değil bekçidir;
      maskeleme deterministik olduğu için yapı gereği 0 çıkar ve yalnızca maskelemeyi atlayan bir
      kod yolunu yakalar. Değerler maskeleyicinin onları yakalayıp yakalamadığına göre değil, ne
      olduklarına göre etiketlenir.
  10. `agent_design.md` §5 — Alıntı doğrulaması geçince bulgunun `quote` alanı **sözleşmedeki eşleşen
      parçayla** değiştirilir; saklanan, avukata gösterilen ve arayüzde işaretlenen metin budur.
      Sayı ve birim de bu parçadan çıkarılır. Modelin yazdığı ham metin audit log'daki `tool_call`
      kaydında kalır. Doğrulanamayan alıntı olduğu gibi kalır ve `quote_not_found` ile işaretlenir.
      Normalleştirmenin yok saydıkları aynı yerde açıkça sayılır (Unicode yazım biçimleri, sıfır
      genişlikli karakterler, boşluk, tırnak ve tire türleri); büyük-küçük harf, noktalama ve
      Türkçe harfler yok sayılmaz. `architecture.md` §4 — klasör yapısına `src/text.py` eklenir:
      metni normalleştirip her karakterin asıl metindeki konumunu veren tek yardımcı. Enjeksiyon
      taraması ve alıntı doğrulaması aynı yardımcıyı kullanır; ikinci bir kopyası yazılmaz.
  11. `code_standards.md` "Async / eşzamanlılık" ve "Hata yönetimi" — `app.db`'ye eşzamanlı yazım:
      tenant başına tek bağlantı (`check_same_thread=False`, `timeout=30`) ve tek `threading.Lock`
      süreç içinde paylaşılır; `Store` her okuma ve yazımı bu kilidin içinde yapar, her yazım kendi
      transaction'ında hemen commit edilir. WAL açılmaz (`./data` host'tan bağlanır). Audit
      yazımından gelen `sqlite3.Error` `no_finding`'e çevrilmez: kimlik hatası gibi yeniden
      fırlatılır ve inceleme `failed` olur (eksik kayıtla tamamlanmış inceleme olmaz).
  12. `agent_design.md` §5 (çevirme kuralı ve sinyal tablosu), `evals.md` §2 — süre birimleri sabit
      "ay = 30, yıl = 365" ile çevrilmez; iki aile vardır:
      - **Aile içi kesin:** gün ↔ hafta (1 hafta = 7 gün); ay ↔ yıl (1 yıl = 12 ay).
      - **Aileler arası aralık:** ay ya da yıl, gün ya da haftayla karşılaştırılırken aralıktır
        (n ay = 28n–31n gün, n yıl = 365n–366n gün).
      - **Tek çevirme fonksiyonu:** `check` hesabı, belirsiz değer kontrolü, modelin `value`'suyla
        kıyas ve taslak kontrolü aynı fonksiyonu kullanır. Aynı ailedeki değerler kesin
        karşılaştırılır; farklı ailede gün değeri ayın / yılın aralığına düşüyorsa iki değer aynı
        sayılır ("30 gün … bir ay" tek değerdir; "on iki (12) ay, yani bir yıl" tek değerdir).
      - **Yeni sinyal `unit_conversion_uncertain`:** kod `check`'i tüm değerlerin tüm uçlarında
        hesaplar; sonuçlar aynı değilse seçim yapmaz, bulgu kuyruğa düşer ("1 ay" ile "en az 30
        gün": 28–31). Uçların hepsi aynı sonucu veriyorsa kod karar verir ("6 ay" ile "en az 30 gün").
      - **Taslak:** belirsiz sonuç `code_check=failed` sayılır (`proposal_noncompliant`).
      - `evals.md` §2 "Sayıyı kod çıkarır" ve "Belirsiz değer" testleri buna göre güncellenir:
        eski "'30 gün … bir ay' tek değerdir, kuyruğa düşmez" cümlesi şu olur: "'30 gün … bir ay'
        tek değerdir (`ambiguous_value` yok); 'en az 30 gün' kuralında `unit_conversion_uncertain`
        ile kuyruğa düşer. '60 gün … iki ay' aynı kuralda kuyruğa düşmez." Kurala istisna yoktur:
        alıntıda kesin bir gün değeri olsa da ayın aralığı hesaba girer.
  13. `agent_design.md` §6 (tarama kalıpları), `evals.md` §2 "Enjeksiyon" — sınırlayıcı etiketin
      kendisi de tarama kalıbıdır: madde metninde `sozlesme_maddesi` etiketinin açılışı ya da
      kapanışı geçiyorsa (büyük-küçük harf, araya boşluk ya da gizli karakter konmuş halleri dahil)
      sözleşme `injection_suspected` olur. Metin kaçışlanmaz ve silinmez (modelin kopyasından
      yalnızca görünmez karakterler çıkarılır kuralı değişmez).
- **Ne (B — karar değişikliği: yalnızca BM25):** Embedding, vektör deposu ve RRF kalkar; Ollama
  yalnızca çevrimdışı modda çalışır.
  - `architecture.md` — şemadaki embedding kutusu; §2 "hibrit indeks"; §3 "BM25 + embedding"; §6
    Ollama satırı ve "Embedding için metin" satırı; ADR-001 (karar: yalnızca BM25; risk: dolaylı
    ifade; ileride ilk eklenecek şey embedding) ve ADR-010.
  - `agent_design.md` §4 — arama yalnızca BM25. Tokenizer: Türkçe'ye uygun küçük harf → her sözcük
    için 3, 4 ve 5 harflik başlangıçları birlikte üretilir (`süresi` → `sür`, `süre`, `süres`; 3
    harften kısa sözcük olduğu gibi kalır). Böylece `süre` / `süresi`, `gün` / `günlük` iki tarafta
    ortak anahtar bulur; `fesih` / `feshedebilir` ve `borç` / `borcu` yalnızca 3 harflik anahtarda
    (`fes`, `bor`) buluşur, yani daha zayıf eşleşir. Kodda eş biçim listesi yoktur. Bilinen sınır:
    üçüncü harfi değişen ya da kökü 3 harften kısa sözcükler (`ret` / `reddi`, `ay` / `aylık`) ve
    dolaylı ifade eşleşmez.
  - `tech_stack.md` — Embedding ve Vektör DB satırları; Deploy (`ollama` ve `ollama-pull` yalnızca
    çevrimdışı modda, compose profiliyle); `EMBED_MODEL` değişkeni kalkar; KULLANMA listesine
    embedding / vektör araması eklenir.
  - `spec.md` — §6 ve §9'daki embedding cümleleri (~1.2 GB indirme dahil); §10'daki "sohbet ve
    embedding modeli aynı anda sığmazsa" sorusu kalkar.
  - `code_standards.md` — "Embedding her zaman yerel Ollama'dır" kuralı ve `llm.py` tanımındaki embedding.
  - `evals.md` §4 — yeni satır **Arama isabeti**: beklenen maddenin `search_contract` sonuçlarında
    en az bir kez döndüğü kuralların oranı (audit'teki `tool_call` kayıtlarından, kodla; eşik yok,
    raporlanır). §3 — sözleşme 4 gerçek bir dolaylı ifade içerir; tokenizer'a göre yazılmaz. Arama
    onu kaçırırsa dataset ya da tokenizer ayarlanmaz; sonuç README'ye sınır olarak yazılır.
- **Ne (C — karar değişikliği: her `not_found` avukat kuyruğuna gider):**
  - `agent_design.md` §5 sinyal tablosu — yeni sinyal `unverified_not_found`: `status` `not_found`
    ise her zaman (kanıtsız sonuç avukatsız geçmez). `not_found_but_keyword_hit` kalkar.
    `critical_not_found` sebep etiketi olarak kalır; kuyruğa ayrıca etkisi yoktur. `enabled=False`
    davranışı değişmez (yalnızca `agent_escalated` + `no_finding`).
  - `agent_design.md` §4 kural tanımı — `keywords` alanı kalkar. `severity` kalır, ama yalnızca
    avukata ve raporda gösterilen bir etikettir.
  - `spec.md` — FR-5, Senaryo 1'deki kuyruk listesi ("`critical` bir kural bulunamadı" ve
    "bulunamadı dendi ama anahtar kelime geçiyor" yerine "her bulunamadı"), §3 ve §9'daki ilgili cümleler.
  - `architecture.md` — ADR-001'in risk cümlesi (arama maddeyi kaçırırsa "bulunamadı" her durumda
    avukata gider) ve ADR-011 (kuyruk: belirsiz olanlar ve kanıtsız olanlar).
  - `evals.md` §2 — "Kritik kural" testi değişir: `normal` ve `critical` kuralın `not_found` sonucu
    ikisi de kuyruğa düşer; `critical` olanda ek sebep `critical_not_found`. §3 — sözleşme 3'ün
    amacı "eksik maddeler: her `not_found` kuyruğa düşer" olur; "anahtar kelimesi de geçmiyor"
    şartı kalkar. §4 — "Kritik kural yakalama" satırına not: her `not_found` zaten kuyruğa gittiği
    için artık ayırt edici değildir; eşiği "%100 (hedef)" yerine "raporlanır" olur. §5 — bu satır
    önce/sonra tablosundan çıkarılır. §8 — regresyon listesinden çıkarılır (liste: güvensiz hata,
    enjeksiyon sonucu, PII sızıntısı). Sinyalin kendisini Adım 6'daki unit test korur.
  - `spec.md` §3 — "Kritik kural yakalama = %100 (hedef)" başarı kriteri "her `not_found` avukat
    kuyruğuna düşer (kod garanti eder)" olarak değişir.
- **Dosyalar:** `docs/agent_design.md`, `docs/architecture.md`, `docs/evals.md`, `docs/tech_stack.md`,
  `docs/spec.md`, `docs/code_standards.md`
- **Doğrulama:** On üç netleştirme ve iki karar değişikliği dokümanda. Dokümanlarda "embedding",
  "hibrit", "bge-m3", `EMBED_MODEL` araması yalnızca ADR-001'deki "ileride" notunda ve KULLANMA
  listesinde eşleşir; `keywords` ve `not_found_but_keyword_hit` araması hiç eşleşmez; başka karar
  değişmemiş.
- **Commit:** `docs: clarify rules, switch search to BM25 and queue every not_found`

### [x] Adım 2 — Proje iskeleti ve config
- **Ne:** uv projesi (Python 3.12, `tech_stack.md`'deki sürüm sınırlarıyla tüm bağımlılıklar), ruff /
  mypy / pytest ayarı, `.gitignore`, `.env.example`. `src/config.py`: env yalnızca burada okunur;
  sağlayıcı varsayılan modelleri, `TENANT_API_KEYS` (JSON → tenant + rol + ad), `MAX_CONCURRENCY`
  varsayılanı, başlangıç kontrolü (eksik `GROQ_API_KEY`, bilinmeyen sağlayıcı, boş `TENANT_API_KEYS`
  → değişkenin adını söyleyen hata).
- **`.env.example` — LangSmith:** LangSmith değişkenleri boş durur; yanında not: "yalnızca eval için;
  uygulamada açarsan gerçek sözleşmelerin maskeli trace'i dışarı gider". Kodla zorlanmaz.
- **Dosyalar:** `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `.env.example`,
  `src/__init__.py`, `src/config.py`, `tests/test_config.py`
- **Doğrulama:** `uv sync` sürümleri çözer. Testler: *Sağlayıcı anahtarı yoksa başlamaz* (mesajda
  `GROQ_API_KEY` ve `LLM_PROVIDER=ollama`), `ollama` iken anahtarsız hata yok, bilinmeyen sağlayıcı,
  boş `TENANT_API_KEYS`, boş `LLM_MODEL` → sağlayıcı varsayılanı.
- **Commit:** `chore: scaffold project and add env config with startup checks`

### [x] Adım 3 — Modeller, kural setleri, Store
- **Ne:** pydantic şemaları (`Rule`, `Clause`, `Finding`, `Proposal`, enjeksiyon işareti). İki
  tenant'ın `rules.yaml` dosyası (her biri ~5 kural: `check`'li gün / yıl / % / TL kuralları,
  `check`'siz sayısal bir kural, sayısal olmayan kurallar, `critical` / `normal` karışık).
  `keywords` alanı yoktur; `severity` yalnızca etikettir.
  `Store(data_dir, tenant_id)` (`data_dir` giriş noktasının yüklediği `Settings`'ten gelir; Store
  env okumaz): tenant bilinen listeye karşı doğrulanır (liste: `data_dir/tenants/` altında
  `rules.yaml`'ı olan klasörler); tenant'ın `app.db`'sinde üç tablo
  (`reviews`, `audit_log`, `pii_map`); `audit_log`'da `UPDATE` / `DELETE`'i reddeden tetikleyiciler;
  yalnızca parametreli SQL; `review_id` UUID. Sonraki adımların ihtiyacı olan tüm yöntemler burada
  yazılır: inceleme oluştur / oku / güncelle, audit ekle / oku, PII eşlemesi oku / yaz, kuralları yükle.
- **Eşzamanlı yazım:** tenant başına tek `app.db` bağlantısı
  (`sqlite3.connect(path, check_same_thread=False, timeout=30)`) ve tek `threading.Lock` süreç
  içinde paylaşılır (`(data_dir, tenant_id)` ile anahtarlanır; testler `tmp_path` kullanır);
  `Store(data_dir, tenant_id)` örnekleri aynı bağlantıyı ve kilidi kullanır. Her okuma ve
  yazım kilidin içinde yapılır, her yazım hemen commit edilir. WAL açılmaz.
- **Dosyalar:** `src/models.py`, `src/store.py`, `data/tenants/acme/rules.yaml`,
  `data/tenants/globex/rules.yaml`, `tests/conftest.py`, `tests/test_store.py`
- **Doğrulama:** Testler: *Kayıt uygulama üzerinden değiştirilemez*; *Tenant izolasyonu* (store
  düzeyi: `globex` store'u `acme`'nin incelemesini, audit kaydını, PII eşlemesini görmez); bilinmeyen
  tenant / `../` yolu reddedilir; kurallar yüklenir (`severity` varsayılanı `normal`, `check` isteğe
  bağlı, bilinmeyen `op` / birim reddedilir);
  paralel yazım: 8 thread aynı tenant'a 200'er audit kaydı yazar → tam 1600 satır, hiçbir thread
  hata almaz, her thread'in kayıtları kendi sırasındadır.
- **Commit:** `feat: add models, tenant rule sets and tenant-scoped store`

### [x] Adım 4 — Hazırlık: madde bölme ve enjeksiyon tarama
- **Ne:** `Madde N` başlıklarına göre bölme (yoksa paragraf), madde kimliği `c<n>` ve asıl metindeki
  ofset. Enjeksiyon taraması: TR/EN kalıplar + görünmez karakterler; işaret maddeyi, ifadeyi ve asıl
  metne göre konumu taşır. Modelin kopyası için yalnızca görünmez karakterleri çıkaran fonksiyon.
- **Ortak konum eşleme yardımcısı (`src/text.py`):** verilen adımlarla (görünmez karakter silme,
  NFKC, boşluk tekilleştirme, tırnak / tire eşitleme) metni normalleştirir ve normalleştirilmiş
  metindeki her karakterin asıl metindeki konumunu döner. Burada yazılır ve enjeksiyon taraması onu
  kullanır; Adım 6'daki alıntı doğrulaması aynı fonksiyonu farklı adım kümesiyle çağırır. Konum
  eşleyen başka kod yazılmaz.
- **Dosyalar:** `src/text.py`, `src/prepare/__init__.py`, `src/prepare/clauses.py`,
  `src/prepare/injection.py`, `tests/test_text.py`, `tests/test_clauses.py`, `tests/test_injection.py`
- **Doğrulama:** Testler: madde bölme (başlıklı / paragraf / giriş ve imza bloğu); *Enjeksiyon*
  (kalıplar ve sıfır genişlikli karakterler işaretlenir, işaret madde + konum taşır; ifadenin içine
  gizli karakter konsa da yakalanır); içinde `</sozlesme_maddesi>` geçen madde işaretlenir (açılış
  etiketi, büyük harfli yazım ve araya boşluk ya da gizli karakter konmuş hali de); temizlenmiş kopyada görünmez karakter yok ama şüpheli cümle
  duruyor; yardımcı: normalleştirilmiş metindeki bir aralık asıl metinde doğru parçaya geri eşlenir
  (uzunluğu değiştiren karakterler dahil: birleşik harf, art arda boşluk, gizli karakter).
- **Commit:** `feat: split contracts into clauses and scan for prompt injection`

### [x] Adım 5 — Hazırlık: PII maskeleme
- **Ne:** Tek maskeleyici (`mask(text, mapping)`: TCKN + checksum, IBAN + mod-97, e-posta, önekli
  telefon, taraf / imza bloğundan kişi adı; aynı değer aynı yer tutucu; var olan eşlemeyle
  çağrılabilir) ve tek maske açma fonksiyonu (`unmask`).
- **Dosyalar:** `src/prepare/pii.py`, `tests/test_pii.py`
- **Doğrulama:** Testler: *PII* (geçerli TCKN maskelenir, checksum'ı tutmayan 11 hane maskelenmez,
  ham değer kalmaz); *Tutar, süre ve tarih maskelenmez* (dokümandaki tüm örnekler; iki telefon biçimi
  maskelenir); IBAN, e-posta, ad; mevcut eşleme + yeni TCKN → eşleme büyür, bilinen ad aynı yer
  tutucuyu alır; `unmask` geri çevirir.
- **Commit:** `feat: mask identity data with a reversible per-review mapping`

### [x] Adım 6 — Doğrulama katmanı
- **Ne:** `verify(findings, rules, clauses, injection_suspected, enabled=True)` saf fonksiyonu:
  alıntı eşleştirme (iki tarafta aynı normalleştirme, `casefold` yok; Adım 4'teki `src/text.py`
  yardımcısıyla, kendi kopyası yazılmaz; eşleşince `quote` sözleşmedeki parçayla değiştirilir),
  sayı / birim çıkarma (rakam +
  Türkçe sayı sözcükleri; süre birimleri; `%`; `TL`), `check` karşılaştırması, taslak
  `code_check`, on bir sinyalin tamamı, `enabled=False` davranışı.
- **Süre çevirme:** tek fonksiyon (Adım 1 madde 12): gün ↔ hafta ve ay ↔ yıl kesin; aileler arası
  aralık (ay 28–31, yıl 365–366 gün). `check`, belirsiz değer kontrolü, modelin `value`'suyla kıyas
  ve taslak kontrolü bu fonksiyonu kullanır; başka çevirme kodu yazılmaz.
- **Dosyalar:** `src/verify.py`, `tests/test_verify.py`, `src/prepare/pii.py` (yalnızca
  `PLACEHOLDER` adı), `docs/agent_design.md`, `docs/evals.md`
- **Doğrulama:** Testler: *Alıntı doğrulama*, *Sayıyı kod çıkarır*, *Belirsiz değer*, *Okunamayan
  değer atılmaz*, *Kritik kural*,
  *Taslak kontrolü*; ayrıca `missing_proposal`, `unverified_not_found`, `agent_escalated`,
  `no_finding`, `injection_suspected` ve `enabled=False` (yalnızca `agent_escalated` + `no_finding`).
  Saklanan alıntı sözleşmedeki parçadır: model alıntıyı farklı boşluk ve tırnakla yazınca doğrulama
  geçer ve `quote` madde metninin birebir alt dizgisi olur (modelin yazdığı değil). Sayı o parçadan
  çıkarılır: modelin metni ile sözleşme parçası normalleştirme öncesinde farklı olsa da kod
  sözleşmedeki değeri okur. Doğrulanamayan alıntı değiştirilmeden kalır.
  Süre çevirme: "12 ay" ile "en az 1 yıl" kuralı kuyruğa düşmez; "on iki (12) ay, yani bir yıl" tek
  değerdir; "30 gün … bir ay" tek değerdir (`ambiguous_value` yok), ama "en az 30 gün" kuralında
  `unit_conversion_uncertain` ile kuyruğa düşer (ayın alt ucu 28); "60 gün … iki ay" aynı kuralda
  kuyruğa düşmez (56–62, tüm uçlar geçer); "1 ay" ile "en az 30 gün"
  `unit_conversion_uncertain` ile kuyruğa düşer; "6 ay" ve "30 gün" aynı kuralda düşmez; "en az 30
  gün" kuralına karşı "1 ay" yazan taslak `code_check=failed` olur (`proposal_noncompliant`).
  *Kritik kural* testi yeni haliyle: `normal` kuralın `not_found` sonucu da `critical` kuralınki de
  kuyruğa düşer; `critical` olanda ek sebep `critical_not_found`; `enabled=False` iken ikisi de düşmez.
- **Commit:** `feat: add deterministic verification and lawyer-queue signals`

### [x] Adım 7 — LLM kurulumu
- **Ne:** `src/llm.py`: sohbet modeli (`init_chat_model`; Groq'a ve Ollama'ya özel parametreler
  yalnızca o sağlayıcı seçiliyken), sağlayıcı hatasını sınıflandıran yardımcı
  (`classify_provider_error`, üç sınıf): `config` (Groq 401 `AuthenticationError`, 403
  `PermissionDeniedError`, 404 `NotFoundError`; Ollama `ResponseError` ve `status_code == 404`;
  `review_rule` yeniden fırlatır, inceleme `failed`, API 502), `rate_limit` (`groq.RateLimitError`;
  `no_finding(rate_limited)`), `other` (zaman aşımı, 5xx, 400, bağlantı reddi (`httpx.ConnectError`
  ya da `ConnectionError`), sağlayıcıdan gelmeyen her hata; `no_finding(llm_error)`). Embedding kurulumu yoktur.
  Sağlayıcıya özel her şey bu dosyada kalır. Testin sahte HTTP taşıyıcısı verebilmesi için
  `build_chat_model(settings, *, http_client=None)` alır; `http_client` yalnızca Groq içindir:
  `ollama` ile `None` olmayan değer `ValueError` fırlatır (`ChatOllama` onu sessizce yok sayıp gerçek
  ağa çıkardı).
- **Dosyalar:** `src/llm.py`, `tests/test_llm.py`
- **Doğrulama:** Testler: *Yeniden deneme sınırlı* (her isteğe 429 dönen `httpx.MockTransport` → tam
  4 istek, sonra `groq.RateLimitError`; sahte 429 yanıtı `retry-after-ms: 1` başlığını taşır,
  yoksa ya da `retry-after: 0` ise SDK üstel beklemeye düşer ve test ~3,5 sn sürer); Groq
  parametreleri, hem model alanlarında hem istek gövdesinde (`temperature` istekte en fazla `1e-8`
  olur, çünkü `ChatGroq` 0'ı `1e-8`'e çevirir; `max_tokens=2048`, `reasoning_effort="low"`,
  `max_retries=3`, 60 sn; `reasoning_format` yok); `ollama` ayarıyla Groq anahtarı olmadan model
  kurulur (`temperature=0`, `base_url`, `num_ctx`, `reasoning=False`, `seed`, 120 sn); `ollama` ile
  `http_client` verilirse `ValueError`; 401 yeniden denenmez (tam 1 istek); hata sınıflandırma:
  Groq 401 / 403 / 404 ve Ollama 404 `config`, 429 `rate_limit`, 400, 5xx, zaman aşımı, Ollama 500,
  bağlantı reddi ve sağlayıcı dışı hata `other`.
- **Commit:** `feat: add provider-switchable chat model`

### [x] Adım 8 — İndeks ve `prepare_contract`
- **Ne:** BM25 indeksi (rank-bm25; embedding yok) ve Adım 1'de tanımlanan tokenizer (küçük harf →
  her sözcüğün 3, 4 ve 5 harflik başlangıçları; kodda sözcük listesi yok). `(tenant_id, review_id)` anahtarlı süreç içi kayıt (koy / al / sil). `prepare_contract()`: Adım 1'de yazılan sırayla asıl metni kaydeder, böler,
  tarar, maskeler, indeksler; her adımı audit log'a yazar. Testlerde ve Adım 12'deki elle denemede
  kullanılacak örnek sözleşme (PII + gizli talimat içeren) burada eklenir.
- **Dosyalar:** `src/prepare/index.py`, `src/prepare/__init__.py`,
  `tests/fixtures/sample_contract.md`, `tests/test_index.py`, `tests/test_prepare.py`
- **Doğrulama:** Testler: arama doğru maddeyi getirir (en fazla 5 madde, 300 karakter özet); sorgu ile
  madde şu çiftlerde ortak anahtar bulur: "süre" ↔ "süresi", "ceza" ↔ "cezası", "gün" ↔ "günlük",
  "bildirim" ↔ "bildirimle", "fesih" ↔ "feshedebilir", "borç" ↔ "borcu"; bilinen sınır da testle
  sabitlenir ("ret" ↔ "reddi" eşleşmez); *Şüpheli ifade silinmez* (saklanan metin bayt bayt aynı; model kopyasında görünmez
  karakter yok, şüpheli cümle duruyor); audit'te `review_started`, `clauses_split`, `pii_masked`,
  `index_built` (+ `injection_flagged`); maskeli maddelerde ve audit'te ham PII yok (şüpheli ifadenin
  içindeki dahil); `pii_map` yalnızca tenant'ın `app.db`'sinde.
- **Commit:** `feat: add BM25 clause index and contract preparation`

### [x] Adım 9 — Tool'lar ve kural agent'ı
- **Ne:** 5 tool (`search_contract`, `get_clause`, `submit_finding`, `propose_change`,
  `escalate_to_lawyer`); bağlam `ToolRuntime.context`'ten; sözleşme metni yalnızca
  `<sozlesme_maddesi>` etiketli tool sonucu olarak. `create_agent` + system prompt dosyası + iki audit
  middleware'i (`model_call`, `tool_call`) + `ModelCallLimitMiddleware(run_limit=8)`. Testler için
  betikli sahte model `tests/conftest.py` içinde: `GenericFakeChatModel`'in `bind_tools` ekleyen küçük
  bir alt sınıfı (`GenericFakeChatModel` `bind_tools`'u kendisi desteklemez; `create_agent`'a olduğu
  gibi verilirse `NotImplementedError` fırlatır).
- **Adım limitini ayırt etme:** `exit_behavior="end"` bir bayrak ya da hata vermez; yalnızca sona
  yapay bir `AIMessage` ekler ve sayaçları çıktıda göstermez. Limit 9. çağrıyı engellediği için
  sonuçtaki `AIMessage` sayısı yalnızca o zaman `RUN_LIMIT`'i (8) aşar: sayı 8'den büyükse limit
  devreye girmiştir (sayaç tutulmaz; tam 8. çağrıda kendiliğinden biten koşu 8 mesaj verir).
  Kütüphanenin mesaj metnine bakılmaz.
- **Koşu kendiliğinden bitmediyse** (`agent_design.md` §6): `submit_finding` yoksa `no_finding`
  (`step_limit`; limit yoksa `no_submit`); varsa bulgu korunur, `interrupted_by` dolar ve
  `verify` `run_interrupted` ile her zaman kuyruğa sokar. Aynısı bulgu bildirildikten sonra gelen
  LLM hatası için geçerlidir: `run_rule_agent` senkron `stream(stream_mode="values")` ile koşar,
  hata gelirse son state'i taşıyan `RuleRunInterrupted`'ı fırlatır; sınıflandırma Adım 10'dadır.
  Bunun için `Finding.interrupted_by` alanı, `run_interrupted` sebebi ve `verify.py`'de tek satırlık
  sinyal bu adımda eklenir.
- **Checkpoint:** `create_agent(..., checkpointer=False)`; yoksa agent Adım 10'da üst graph'ın
  `SqliteSaver`'ını devralır.
- **Sahte model:** betikte istisna öğesi, gelen mesajların kaydı, kural metnine göre anahtarlı ve
  kilitli betikler (Adım 10'un paralel testleri aynı sahte modeli kullanır).
- **Dosyalar:** `src/tools.py`, `src/agent.py`, `src/prompts/rule_agent.md`, `src/models.py`,
  `src/verify.py`, `tests/conftest.py`, `tests/test_tools.py`, `tests/test_agent.py`,
  `tests/test_verify.py`
- **Doğrulama:** Testler: *Onay yok* (derlenmiş agent'ın tool düğümünde tam olarak bu 5 ad); tool
  açıklamaları `agent_design.md` §2'deki metinle aynı, şemada tenant / inceleme / kural alanı yok;
  olmayan madde → hata mesajı modele döner ve koşu sürer; alıntısız `compliant` → sebebi dolu hata
  mesajı (`status="error"`), state'e yazılmaz, önceki geçerli bulgu silinmez; kapanış etiketi
  içeren madde iki okuma tool'undan da olduğu gibi döner; son `submit_finding` geçerli (aynı
  mesajda iki çağrı da çökmez; satır sırası değil sayısı doğrulanır); betikli koşuda bulgu + taslak;
  audit'te `model_call` (system prompt + mesajlar + `usage_metadata`) ve `tool_call` (durum, sonuç,
  `search_contract`'ta tüm `clause_ids`) var; system prompt'ta sözleşme metni yok, sözleşme metni
  modele yalnızca etiketli tool sonucunda gider; 8 çağrı sınırı (8. çağrıda biten koşu `no_submit`,
  8 tool çağrısı `step_limit`; ikisinde de tam 8 `model_call`); bulgudan sonra limit ve bulgudan
  sonra model hatası → bulgu korunur ve işaretlenir, `verify` `run_interrupted` ekler; `no_finding`
  bulgusuna taslak ve devretme eklenmez; yalnızca bozuk tool çağrısı → `no_submit`, audit'te
  görünür; model hatası `model_call` satırına `error` ile yazılır; audit yazım hatası iki kancadan
  ayrı ayrı (TEMP TRIGGER) dışarı fırlar; aynı derlenmiş agent art arda iki koşuda birbirini
  etkilemez; `SqliteSaver`'lı üst graph içinde koşan agent checkpoint'e alt ad alanı yazmaz.
- **Commit:** `feat: add rule agent with five tools and audit middleware`

### [x] Adım 10 — Graph: dağıtım, doğrulama, durma noktası
- **Ne:** `START → review_rule × N (Send) → verify → human_review (interrupt)`; tenant başına
  derlenmiş graph + `SqliteSaver` (önbellek `(çözülmüş data_dir, tenant_id)` ile anahtarlanır; graph
  kurucusu `Settings`'i parametre alır; tenant klasörü `Store`'un doğrulamasından gelir;
  `close_graphs()` testler için); `max_concurrency` config anahtarı; invoke config'i ve başlangıç
  state'i tek yerde kurulur. `review_rule` hata eşlemesi **kaynağa göredir**: model çağrısını saran
  middleware hatanın satırını yazdıktan sonra `ModelCallFailed` fırlatır; `run_rule_agent` yalnızca
  onu `RuleRunInterrupted`'a çevirir (`__cause__` sağlayıcının asıl hatası); `review_rule` onu
  `classify_provider_error` ile sınıflandırır (`config` → yeniden fırlat, `rate_limit` →
  `rate_limited`, `other` → `llm_error`) ve bulguyu `interrupted_finding` ile kurar (bulgu
  bildirilmişse korunur + `interrupted_by`, yoksa `no_finding`); `no_submit` ve `step_limit`
  `run_rule_agent`'tan hazır gelir. Başka her hata (tool gövdesi, middleware, kütüphane,
  `sqlite3.Error`, eksik indeks) sarmalanmadan graph'tan fırlar, inceleme `failed` olur
  (`agent_design.md` §6). **Karar — state düz sözlük tutar:** bulgu, kural ve madde state'e
  `model_dump(mode="json")` ile girer, düğümler girişte doğrular; izin listesi tutulmaz (pydantic
  nesnesi checkpoint'ten okunurken "kayıtsız tip" uyarısı loglanıyordu). `verify` düğümü bulguları
  kural sırasına dizer ve kural başına tek bulgu olduğunu denetler. `review_rule` `finding`
  (`agent`), `verify` düğümü bulgu başına `verification` (`system`, tek transaction'da) olayını
  audit log'a yazar. (`finalize` Adım 13'te eklenir.)
- **Karar — graph senkron çalışır:** yalnızca `graph.invoke` / `agent.invoke`, senkron düğümler ve
  senkron middleware kancaları (`wrap_model_call`, `wrap_tool_call`) kullanılır. `ainvoke`, `astream`
  ve `awrap_*` kancaları yazılmaz. Sebep: `SqliteSaver` async çağrıları desteklemez (`aget_tuple`,
  `alist`, `aput` `NotImplementedError` fırlatır); async için ayrı sınıf `AsyncSqliteSaver` gerekir ve
  dokümanlar senkronu seçmiştir (`code_standards.md` "Async / eşzamanlılık"). Paralellik yalnızca
  `Send` + `max_concurrency` ile, thread havuzunda olur; `SqliteSaver` bağlantıyı kendi kilidiyle korur.
- **Bağlantı:** checkpoint bağlantısı `check_same_thread=False` ile açılır:
  `SqliteSaver(sqlite3.connect(path, check_same_thread=False))` (`agent_design.md` §1). Dallar ve
  endpoint'ler farklı thread'lerde çalıştığı için bu olmadan SQLite `ProgrammingError` fırlatır.
- **Dosyalar:** `src/graph.py`, `src/agent.py` (`ModelCallFailed` işareti), `tests/test_graph.py`,
  `tests/test_agent.py`, `tests/conftest.py` (`close_graphs()` fixture'ı), `docs/agent_design.md`,
  `docs/code_standards.md`, `docs/spec.md` (§10'daki `max_concurrency` sorusu
  kapatılır), `docs/evals.md`, `docs/tech_stack.md`, `CLAUDE.md`
- **Doğrulama:** Testler (betikli sahte model): graph `interrupt`'a kadar koşar, state'te `findings`
  ve `verified` var; audit'te `finding` ve `verification`; *Hız limiti → `rate_limited`* (gerçek
  `groq.RateLimitError`, diğer kurallar etkilenmez); `config` sınıfı hata graph'tan dışarı fırlar (bağlantı reddi fırlamaz, `llm_error` olur); *Maskesiz
  metin modele gitmez* (`groq` ve `ollama` ayarıyla); checkpoint'te ham PII yok; aynı anda çalışan
  `review_rule` sayısı `MAX_CONCURRENCY`'yi aşmaz; paralel koşuda (`MAX_CONCURRENCY` > 1) audit'teki
  `model_call`, `tool_call` ve `finding` sayıları betikten beklenenle birebir aynıdır; audit yazımı
  hata verirse graph'tan dışarı fırlar, bulgu `no_finding`'e çevrilmez; `src/` altında `async def` / `ainvoke` yok.
  Ek testler: *Beklenmeyen kod hatası incelemeyi `failed` yapar* (tool gövdesinden sağlayıcı dışı
  hata bulguya çevrilmez, graph'tan aynı tiple fırlar; aynı tip model çağrısından gelince
  `llm_error`); `RuleRunInterrupted.__cause__` işaret değil asıl hatadır; hata satırı yazılamazsa
  `sqlite3.Error` fırlar; dallar farklı sırada bitse de `verified` kural sırasındadır; kural başına
  bulgu sayısı bir değilse `verify` düğümü fırlatır; `finding` satırı `agent`, `verification`
  satırları `system` aktörlüdür ve bulgu başına birdir (yük: `rule_id`, `needs_review`,
  `review_reasons`, `code_check`); checkpoint'ten okunan state'te pydantic nesnesi yok ve okuma
  "kayıtsız tip" uyarısı loglamaz; `interrupt`'tan sonra `get_state(config).next ==
  ("human_review",)`; eksik indeks graph'tan fırlar; bilinmeyen tenant'la graph kurulamaz
  (`UnknownTenantError`).
- **Commit:** `feat: add review graph with per-rule fan-out and single interrupt`

### [x] Adım 11 — API: inceleme başlatma ve okuma
- **Ne:** `get_principal` (anahtar → tenant + rol + ad) ve `require_lawyer` bağımlılıkları;
  `POST /reviews`, `GET /reviews/{id}`, `GET /reviews/{id}/audit`; role göre gösterim (maske açma
  yalnızca yanıt üretilirken, `lawyer` için); graph döndükten sonra `awaiting_lawyer` yazımı ve
  indeksin silinmesi; `config` sınıfı hatada `failed` + `review_failed` + HTTP 502. FastAPI'nin otomatik `/docs`
  uçları kapatılır (anahtarsız uç kalmasın).
- **Graph'tan çıkan diğer hatalar (Adım 10 notu):** `config` sınıfı olmayan her hata (kod hatası,
  `sqlite3.Error`) da incelemeyi `failed` yapar, `review_failed` yazılır ve HTTP 500 döner.
  `graph.invoke` başlamış her dal bitmeden dönmediği için `review_failed` son satırdır (hatadan
  sonra sıradan birkaç dal daha başlayıp bitmiş olabilir; satırları audit'te kalır); `failed`
  incelemenin thread'i bir daha çağrılmaz. Invoke config'i ve başlangıç state'i graph modülündeki
  tek yardımcılardan alınır.
- **Adım 10'un bıraktığı arayüz (`src/graph.py`):** `get_review_graph(settings, tenant_id)` tenant'ın
  derlenmiş graph'ını verir (bilinmeyen tenant → `UnknownTenantError`); `initial_state(prepared,
  rules)` girdiyi kurar (boş kural seti → `ValueError`); `review_config(settings, review_id)`
  `thread_id` ve `max_concurrency`'yi taşır. `graph.invoke(...)` `interrupt`'ta döner: sonuçta
  `verified` (kural sırasında, düz sözlük; `Finding` olarak doğrulanıp okunur), ham `findings` ve
  `__interrupt__` vardır; `get_state(config).next == ("human_review",)`. Graph indeksi silmez ve
  `awaiting_lawyer` / `review_failed` yazmaz; ikisi de API'nindir. **Testlerde sahte model:**
  istekten önce `get_review_graph(settings, tenant_id, model=sahte)` çağrılır; API'nin modelsiz
  çağrısı önbellekteki aynı graph'ı alır (önbellek anahtarı sağlayıcıyı / modeli içermez; başka
  modelle istemek `ValueError`). `tests/conftest.py`'deki autouse fixture her testten sonra
  `close_graphs()` çağırır. Betikli sahte modelin içindeki bir test hatası (tükenen betik, hata
  veren kanca) model çağrısı hatası sayılır ve `llm_error` bulgusuna döner; beklenmeyen
  `llm_error`'a karşı `tests/test_graph.py`'deki `_assert_no_failed_rule` gibi bir denetim konur.
- **Giriş noktası:** modül düzeyinde `app` yoktur. `create_app(settings: Settings) -> FastAPI`
  uygulamayı kurar; `python -m src.api` önce `load_settings_or_exit()` çağırır, sonra
  `uvicorn.run(create_app(settings))`. Testler `create_app(load_settings(env={...}))` ile kurar.
- **Dosyalar:** `src/api.py`, `tests/test_api.py`, `src/store.py` (`save_findings` atomik),
  `tests/test_store.py`, `docs/architecture.md`, `docs/agent_design.md`, `docs/code_standards.md`,
  `docs/evals.md`, `docs/tech_stack.md`, `CLAUDE.md`
- **Doğrulama:** Testler: *Tenant izolasyonu* (404), *Tenant parametresi yok sayılır*, *Tüm yazma
  uçları `lawyer` ister* ve *Anahtar yok / tanınmıyor* (ikisi de rota listesini dolaşır; sonradan
  eklenen uçları kendiliğinden kapsar), *Kimlik / yapılandırma hatası* (`failed`, 502,
  `review_failed`), *Role göre gösterim* (inceleme + audit), *Şüpheli ifade silinmez* (`lawyer`
  yanıtında işaret, ifade, konumlar).
- **Commit:** `feat: add review API with key-based tenant and role resolution`

### [x] Adım 12 — Docker Compose ve ilk gerçek model denemesi
- **Ön koşul (kullanıcı):** `.env.example`'dan `.env` oluşturulur; `GROQ_API_KEY` ve
  `TENANT_API_KEYS` dolu olmalı.
- **Ne:** `Dockerfile` (`python:3.12-slim` + uv), `docker-compose.yml` (`app`; `app`'te `restart`
  yok; `./data` ve `./evals` host'tan bağlanır, eval sonuçları host'ta kalır). `ollama` ve
  `ollama-pull` yalnızca `offline` compose profilinde çalışır; Groq modunda başlamaz ve imajları
  inmez. Çevrimdışı moda geçiş `.env`'de iki değişkendir: `LLM_PROVIDER=ollama` ve
  `COMPOSE_PROFILES=offline`; komut yine `docker compose up`. `ollama-pull` sohbet modelinin adını
  `src/config.py`'den alır (model adı compose dosyasına yazılmaz). İlk kez gerçek modelle bir
  sözleşme denenir.
- **Dosyalar:** `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, `docs/spec.md`,
  `src/config.py` (`ollama-pull` için çözülmüş model adını basan küçük giriş), `tests/test_config.py`
- **Doğrulama:** `docker compose up` → yalnızca `app` başlar (Ollama kapsayıcısı ve imajı yok);
  `tests/fixtures/sample_contract.md` ile `POST /reviews` bulguları döner. `GROQ_API_KEY` boşken `app`
  açık hatayla, sıfır olmayan kodla çıkar ve döngüye girmez. `LLM_PROVIDER=ollama` +
  `COMPOSE_PROFILES=offline` ile aynı istek çalışır (donanım yetmezse `tech_stack.md`'deki yedek
  model). İnen boyutlar iki mod için ölçülür. Gözlemler (Türkçe tool çağrısı, sözleşme başına token)
  `spec.md` §10'a işlenir.
- **`checkpoints.db` WAL (Adım 10 notu):** `SqliteSaver.setup()` dosyayı WAL'a alır ve dosya
  host'tan bağlanan `./data`'dadır; gerçek bind mount'ta denenir. Sorun çıkarsa: bağlantı
  `SqliteSaver`'a verilmeden önce `PRAGMA locking_mode=EXCLUSIVE` ya da `setup()`'tan sonra
  `journal_mode=DELETE`.
- **Commit:** `chore: add docker compose with offline ollama profile`

### [ ] Adım 13 — Avukat kararı ve finalize
- **Ne:** `LawyerDecision` şeması; `POST /reviews/{id}/decision` (kontrol sırası 401 → 403 → 404 →
  409 → 422, ilk dördü bağımlılıklarda; zorunluluk kontrolü resume'dan önce; `edited_text` aynı
  maskeleyiciden geçer; kararı verenin adı anahtardan). Graph'a `finalize` düğümü: bulguların son
  sonucunu hesaplar, `reviews` kaydını tamamlar, karar olaylarını + `report_created` +
  `review_closed` yazar.
- **Dosyalar:** `src/models.py`, `src/api.py`, `src/graph.py`, `tests/test_api.py`,
  `tests/test_graph.py`
- **Doğrulama:** Testler: *`viewer` karar veremez*, *Gövdedeki rol/ad yok sayılır*, *Avukat kararı —
  zorunlu alanlar*, *— geçersiz değerler*, *Son karar* (durum `approved` / `changes_requested`),
  *Düzenlenmiş metin maskelenir* (`reviews` kaydı, graph state'i, audit), *Kayıt eksiksiz* (baştan
  sona tüm olaylar); kapanmış incelemeye ikinci karar 409, başka tenant'ın incelemesine karar 404;
  reddedilen isteklerde durum ve audit değişmez.
- **Commit:** `feat: add lawyer decision endpoint and finalize step`

### [ ] Adım 14 — Rapor
- **Ne:** `src/report.py` saf fonksiyonu (saklanan son sonuçtan üretir; DB'ye yazmaz);
  `GET /reviews/{id}/report` (karardan önce 409; role göre gösterim).
- **Dosyalar:** `src/report.py`, `src/models.py`, `src/api.py`, `tests/test_report.py`,
  `tests/test_api.py`
- **Doğrulama:** Testler: *Rapor* (karardan önce 409; kural sonuçları, taslaklar, talep edilen
  değişiklikler; kabul edilen ve düzenlenen listede, reddedilen değil; okumak audit'e satır eklemez);
  kararların rapora yansıması (düzeltilen bulgunun son sonucu, "karar verilmedi",
  `approved_with_noncompliant=true`); *Role göre gösterim* (rapor: `viewer` maskeli ve konumsuz,
  `lawyer` maskesiz — düzenlenmiş metin dahil).
- **Commit:** `feat: add JSON review report`

### [ ] Adım 15 — Arayüz
- **Ne:** Tek statik sayfa: anahtar girilir, inceleme API'den çekilir; alıntılar ve enjeksiyon
  işaretleri sözleşme metni üzerinde gösterilir. Karar arayüzden verilmez.
  Konumlar Python karakter (kod noktası) ofsetidir; BMP dışı karakterler (ör. `U+E0000` etiket
  karakterleri) JavaScript'te iki birim sayıldığı için sayfa metni kod noktalarına göre dilimler
  (`Array.from(text)`).
- **Dosyalar:** `src/static/index.html`, `src/api.py` (`GET /`), `tests/test_api.py`
- **Doğrulama:** Test: `GET /` anahtarsız 200 döner ve veri içermez. Elle: tarayıcıda `lawyer`
  anahtarıyla maskesiz, `viewer` anahtarıyla maskeli görünüm.
- **Commit:** `feat: add single-page UI showing findings on the contract text`

### [ ] Adım 16 — Eval dataset'i
- **Ne:** `evals.md` §3'teki 7 sentetik sözleşme ve `labels.json`. Etiketleri commit'ten önce
  kullanıcı gözden geçirir (CASE.md: "etiketleri kendin kontrol et"). Sözleşme 4 gerçek bir dolaylı
  ifade içerir: kuralın sözcüklerini kullanmadan konuyu düzenleyen bir madde.
  Tokenizer'a bakılarak yazılmaz; aramada çıkıp çıkmadığı denenip metin
  buna göre düzeltilmez.
- **PII etiketleme kuralları (sözleşme 5 başta, tüm sözleşmeler):**
  1. Bir değer, maskeleyicinin onu yakalayıp yakalamadığına göre değil, ne olduğuna göre etiketlenir.
  2. Sözleşme 5 regex'lere bakılmadan, birkaç gerçekçi biçim bozukluğuyla yazılır: öneksiz telefon,
     boşluklu TCKN, madde gövdesinde geçen ad, etiketsiz imza satırı.
  3. Garanti edilmeyen her kimlik bilgisi `pii_best_effort` listesine girer; sızsa da orada kalır.
  4. Garanti edilen türden, standart biçimde bir değer sızarsa maskeleyici düzeltilir (kendi `fix:`
     commit'i ve testiyle). Testi geçirmek için değer öbür listeye taşınmaz, sözleşmedeki yazımı da
     değiştirilmez.
- **Dosyalar:** `evals/dataset/contracts/01…07_*.md`, `evals/dataset/labels.json`,
  `tests/test_dataset.py`
- **Doğrulama:** Tutarlılık testi (LLM yok): her etiketin dosyası var; `rule_id`'ler tenant'ın kural
  setiyle birebir; `clause_no` sözleşmede var; `pii_values` ham metinde var, maskeli metinde yok;
  `pii_best_effort` ham metinde var (maskeli metin şartı aranmaz) ve sözleşme 5'te boş değil;
  sözleşme 6 taramada işaretlenir, 7 işaretlenmez; sözleşme 3'te beklenen `not_found` kurallar var (en az biri `critical`, en az biri `normal`).
- **Commit:** `test: add synthetic contract dataset with expected findings`

### [ ] Adım 17 — Eval metrikleri ve koşucu
- **Ne:** `evals/metrics.py` (`evals.md` §4 metrikleri ve §5 tablosunun satırları, saf fonksiyonlar).
  `evals/run.py`: geçici bir `DATA_DIR`'de her sözleşme için hazırlık + graph'ı **bir kez**
  `interrupt`'a kadar çalıştırır; ham bulgularla `verify`'ı kapalı ve açık hesaplar; önce/sonra
  tablosunu basar; sonucu (sağlayıcı, model, tarih ile) `evals/results/` altına yazar; `rate_limited`
  varsa metrik basmadan hatayla çıkar. LangSmith `evaluate()` iki kez, aynı önbellekten; anahtar
  yoksa `upload_results=False`. Birim fiyatlar `src/config.py`'de sağlayıcı varsayılanlarının yanında durur.
- **Sözleşme sayısı sınırı:** `python -m evals.run --limit N` yalnızca ilk N sözleşmeyi koşar (düşük
  limitli Groq hesabı için; varsayılan hepsi). Tablo ve sonuç dosyası kaç sözleşmenin koşulduğunu
  yazar; koşulmayan sözleşmelere bağlı metrikler (ör. enjeksiyon: sözleşme 6–7) "ölçülmedi" görünür.
- **Kritik kural yakalama:** önce/sonra tablosunda yer almaz (her `not_found` kuyruğa gittiği için
  artık ayırt edici değil); metrik listesinde bu notla raporlanır.
- **Gereksiz yönlendirme:** kuyruğa düşen bulgulardan `status`'ü etiketle aynı olanların oranı;
  önce/sonra tablosunda yönlendirme oranının hemen altında bir satırdır.
- **Garanti edilmeyen PII sızıntısı:** `pii_best_effort` listesinden `model_call` kayıtlarında ya da
  maskeli maddelerde geçenlerin sayısı ve toplamı ("M / N"); eşik yok. `pii_values` satırı ayrı kalır
  (eşik 0, bekçi).
- **Arama isabeti:** beklenen maddenin `search_contract` sonuçlarında en az bir kez döndüğü
  kuralların oranı; audit'teki `tool_call` kayıtlarından kodla hesaplanır, tabloda bir satırdır.
  Sonuç dosyası kural bazında satır da içerir (beklenen, gelen, kuyruk sebepleri, arama isabeti);
  böylece bir kaçışın aramadan mı modelden mi geldiği görülür.
- **Dosyalar:** `evals/__init__.py`, `evals/metrics.py`, `evals/run.py`, `src/config.py`,
  `tests/test_metrics.py`, `docs/spec.md` (§10'daki `evaluate()` sorusu; yedek seçilirse
  `docs/tech_stack.md` de)
- **Doğrulama:** Testler: elle verilen satırlarla her metrik ("geçersiz alıntıyla kabul edilen bulgu"
  ve "gereksiz yönlendirme" dahil, iki modda; arama isabeti elle verilen `tool_call` kayıtlarıyla); `rate_limited` → sıfır
  olmayan çıkış; `--limit 3` yalnızca ilk 3 sözleşmeyi
  koşar, tabloda "3/7" ve ölçülmeyen metrikler "ölçülmedi" yazar. Komut:
  `docker compose run --rm app python -m evals.run` önce/sonra tablosunu basar. Anahtarsız
  `evaluate()` çalışmazsa `spec.md` §10'daki yedek (aynı evaluator'larla düz döngü) kullanılır.
- **Commit:** `feat: add one-command eval with before/after verification table`

### [ ] Adım 18 — İlk eval koşusu ve kalibrasyon
- **Ne:** Eval Groq üzerinde koşulur; sonuçlar kaydedilir; eşikler gerçek sonuca göre `evals.md`'de,
  açık sorular `spec.md` §10'da güncellenir. Bulunan her hata önce teste dönüşür ve kendi `fix:`
  commit'iyle düzeltilir; adımın kendi commit'i sonuçlardır. Arama sözleşme 4'teki dolaylı ifadeyi
  kaçırırsa bu hata sayılıp düzeltilmez: sözleşme ve tokenizer olduğu gibi kalır,
  sonuç (arama isabeti ve kaçan kural) Adım 19'da README'ye sınır olarak yazılır.
- **Dosyalar:** `evals/results/*`, `docs/evals.md`, `docs/spec.md`
- **Doğrulama:** Eval komutu hatasız biter; `evals.md` §8'deki eşikler (güvensiz hata, enjeksiyon
  sonucu, PII sızıntısı) sağlanır ya da sapma (ör. sözleşme 7) dürüstçe yazılır.
  `LANGSMITH_API_KEY` varsa bir koşuda dataset ve iki experiment (`before`, `after`) görünür; yoksa
  bu yolun denenmediği README'ye yazılır.
- **Commit:** `docs: record first eval results and calibrated thresholds`

### [ ] Adım 19 — README ve karar dokümanı
- **Ne:** README (kurulum, eval sonuçları ve önce/sonra tablosu, düşük Groq limiti notu, bilinen
  sınırlar, "daha fazla vaktim olsaydı"). Karar dokümanı: CASE.md'deki 5 soru sırasıyla, en fazla 2 sayfa.
- **README — ücretsiz Groq anahtarıyla:** tam eval'in günlük limite sığmadığı ve çalışacak komut
  açıkça yazılır: `docker compose run --rm -e MAX_CONCURRENCY=1 app python -m evals.run --limit 3`.
  N değeri Adım 18'de ölçülen sözleşme başına token'a göre kesinleşir. Yanında iki seçenek daha:
  limitsiz tam koşu için `LLM_PROVIDER=ollama`, koşmadan görmek için `evals/results/`.
- **README — ilk çalıştırmada inen toplam:** her iki mod için ayrı yazılır. Adım 12'de ölçülen:
  varsayılan mod (Groq) yalnızca uygulama imajı, diskte 611 MB (sıkıştırılmış 140 MB). Çevrimdışı
  mod üstüne Ollama imajı 3,40 GB indirme (diskte 8,52 GB) + sohbet modeli: `qwen3.5:4b` 3,3 GB
  (ölçüldü), varsayılan `qwen3.5:9b` ~7 GB (tahmin, ölçülmedi). Çevrimdışı moda geçişin iki
  değişken istediği (`LLM_PROVIDER=ollama`, `COMPOSE_PROFILES=offline`) açıkça yazılır.
- **Karar dokümanı — 300 sayfa sorusu:** cevap "ilk kırılan arama olur; ilk eklenecek şey
  embedding'dir, ama tek başına yetmez" olacak. Yetmemesinin sebepleri de yazılır: kural başına tek
  madde ve tek alıntı, ilk 5 madde sınırı, 8 model çağrısı limiti, `Madde N` kalıbına uymayan iç içe
  numaralar ve ekler.
- **README ve karar dokümanı — LangSmith:** README'de ve karar dokümanındaki banka sorusunun
  cevabında aynı cümle geçer: çevrimdışı kurulumda LangSmith değişkenleri verilmez; uygulamada
  açılırsa gerçek sözleşmelerin maskeli trace'i dışarı gider. Karar dokümanına ayrıca şu not yazılır:
  "üretimde izleme açıkken uygulamanın başlamayı reddetmesi eklenirdi" (bu projede eklenmedi; koruma
  işletim kuralıdır).
- **README — bilinen sınır (kanıtlı ama yanlış "uygun"):** model alakasız ama sözleşmede gerçekten
  geçen bir alıntıyla "uygun" derse ve kuralda `check` yoksa, bulgu avukatsız geçer. Kod alıntının
  varlığını doğrular, konuyla ilgisini doğrulayamaz. Her `not_found`'u kuyruğa almak bunu kapatmaz.
- **README — PII:** iki sayı birlikte ve olduğu gibi yazılır: "maskelenmesi garanti edilenlerde
  sızıntı 0; garanti edilmeyen N değerden M'si modele gitti", hangi türlerin gittiğiyle. İlk sayının
  yapı gereği 0 olduğu, maskeleyicinin kapsamını ikinci sayının gösterdiği de söylenir.
- **README — arama sınırı:** arama yalnızca BM25'tir; arama isabeti sayısı ve sözleşme 4'teki dolaylı
  ifadenin bulunup bulunmadığı olduğu gibi yazılır. Kaçırdıysa "bilinen sınırlar"a, embedding
  "daha fazla vaktim olsaydı"ya girer.
- **Dosyalar:** `README.md`, `docs/decisions.md`
- **Doğrulama:** Temiz bir klonda README adımları izlenerek `docker compose up` ve eval çalışır;
  README'deki tablo `evals/results/` ile aynı; karar dokümanı 5 soruyu yanıtlar ve 2 sayfayı aşmaz.
- **Commit:** `docs: add README and decision document`

### [ ] Adım 20 — Teslim
- **Ne:** Kullanıcının onayıyla `origin`'e push edilir. AI oturumları bu planın dışındadır: ham
  oturumları ekleme ve secret maskeleme işini kullanıcı kendisi yapar.
- **Dosyalar:** —
- **Doğrulama:** Push'tan sonra GitHub'dan temiz klonda `.env` ve `*.db` dosyası yok; README adımları
  o klonda çalışır.
- **Commit:** yok (yalnızca push).

## CASE.md teslimatları → adımlar
| CASE.md | Adım |
|---|---|
| Agent: tool'ları sen tasarla | 9 |
| Agent: birebir alıntı; geçtiğini kod kontrol eder | 6 (10'da graph'a bağlanır) |
| Agent: emin olmadığını avukata bırakır; onay yalnızca insanın | 6, 9 (onay tool'u yok), 10, 13 |
| Agent: her tool çağrısı ve bulgu kayda yazılır | 3 (tablo), 9 (model / tool çağrıları), 10 (bulgu, doğrulama), 13 (kararlar) |
| Eval: en az 5 sentetik sözleşme + beklenen bulgular | 16 |
| Eval: metrikler ve gerekçeleri | 17 (gerekçe `evals.md` §4 ve README) |
| Eval: tek komut + önce/sonra | 17, 18 |
| Çalıştırma: `docker compose up`; API | 11, 12, 13, 14 |
| Çalıştırma: sağlayıcı env ile değişir | 2, 7, 12 |
| Çalıştırma: tenant ayrımı + bunu gösteren test | 3, 11 |
| Karar dokümanı (zorunlu) | 19 |
| Bonus: prompt injection savunması + ölçen testler | 4, 6, 16–18 |
| Bonus: bulguları metin üzerinde gösteren arayüz | 15 |
| AI oturumları (zorunlu) | Plan dışı; kullanıcı kendisi ekler |
| README (kurulum, eval sonuçları, "daha fazla vaktim olsaydı") | 19 |
| GitHub reposu | 20 (push) |

## Uçtan uca doğrulama (plan bitince)
1. `uv run pytest` · `uv run ruff check . && uv run ruff format --check .` · `uv run mypy src`
2. `docker compose up` → `POST /reviews` (lawyer) → `POST /reviews/{id}/decision` →
   `GET /reviews/{id}/report` ve `/audit`; aynı okuma uçları `viewer` anahtarıyla maskeli, karar ucu 403.
3. `docker compose run --rm app python -m evals.run` → önce/sonra tablosu.
