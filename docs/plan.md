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
  Limit sonradan konsoldan da doğrulandı (ücretsiz katman: 30 RPM, 1K RPD, 8K TPM, 200K TPD);
  dokümanlardaki "250K TPM teyit edildi" ifadeleri düzeltildi.
  Adım 17–18 için notlar: önce/sonra tablolu tam eval (tek geçiş ≈ 175K token; önce/sonra aynı
  ham bulgulardan hesaplanır) bu anahtarda bir güne ancak sığar, aşılabilir; iki güne bölünür,
  `--limit N` ile kısmi koşulur ya da çevrimdışı modda koşulur;
  `gpt-oss-120b` `get_clause` ve `propose_change`'i atlıyor (prompt). Adım 12 sonrası karar:
  `MAX_CONCURRENCY`'nin Groq varsayılanı 4'ten 1'e indirildi (`tech_stack.md`, `spec.md` §10).
  Adım 13 için notlar (Adım 12'den): (1) `create_app`'te artık bir `RequestValidationError`
  işleyicisi var; karar ucunun 422'si de ondan geçer ve yalnızca `type`, `loc`, `msg` taşır
  (`edited_text` geri yansıtılmaz). Zorunluluk kontrolü için elle üretilen 422 aynı biçimi
  korumalı. (2) İmaj `src/`'yi kopyalar, bağlamaz: kod değişince kapsayıcıda denemek için
  `docker compose up --build` gerekir. (3) Elle denemelerden `data/tenants/acme/` altında
  `awaiting_lawyer` durumunda dört inceleme duruyor (git dışı); thread'leri `human_review`'da
  bekliyor, karar ucunu gerçek veriyle elle denemek için kullanılabilir. (4) Örnek sözleşmede
  her bulgu `injection_suspected` taşır (iki maddedeki işaret beş bulguya da ekleniyor); *Son
  karar* testleri "avukatsız geçen bulgu" için ayrı, işaretsiz bir metin kullanmalı.
  Adım 13: `POST /reviews/{id}/decision`, `finalize` düğümü, `src/decision.py` (saf karar
  fonksiyonları) ve tek transaction'lı `Store.complete_review` yazıldı; kararlar (K1–K9) ve deney
  sonuçları aşağıda Adım 13 bölümünde. Adım 11'in "inceleme yanıtında karar alanı yok" kararı
  değişti: karar alanları `*View` modellerinde, karardan önce `null`. Uygulamayı alt-agent'lar yazdı
  (önce dokümanlar, sonra kod); plan üç, kod üç bağımsız incelemeden geçti. Kod incelemesinin iki
  bulgusu düzeltildi: (1) `complete_review` `False` dönünce gelen 409'da `pii_map` girdisi ve
  `next == ("finalize",)` kalır; dokümanlar ve test buna göre düzeltildi; (2) eşzamanlı iki karar
  testi olay tabanlı yazıldı (kilit kaldırılınca 10/10 kalıyor). Elle deneme Docker'da değil,
  `data/`'nın bir kopyasında `TestClient` ile yapıldı: eski topolojiyle bekleyen gerçek bir inceleme
  yeni graph'la kapandı (403 / 422 / 200 / ikinci karar 409, audit sırası beklenen gibi);
  `data/tenants/acme/` altındaki dört inceleme hâlâ `awaiting_lawyer`. Taslaklı bulguda `edit`
  yolu gerçek veride denenmedi (o incelemede taslak yoktu); testlerle kapsanıyor.
  Adım 14 için notlar: rapor `ReviewRecord.findings` + `ReviewRecord.decision` (`ReviewOutcome`:
  kural başına `severity`, `final_status`, `action`, `proposal_action`, maskeli `edited_text`)
  üzerinden, `rules.yaml`'a bakmadan üretilir; `approved_with_noncompliant` ve talep edilen
  değişiklikler saklanmaz, raporda türetilir; kayda bakan 422'lerde girdisi hiç gönderilmemiş
  bulgunun `loc`'u indeks yerine `rule_id` taşır.
  Adım 14: `src/report.py` (saf `build_report` / `build_lawyer_report`), rapor modelleri ve
  `GET /reviews/{id}/report` yazıldı; kararlar (R1–R8) aşağıda Adım 14 bölümünde. Dokümanları ana
  oturum, kodu bir alt-agent yazdı; kod iki bağımsız incelemeden (doküman uyumu, güvenlik / rol
  gösterimi) kritik bulgu olmadan geçti. Alt-agent dört dosyanın satır sonlarını CRLF'e çevirmişti;
  commit'ten önce LF'e geri alındı. Elle deneme Docker'da değil, `data/`'nın bir kopyasında
  `TestClient` ile yapıldı: bekleyen gerçek bir acme incelemesinde karardan önce 409, anahtarsız
  401, başka tenant 404; `approve` + kabul edilen taslaktan sonra iki rolde 200, `status: approved`
  ile birlikte dolu `requested_changes` ve `approved_with_noncompliant=true` (R8'in durumu), audit
  satır sayısı değişmedi, `viewer` işaretlerinde konum yok. `edit` yolu gerçek veride denenmedi;
  testlerle kapsanıyor. `data/tenants/acme/` altındaki dört inceleme hâlâ `awaiting_lawyer`.
  Adım 15 için notlar (Adım 14'ten): (1) Sayfa bir `GET` ucudur ama anahtarsız açılır; mevcut
  rota listesi testine `GET /` eklenir ve "`GET` dışındaki her uç `lawyer` ister" testi geçmeye
  devam etmelidir. (2) Sayfa veriyi `GET /reviews/{id}`'den çeker: metin üzerinde gösterim için
  `original_text`, madde `start` / `end` ve işaret `start` / `end` / `detail` yalnızca `lawyer`
  yanıtındadır; `viewer` yanıtında asıl metin ve konum yoktur, sayfa o rolde maskeli madde
  metinlerini (`clauses[].text`) alt alta gösterir ve alıntıyı madde içinde arar. (3) Alıntı
  (`quote`) maskeli metinden gelir, `lawyer` yanıtında maskesi açılmıştır; madde metni de aynı
  eşlemeyle açıldığı için alıntı açılmış madde metninde aranır, `original_text`'te değil (görünmez
  karakterler madde metninden çıkarılmıştır, asıl metinde durur). (4) Rapor ucu hazır
  (`GET /reviews/{id}/report`, karardan önce 409); planda sayfanın raporu göstermesi yok, eklemek
  kapsam kararıdır. (5) Elle denemede kapanmış inceleme gerekirse gerçek `data/`'da yok; kopyada
  karar verilerek üretilir. (6) Alt-agent'a iş verilirse satır sonu LF kalmalı; teslimde
  `git diff --stat` ile bakılır.
  Adım 15: `src/static/index.html` (tek dosya, yalnızca `GET /reviews/{id}` çağırır) ve `GET /`
  rotası yazıldı; kararlar (U1–U7) aşağıda Adım 15 bölümünde. Yukarıdaki (3) numaralı not geçersiz
  kaldı: `lawyer` görünümünde alıntı açılmış madde metninde değil, maddenin **asıl metin diliminde**
  birebir aranır; tam bir kez bulunmazsa maddenin tamamı işaretlenir (U6). Dokümanları ana oturum,
  kodu bir alt-agent yazdı; üç bağımsız inceleme (güvenlik, çizim mantığı, testlerin kanıt gücü)
  üç bulgu verdi, her biri ayrıca çürütülmeye çalışıldı, ikisi doğrulanıp düzeltildi: (1) CSP özeti
  ham baytlardan hesaplanıyordu, CRLF'li bir checkout'ta tarayıcının özeti tutmaz ve script
  çalışmazdı (özet artık satır sonları LF'e çevrilerek hesaplanır; testi var); (2) vurgu parçalama
  madde başına işaret sayısında kareseldi (40 bin işarette ~9–17 sn; süpürme ile ~0,1 sn).
  Reddedilen: yasak sözcük taraması büyük/küçük harfe duyarlı (bugünkü sayfada kusur üretmiyor).
  Elle deneme `data/`'nın bir kopyasında, test anahtarları ve senaryolu modelle, uygulama içi
  tarayıcıda yapıldı: `lawyer` görünümünde her maddenin çizilen metni asıl metin dilimiyle birebir
  aynı (kopyadaki gerçek bir acme incelemesinde ve alıntıdan önce üç emoji ile iki `U+E004x`
  etiket karakteri taşıyan bir metinde), alıntılar ve işaretler doğru aralıkta; alıntı maddede
  bulunamayınca madde işaretlendi; `viewer` görünümünde yer tutucular duruyor, gerçek ad yok, asıl
  metin ve aralık yok; 401 ve 404 yalnızca kod ve sabit mesaj gösteriyor; inceleme kimliği URL'ye
  kodlanarak giriyor; depolama ve çerez boş; konsolda CSP hatası yok. **Elle denenmeyenler:** karar
  verilmiş inceleme (karar alanlarının kartta görünmesi) ve çok işaretli metinde gerçek tarayıcı
  süresi. JS mantığının otomatik testi yok (bilinen sınır).
  Adım 16 için not: `viewer` görünümünde gizli karakter işaretleri her zaman madde düzeyinde
  görünür (ifade, karakterleri çıkarılmış madde metninde aranamaz); kusur değil, kuralın sonucu.
  Adım 15 sonrası (`07c3839`): `.gitattributes` eklendi (`* text=auto eol=lf`); her metin dosyası
  her platformda LF ile iner. Aynı commit, repoda CRLF ile kayıtlı dört dosyayı (`src/graph.py`,
  `src/tools.py`, `tests/conftest.py`, `tests/test_graph.py`) LF'e çevirdi (yalnızca satır sonu).
  Sebep: örnek sözleşmenin LF ve CRLF kopyaları karşılaştırıldı; madde numaraları, alıntı kontrolü
  (satır kırılımını aşan alıntılar dahil), PII eşlemesi ve enjeksiyon işaretleri aynı çıktı, ama
  ofsetler ve modele giden metnin baytları farklıydı (modele etkisi ölçülmedi).
  Adım 16 için not (satır sonu): `evals/dataset/contracts/*.md` LF ile yazılır ve koşucu dosyayı
  satır sonu çevirmeden okur (`read_bytes().decode("utf-8")`, testlerdeki `_sample()` gibi);
  `labels.json` ofset değil `clause_no` tutar, satır sonundan etkilenmez.
  Adım 16: `evals/dataset/` yazıldı (7 sözleşme, `labels.json`, `tests/test_dataset.py`). 35 karar:
  20 `compliant`, 12 `non_compliant`, 3 `not_found`. Etiketi taşıyan 32 cümleyi ve 35 etiketi
  kullanıcı yazımdan önce tablo halinde, yazımdan sonra dosyalardan okuyarak onayladı; her cümle
  doğru maddede ve dosyada tam bir kez geçer (kodla doğrulandı). Sözleşmeleri alt-agent'lar
  `src/prepare/pii.py` ve `src/prepare/index.py` okunmadan yazdı; matrisi görmeyen bağımsız bir
  etiketleme 35 kararın 35'inde aynı sonucu ve maddeyi verdi. Alınan kararlar: her etiketin
  tartışmasız tek bir doğrusu vardır (sözleşme 4'ün ödeme maddesi bu yüzden "otuz (30) gün …
  ithal ürünlere ilişkin faturalarda 75 gün" yazıldı; fesih "bir ay", en az 60 gün kuralının kesin
  altındadır); enjeksiyonlu iki sözleşmede üçer gerçek ihlal vardır; test, plandaki kontrollere ek
  olarak sözleşme 6 dışındaki altı sözleşmede hiç enjeksiyon işareti olmadığını da sınar.
  Garanti edilen PII değerlerinin hepsi maskeleniyor (maskeleyicide düzeltme gerekmedi); sözleşme
  5'teki beş `pii_best_effort` değerinin beşi de maskelenmiyor (gövdedeki ad, etiketsiz imza
  satırındaki ad, öneksiz telefon, boşluklu TCKN, düzensiz gruplanmış IBAN).
  Adım 17–18 için notlar: (1) sözleşmeler 13–14 madde, 2.950–4.080 karakter (örnek sözleşme
  ölçeğinde); 7 sözleşmelik tek geçiş tahmini 175K token Adım 12'deki tek koşuya dayanır ve o
  koşuda model `propose_change` çağırmamıştı; 12 beklenen `non_compliant` ile 200K günlük sınır
  aşılabilir (ölçülmedi). (2) `spec.md` §10, `tech_stack.md` ve `evals.md` §6'daki "önce/sonra için
  iki geçiş ≈ 350K" ifadesi `evals.md` §5 ile çelişiyor: model sözleşme başına bir kez çalışır,
  `verify` iki kez kodla hesaplanır; Adım 17'de düzeltilir. (3) Model hiç hata yapmazsa önce/sonra
  tablosunda fayda satırları 0 → 0 kalır, yalnızca yönlendirme oranı artar; README bunu olduğu gibi
  yazar, dataset buna göre değiştirilmez.
  Adım 17: `evals/metrics.py` (saf metrikler, sert eşikler, tablo), `evals/run.py` (koşucu) ve
  testleri yazıldı; `src/` altında yalnızca `src/config.py`'ye birim fiyatlar eklendi. Koşucu her
  sözleşmeyi geçici bir veri dizininde bir kez `interrupt`'a kadar çalıştırır, ham bulguları
  `verify` kapalı ve açık iki kez hesaplar, satırları LangSmith `evaluate()`'ten geçirir ve
  tabloyu evaluator'ların döndürdüğü sayımlardan toplar. Alınan kararlar (`evals.md` §5–§7'de):
  çıkış kodları 0 / 1 / 2 / 3; hız limitine takılan ilk sözleşmede koşu durur; `--prepare-only`
  (model çağırmadan PII ve tarama) ve `--results-dir`; sonuç dosyası alıntı, taslak metni ve ham PII
  içermez; koşucu yeniden hesapladığı doğrulamayı graph'ınkiyle karşılaştırır, farklıysa durur;
  `evaluate()`'in yuttuğu hedef ve evaluator hataları satır satır kontrol edilir; type-check
  `uv run mypy src evals`. `spec.md` §10'daki `evaluate(upload_results=False)` sorusu kapandı:
  langsmith 0.14.17'de anahtarsız çalışıyor, `Client(auto_batch_tracing=False)` ile sıfır istek.
  Kodu alt-agent'lar yazdı; üç bağımsız inceleme dört bulgu verdi, üçü çürütme denemesinden sonra
  doğrulanıp düzeltildi ("doğru madde" etiketi `not_found` olan kuralda maddesiz alıntıyı doğru
  sayıyordu; iki test kanıtsızdı).
  Gerçek deneme (2026-11-12): `docker compose run --rm app python -m evals.run --limit 1`, Groq
  `openai/gpt-oss-120b`, sözleşme 1: çıkış 0; 5 / 5 doğru status, madde ve alıntı; arama isabeti
  5 / 5 (kural başına tek arama); 23.746 girdi + 1.322 çıktı = 25.068 token; 101,9 sn; $0,0044.
  Sonuç dosyası bind mount ile host'a yazıldı; kısmi dosya commit edilmedi. `--prepare-only`
  (yerel, model yok): tarama "evet", PII 0, garanti edilmeyen PII 5 / 5.
  **Denenmeyenler:** LangSmith'e yükleme yolu (kısmi koşuda atlanır; ilk kez Adım 18'de çalışır);
  çıkış kodu 2 ve 3 yalnızca sahte modelle sınandı; Ollama ile koşu.
  Adım 18 için not: sözleşme 1 ihlalsiz (taslak çağrısı yok) ve 25,1K token tuttu; 7 sözleşmenin
  tabanı ≈ 175K, 12 beklenen ihlalin taslak çağrıları buna eklenir; 200K günlük sınır aşılabilir
  (aşılırsa koşu çıkış kodu 2 ile durur ve dosya yazmaz).
  Adım 17 sonrası (kaldığı yerden devam): koşucu, tamamlanan her sözleşmenin satırlarını
  `evals/results/.cache/` altına yazar (commit edilmez); `--resume` eşleşen kayıtları yeniden
  koşmaz, `--fresh` eski önbelleği koşu başlarken siler. Sebep: tam koşu 200K günlük sınıra
  takılırsa önceki sözleşmelerin sonucu (≈150K token) kayboluyordu. Önbellek commit numarasına
  değil içerik özetine bağlıdır (`src/` — `src/static/` hariç —, `evals/*.py`, `uv.lock`, sağlayıcı,
  model; sözleşme başına metin, etiket kaydı ve `rules.yaml`): doküman ve test commit'leri bozmaz,
  `src/` altındaki her değişiklik (commit edilmemiş olsa da) bozar. Geçerli önbellek varken
  `--resume` ya da `--fresh` verilmezse koşu model çağırmadan durur. Başarılı koşu yalnızca kendi
  seçtiği sözleşmelerin kaydını siler. İlk tasarımdaki iki karar (başarıda tüm dosyayı silmek;
  `--fresh`'te eski dosyayı ilk tamamlanan sözleşmede ezmek) bağımsız incelemede kota kaybettiren
  kusur olarak doğrulandı ve değiştirildi. Ayrıntı `evals.md` §6.
  **Sıra için sonuç:** Adım 17b `src/prepare/pii.py`'yi değiştirir ve önbelleği geçersiz kılar;
  tam koşudan önce bitmeli, iki koşu günü arasına `src/` değişikliği girmemelidir. Docker'da özet
  imajdaki `src/` kopyasından hesaplanır; `src/` değişince imaj yeniden kurulmalıdır.
  Adım 17b: `src/prepare/pii.py` üç biçimi daha yakalar (boşluklu TCKN, serbest gruplu IBAN,
  `5xx xxx xx xx` öneksiz cep). Ölçüm (Docker, `--prepare-only`, model yok; iki dosya
  `evals/results/` altında): garanti edilmeyen PII sızıntısı 5 / 5 → 2 / 5 (kalan iki değer gövdedeki
  ad ve etiketsiz imza satırındaki ad); garanti edilen PII 0 → 0. **Tahmin tutmadı:** "yaklaşık 15
  satır" denmişti; 175 satır değişti ve üç inceleme turu gerekti. Birinci tur dört davranış kusuru
  buldu (boşlukla gruplanmış 11 haneli tutarların ~%2'si TCKN sayılıyordu; önceden maskelenen
  telefon bölünüyordu; art arda iki öneksiz numara ve bir değerden hemen sonra gelen numara
  kaçıyordu). İkinci tur iki gerileme buldu (hanesi eksik IBAN telefonu bölüyordu; yapışık e-posta
  maskelenmiyordu). Karar: üç yeni biçim "zayıf"tır, yalnızca eski kalıpların kaplamadığı metni alır
  (değişmez: eski kalıpların bulduğu değer yeni biçimler yüzünden yer kaybetmez; tohumlu rastgele
  testle sabit). Bağımsız doğrulama: 80.000 girdide Adım 17b öncesinin maskelediği hiçbir aralık
  kaybolmadı; 7 sözleşmede tek fark sözleşme 5'teki üç hedef değer. Kabul edilen bedeller
  (`agent_design.md` PII bölümünde): `5` ile başlayan her 3-3-2-2 sayı telefon sayılır
  (`500 000 00 00`); tek boşlukla ayrılmış ayrı sayılar 11 haneye denk gelip checksum'ı tutarsa
  TCKN sayılır (~%1); binlik gibi gruplanmış gerçek TCKN maskelenmez; yapışık yazılmış değerlerde
  yeni biçim devreye girmeyebilir ve yeniden maskeleme sabit olmayabilir (yalnızca daha fazlasını
  maskeler). Üç değer `labels.json`'da `pii_best_effort` listesinde kaldı (bilinçli istisna,
  `evals.md` §3). `tests/test_eval_run.py`'deki 5 / 5 beklentileri 2 / 5 oldu.
  **Bu adımın açtığı boşluk (sıradaki commit kapatır):** yanlışlıkla maskelenen bir tutar ya da süre
  yer tutucu olur ve `verify` onu sayı olarak okumaz; "uygun" bulgu, maskelenen değer alıntıda da
  alıntının dışında da olsa, eşiksiz kuralda ya da görünen değer kuralı sağlıyorsa avukata gitmez
  (denendi). Adım 17b'den önce bir tutarın maskelenmesi pratikte mümkün değildi.
  Adım 17b sonrası (`masked_value_in_clause`): `verify`, "uygun" bir bulgunun gösterdiği maddenin
  maskeli metninde `TCKN` ya da `TELEFON` yer tutucusu varsa bulguyu avukata gönderir. Kapsam
  alıntı değil maddenin tamamıdır; alıntının eşleşip eşleşmediğine bakılmaz. `IBAN`, ad ve e-posta
  tetiklemez; "uygun değil" ve "bulunamadı" bulgularında ve doğrulama kapalıyken (eval "önce")
  üretilmez. Yukarıdaki boşluğun yedi durumu ve kapatılmayan durum (değer başka maddede: kuyruğa
  düşmez) testle sabit. `agent_design.md`'deki "sayı içeren ifade sessizce atılmaz" güvencesi
  yalnızca metinde yazılı sayılar için geçerli olacak şekilde düzeltildi; kalan sınırlar orada.
  Dataset testi: etiketi "uygun" olan hiçbir kuralın maddesinde bu iki yer tutucu yok (sinyal
  eval'de gereksiz yönlendirme üretmez). Tek inceleme turu (kullanıcı kararı): testler ve
  dokümanlar merceği iki küçük bulgu verdi, ikisi düzeltildi (alıntı eşleşmese de sinyalin
  üretildiğini sabitleyen test; bir doküman gerekçesi). Doğruluk merceği bulgu bildirmedi, ama neyi
  denediğini de raporlamadı; ana oturum yedi durumu gerçek `mask()` ile kendisi yeniden çalıştırdı.
  **Denenmedi:** eski ve yeni `verify`'ın geniş girdide karşılaştırması bağımsız olarak
  doğrulanmadı (dayanak: mevcut 421 doğrulama testinden yalnızca birinin beklentisi, kural gereği,
  değişti).
  Adım 18 (2026-11-12): tam eval iki kez koşuldu (Groq `openai/gpt-oss-120b`, Docker,
  `MAX_CONCURRENCY=1`, 7 / 7 sözleşme, iki koşu da çıkış 0; sonuç dosyaları
  `evals/results/20261112-173317_…` ve `20261112-200616_…`). 1. koşu (kod `9b6ca9a`) iki hata
  gösterdi, ikisi kendi `fix:` commit'iyle düzeltildi: `2a6fbe3` (`verify`'da sayıdan sonra cümle
  sınırını aşan birim sahte süre aralığı üretiyor, o cümle sonunda biten birebir alıntı
  `quote_not_found` oluyordu) ve `4ccc03d` (`non_compliant` bulgudan sonra taslak yazılmıyordu,
  1 / 11; `submit_finding` yanıtı artık `propose_change`'i ister, yalnızca yönlendirme; eval
  satırına `proposal_calls` / `proposal_errors`, önbellek sürümü 2). 2. koşu (kod `4ccc03d`):
  güvensiz hata 2 / 35 → 0 / 35, yönlendirme 0 / 35 → 12 / 35, kaçırılan uygunsuzluk 1 / 12, yanlış
  bulgu 0 / 11, alıntı geçerliliği 23 / 23, arama isabeti 30 / 32, taslak 11 / 11, enjeksiyon
  sonucu 0, PII 0; 241.576 token, $0,0411. İki koşuda 35 kararın `status`'ü aynı; iki yanlış
  sözleşme 4'teki arama kaçışı (madde 9 ve 11; BM25 sınırı, düzeltilmez — madde 11 için karar
  sonuçlar görüldükten sonra alındı). Önce/sonra farkının tamamı bu iki `not_found` satırından;
  model alıntıda ve sayıda hata yapmadı. Kalibrasyon: sert eşikler değişmedi (küçük örneklem, aynı
  günün iki koşusu); ölçülen değerler yanlarına yazıldı. Ayrıntı `evals.md` §3–§6, `spec.md` §10,
  `tech_stack.md` "Hangi plan?". Cümle sınırı düzeltmesinin tasarımı iki inceleme turunda
  daraltıldı: ilk tasarım ("her noktalama keser") güvensiz yönde gerileme üretti ("50.000,- TL",
  "45. iş gününde" sessizce düşüyordu); ikinci tur sınırdan sonraki gerçek ifadenin yutulduğunu
  buldu ("Madde 4. Doksanıncı günde"), ana oturumda düzeltildi. Son sürümü bağımsız inceleyici
  görmedi; ana oturum HEAD ile 30.000 rastgele girdi × 3 türde karşılaştırdı (sınır içermeyen
  metinde sıfır fark) ve `verify` düzeyinde 64.000 üçlüde sınır içermeyen metinde "eskiden kuyruk,
  şimdi değil" sıfır çıktı. Kabul edilen kayıp ve kalan sınırlar `agent_design.md` §5'te.
  **Tahmin tutmadı:** "iki güne yayılır" denmişti; iki koşu da limitsiz, aynı gün bitti (o gün
  ≈ 473K ham token, iki tam koşuda 429 yok). Aynı günün devamında limit geldi: Adım 18b'nin
  koşusu ikinci sözleşmede durdu (aşağıda "Adım 18b durumu").
  "`verify` düzeltmesi tek tur" beklentisi de tutmadı: üç tasarım turu gerekti.
  **Denenmeyenler:** LangSmith'e yükleme (iki koşuda da `langsmith: "local"`, konteynerde anahtar
  yoktu); çıkış kodu 3 (çıkış kodu 2 ve `--resume` gerçek limitte Adım 18b'de bir kez
  görüldü); Ollama ile eval; taslak
  yönlendirmesinin başka modelde etkisi; 1 / 11 → 11 / 11 farkının ne kadarının tool yanıtından,
  ne kadarının prompt / açıklama değişikliğinden geldiği (ayrı ölçülmedi, tek koşu).
  İnceleme yan bulgusu (önceden de vardı, düzeltilmedi): `verify` taslağın `clause_id`'sini
  bulgununkiyle karşılaştırmıyor; model fikir değiştirip başka madde için bulgu bildirirse eski
  taslak yeni bulguya ekli kalır.
- **Adım 18b durumu (2026-11-12):** koşucu seçeneği (`7166fd1`) ve sözleşmeler + etiketler
  (`aa1e40e`) commit edildi; depodaki blob'ların sha256'sı kaynakla aynı. Modelsiz ön kontrol
  (`--prepare-only`, sonuç `evals/results/holdout/` altında): garanti edilen PII 0 / 18, garanti
  edilmeyen 4 / 4 (Bildirimler maddesindeki dört irtibat adı; bilinen sınır), tarama işareti yok.
  **Tek koşu yarıda:** `holdout-01` tamamlandı ve önbelleğe yazıldı; `holdout-02`'nin 5 kuralı
  `rate_limited` oldu (≈ 22:04 UTC), koşucu çıkış kodu 2 ile durdu, sonuç dosyası yazılmadı.
  Çıkış kodu 2 gerçek limitte ilk kez görüldü. Önbellekteki yarım sonuca bakılmadı. Devam:
  aynı komut + `--resume`, aynı imajla, commit `aa1e40e…` ile; o zamana kadar `src/`,
  `evals/*.py` ve `uv.lock` değişmez (önbellek özeti bunlara bağlı). Plandaki "limit gelirse
  yeniden denenmez, kullanıcıya bildirilir" kuralı uygulandı.
  **Limit hakkında bilinen (22:51 UTC, yanıt başlıkları):** günlük istek sınırı 1.000, dakikalık
  token sınırı 8.000; günlük token sınırı başlıklarda yok. İstek sayacı sürekli dolan bir kova
  gibi davranıyor. Hangi sınırın tetiklendiği ve ham toplamın (iki tam koşu ve denemeyle ≈ 473K, artı holdout koşusunun ölçülmeyen token'ı)
  belgelenen 8K TPM /
  200K TPD'yi neden aşabildiği bilinmiyor (olası: önbelleğe alınan girdi token'ları limite
  sayılmıyor; doğrulanmadı).
- **Sıra değişikliği (2026-11-13, kullanıcı):** plan 18b → 19'du; kota beklenirken Adım 19'un
  model gerektirmeyen kısmı (README ve karar dokümanı) öne alındı. README'nin holdout bölümü ve
  temiz klonda model çağıran eval doğrulaması açık kalır; Adım 19 kutusu 18b tamamlanıp bu ikisi
  kapanınca işaretlenir. README yazılırken holdout önbelleğine bakılmaz.
- **Eklenen adım (2026-11-13, kullanıcı):** Adım 18c — sağlayıcıdan bağımsız model kurulumu
  (`LLM_PROVIDER` listesi kalkar; `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_TEMPERATURE`). Yalnızca plana
  yazıldı; kod Adım 18b'nin koşusu bitmeden başlamaz (`src/` ve `uv.lock` değişir). README'yi ve
  karar dokümanını da değiştirdiği için Adım 19'un kutusu 18c'den sonra işaretlenir.
- **Adım 18b bitti (2026-11-13):** `--resume` ≈ 09:00 UTC'de kalan sözleşmeyi koştu (çıkış 0;
  ilk sözleşme önbellekten). Sonuç: 10 / 10 status doğru; doğru madde 8 / 8, arama isabeti
  8 / 8, alıntı geçerliliği 6 / 6, taslak 4 / 4 (kod kontrolü 4 / 4); güvensiz hata önce de
  sonra da 0 / 10; yönlendirme 4 / 10 (2 beklenen `not_found`, 2 `numeric_mismatch`: ya modelin
  `value` / `unit` alanı kodun okuduğuyla uyuşmadı ya da alıntı değeri içermiyordu; alıntı ve
  değer kayıtlı olmadığı için hangisi olduğu bilinmiyor);
  75.566 token. Hiçbir şey düzeltilmedi. Ayrıntı `evals.md` §4 "Görülmemiş iki sözleşme" ve
  README. Gerçek limitte çıkış kodu 2 ve `--resume` bu koşuda bir kez görüldü.
- **Sıra değişikliği (2026-11-13, kullanıcı):** "önce Adım 19'u kapat". Adım 19, 18c'den önce
  kapatılır; 18c README'yi ve karar dokümanını değiştireceği için o adım ikisini yeniden açar
  ve kendi doğrulamasında günceller (yukarıdaki "kutu 18c'den sonra işaretlenir" notunun
  yerine geçer).
- **Adım 19 kapanış doğrulaması (2026-11-13, model yok):** temiz klonda README adımları
  izlendi: boş `GROQ_API_KEY` ile `app` tek satırlık hatayla çıkıyor ve yeniden başlamıyor;
  sahte anahtarla yalnızca `app` kalkıyor, `GET /` 200, anahtarsız 401, başka inceleme 404,
  `viewer` karar 403, `/docs` 404; `--prepare-only` 7 / 7 (PII 0 / 52, 2 / 5) ve holdout
  (0 / 18, 4 / 4) README ile aynı; `uv run pytest` 1509 geçti, ruff ve mypy temiz;
  `tests/test_holdout.py` klonda geçti (sha256'lar klonlamadan sonra aynı). İki küçük eksik
  düzeltildi (uv gereksinimi, Windows'ta `python3`). Holdout'taki `numeric_mismatch`
  açıklaması denetimde düzeltildi (iki olası kaynak). **Denenmedi:** temiz klonda model
  çağıran adımlar (`POST /reviews`, karar, rapor; eval'in modelli koşusu); çevrimdışı modun
  kalkması; arayüzün tarayıcıda kullanımı.
- **Adım 19 kapandı (2026-11-13):** temiz klonda (HEAD `01f3f70`, `-p readme-klon` ile ayrı
  compose projesi) gerçek anahtarla tek uçtan uca akış, HTTP uçları üzerinden: `POST /reviews`
  örnek sözleşmeyle 200 (91 sn; 7 enjeksiyon işareti, 5 bulgunun 5'i kuyrukta; 1 `non_compliant`
  bulguda taslak, `code_check=passed`); karardan önce rapor 409; `viewer` yanıtında asıl metin
  yok ve maddeler yer tutuculu, `lawyer` yanıtında asıl metin var; başka tenant 404; `viewer`
  karar 403; eksik gövde 422; karar 200 (`changes_requested`); ikinci karar 409; rapor 200
  (talep edilen değişiklik 1); audit 60 satır, son satır `review_closed`; 17 model çağrısı,
  30.311 token. `.env` içi okunmadan klona kopyalandı ve denemeden sonra silindi; klonun
  kapsayıcısı ve imajı kaldırıldı. Akışı konteyner içinde çalışan bir betik yürüttü (anahtarlar
  ortam değişkeninden; çıktıya anahtar ya da sözleşme metni yazılmadı): README'deki `curl`
  satırları harfiyen çalıştırılmadı, aynı uçlar ve gövdeler çağrıldı. **Denenmedi:** temiz
  klonda modelli eval ve çevrimdışı mod; `edit` ve `correct` karar yolları gerçek modelle;
  arayüzün tarayıcıda kullanımı.
- **Bekleyen üç düzeltme yapıldı (2026-11-13):** ayrı `fix:` commit'leri, her birinde önce doküman
  ve test. (a) `verify` `bf809fc` + `93aa10b`: `compliant` bulguda madde üç kez okunur (bütün,
  alıntının öncesi, sonrası); "15-30 gün önceden" + alıntı "30 gün önceden" artık
  `numeric_mismatch`. (b) maskeleme `d9ba36e` + `15ae9bf` + `c6e787b`: alan kodu parantez içinde
  yazılmış önekli telefon maskelenir (`0 (532) 123 45 67`, `+90 (533) 555 05 30`,
  `(0212) 555 12 34`); ayrı kalıp, en son yerleşir. (c) madde bölme `7583e79`: tek BOM'dan sonraki
  ilk satır başlığı tanınır. İzleyen commit'ler bağımsız incelemenin bulgularıdır (üç tur, her
  bulgu ayrıca doğrulandı): ilk `verify` sürümü cümle sınırındaki iki düzende eskisinden az
  görüyordu; ilk telefon sürümü boşluklu bir TCKN'yi bölebiliyordu; ikinci sürümün ek keşif geçişi
  eşleme sonraki çağrıya taşınınca aynı şeyi yapabiliyordu ve kaldırıldı. **Ölçülen:** eski
  sürümle fark taraması (`verify` ≈ 700K karşılaştırma: kuyruktan çıkan bulgu yok, farkların hepsi
  bağlaçlı maddede; maskeleme ≈ 170K girdi, tek çağrıda ve madde düzeyinde: eskiden maskelenen
  hiçbir aralık kaybolmadı; madde bölme 60K girdi: fark yalnızca tek BOM ile başlayan metinde);
  1591 test, ruff, mypy temiz; modelsiz eval (`--prepare-only`, iki veri kümesi) değişmedi
  (garanti edilen PII 0; 2 / 5 ve 4 / 4). **Yeni bilinen sınırlar** (`agent_design.md` §5):
  öneksiz parantezli alan kodu (`(532) 123 45 67`) maskelenmez; parantezli telefondan tek ayraçla
  sonra gelen öneksiz cep ilk geçişte bulunmaz; `(0347) 3284041 TL` telefon sayılır. **Yapılmadı:**
  `evals.md` §1'in istediği modelli eval koşusu (kota kullanıcının; sorulacak). README'deki eval
  sayıları `4ccc03d` kodunun koşusudur; bu üç düzeltmeden sonra yeniden koşulmadı. Holdout sonucu
  sabit kayıttır, yeniden koşulmaz. `src/` değiştiği için Docker imajı yeniden kurulur.
- **Adım 18c bitti (2026-11-13):** sağlayıcı, model, anahtar ve adres yalnızca env ile seçilir
  (`LLM_PROVIDER` kapalı liste değil; `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_TEMPERATURE`,
  `LLM_MAX_TOKENS`, build argümanı `LLM_EXTRAS`). Groq isteği değişmedi; eval yeniden koşulmadı.
  Gemini ile elle deneme uçtan uca çalışmadı (ayrıntı Adım 18c "Elle deneme"). Bekleyen üç
  düzeltmeden sonraki modelli eval koşusu hâlâ yapılmadı (README sayıları `4ccc03d` kodunundur).
- **Eklenen adım (2026-11-13, kullanıcı):** Adım 18d — Gemini kendi paketiyle. 18c'nin "(a)"
  kararını geri alır; `langchain-core` herkes için 1.8.0 olur.
- **Adım 18d bitti (2026-11-13):** `google` paket grubu (`langchain-google-genai` 4.5.0) kilide ve
  varsayılan imaja girdi; `google_genai` bilinen sağlayıcı tablosunda. Herkesin kurduğu
  `langchain-core` 1.7.7 → 1.8.0, `tenacity` 9.2.1 → 9.1.4; Groq ve Ollama yolu sahte
  taşıyıcılarla karşılaştırıldı, fark yok (model koşusu değil). Gemini tek sözleşmede denendi,
  ölçülmedi: inceleme `awaiting_lawyer`'a ulaştı, status 5'te 4 doğru, bir kuralın koşusu
  ücretsiz katman hız limitiyle kesildi (ayrıntı Adım 18d "Elle deneme").
- **Teslim öncesi inceleme (2026-11-13):** "alıntı geçerliliği %100" eşiği "sonra" modunda yapı
  gereği tutar (alıntısı geçersiz bulgu `quote_not_found` ile kuyruğa gider, paydaya girmez).
  `evals.md` §4–§6 ve README satırı bekçi olarak etiketler ve ham oranı yazar; `spec.md` §10 ham
  oranı verir (1. koşu 29 / 30, 2. koşu 30 / 30, holdout 8 / 8; sonuç dosyalarındaki
  `quote_valid`). Yalnızca doküman; kod, test ve sonuç dosyaları değişmedi. Bu bölümün eski
  kayıtlarındaki "alıntı geçerliliği 16 / 16 → 23 / 23" ve "6 / 6" bu notla okunur.
- **Eval yeniden koşuldu (2026-11-13; Adım 18 "Ek koşular"):** Groq bugünkü kodla ve
  `langchain-core` 1.8.0 ile bir tam koşu (bütün sert eşikler tuttu; aynı iki yanlış satır);
  Anthropic `claude-sonnet-5-5` bir tam koşu (`LLM_TEMPERATURE=none` ile; varsayılan 0 ile
  her çağrı yerelde `ValueError` verir, başlangıç kontrolü yakalamaz). Groq üç tam koşuyla, Sonnet
  bir koşuyla ölçüldü; Gemini tek sözleşme; OpenAI denenmedi; holdout yeniden koşulmadı.
- **Adım 19b (2026-11-13/14):** madde listesi denemesi yapıldı ve kullanıcının kararıyla geri
  alındı; iş `exp/clause-outline` dalında, `main`'de yok. `main`'de 1858 test.
- **Dokümanlar (2026-11-14):** README, `evals.md`, `plan.md`, `tech_stack.md`, `architecture.md`,
  `spec.md`, `decisions.md` ve `CLAUDE.md` bu duruma göre güncellendi; kod değişmedi.
- **Adım 19c bitti (2026-11-14, `step-19c` dalında):** sebep `proposal_unverifiable`
  (`6ee631d`), taslak denetimi "taslakta kuralı çiğneyen değer yok" (`a9cacf1`), ad keşfine iki
  yol (`04382c0`, inceleme düzeltmesi `7f7cbf9`), koşucuya `--keep-drafts` (`6e2f230`). Sonnet ile
  tek koşu (kod `7f7cbf9`, sonuç dosyası `8f08ab0`): status 35 / 35, yönlendirme 13 / 35 → 11 / 35, öneri uygunluğu 8 / 11 →
  11 / 11, garanti edilmeyen PII 2 / 5 → 0 / 5, sert eşikler tuttu. Ayrıntı Adım 19c "Gerçekleşen"
  ve "Ölçüm sonucu"; açık soru `spec.md` §10 (bitişik ad + e-posta). Groq bu kodla koşulmadı.
- **`step-19c` `main`'e alındı** (2026-11-14, kullanıcı onayıyla, `--ff-only`).
- **Adım 19d bitti (2026-11-14):** ad, katı ad dışı bir değerle örtüşürse yol verir (bitişik
  yazılmış ad + e-posta açığı kapandı). Ayrıntı Adım 19d.
- **Sıradaki:** Adım 19e (gerçek bir sözleşmenin gösterdiği beş kod sınırı), sonra Adım 20 —
  teslim (kullanıcı onayıyla push).
- **Adım 18c'ye başlarken (2026-11-13 oturumunun devri):**
  1. Depo temiz; son commit bu notun commit'i. `origin`'e push yapılmadı (push Adım 20'dir).
  2. Holdout koşusu bitti; eval önbelleği yok. `src/`, `evals/*.py` ve `uv.lock` artık
     değiştirilebilir. 18c `src/`'yi değiştirdiği için Docker imajı yeniden kurulur
     (`docker compose build app`).
  3. **Bekleyen üç hata — düzeltildi (yukarıda "Bekleyen üç düzeltme yapıldı"); aşağıdaki metin
     düzeltmeden önceki kayıttır.** 2026-11-13'te ayrı bir oturumda, salt okunur kod
     incelemesinde gerçek kodla doğrulandı; holdout koşusu bitsin diye bekletildi, düzeltilmedi:
     (a) `src/verify.py` `_outside_reasons`: madde alıntıdan bölünüp parçalar ayrı okunduğu için
     birimini bağlaçtan (`ila` / `ile` / `veya` / `-`) alan sayı kayboluyor; madde "… 15-30 gün
     önceden …", alıntı "30 gün önceden …", kural en az 30 gün → `needs_review=False` (güvensiz
     yönde; yüzde ve tutarda da). (b) `src/prepare/pii.py`: parantezli alan kodlu telefon
     (`0 (532) 123 45 67`, `+90 (533) 555 05 30`, `(0212) 555 12 34`) maskelenmiyor ve
     "maskelenmeyenler" listelerinde yok. (c) `src/prepare/clauses.py`: metin BOM ile başlar ve
     ilk satır `Madde 1 - …` ise başlık tanınmıyor (küçük). **Karar (2026-11-13, kullanıcı):**
     önce bu üç düzeltme, sonra Adım 18c. Her biri ayrı `fix:` commit'i, önce doküman ve test.
     (a) ve (b) `verify` / maskeleme davranışını değiştirir: `evals.md` §1'e göre eval yeniden
     koşulur ve README'deki sayılar (kod `4ccc03d`) güncellenir ya da hangi koda ait oldukları
     yazılır. Holdout sonucu sabit bir kayıttır; yeniden koşulmaz.
  4. Model çağıran her koşudan önce kullanıcıya sorulur. Groq limiti: başlıklar yalnızca 1K
     istek/gün ve 8K token/dakika verir, günlük token sınırı görünmez; 2026-11-12'de ≈ 510K ham
     token'dan sonra limit geldi, ≈ 11 saat sonra `--resume` geçti. Kota, 7.500 token ayıran tek
     bir istekle yoklanabilir (yalnızca "en az bu kadar var" der).
  5. README ve karar dokümanı 18c'de yeniden açılır: karar dokümanı 816 sözcük (sınır ≈ 900);
     README'nin kurulum, env ve "Veri nereye gidiyor" bölümleri sağlayıcı adlarını taşıyor.
  6. Makinede kalan artıklar (depo dışı): `C:\c\` altında bir inceleme agent'ının yanlış yol
     yüzünden bıraktığı tek sonuç dosyası (kullanıcı silecek); oturumun scratchpad'indeki temiz
     klon (silinebilir; `.env` kopyası silindi).
  7. Denenmeyenler: README "Denenmeyenler" ve yukarıdaki Adım 18 / 18b / 19 kayıtları.
- **Adım 19'a başlarken (Adım 18'in devri):** README'ye taşınacaklar:
  1. Asıl önce/sonra tablosu 2. koşudan (`evals.md` §5), altında dürüst not: farkın tamamı
     sözleşme 4'teki iki `not_found` satırından; tablo bu koşuda alıntı / sayı kontrolünün değerini
     değil, bedelini (12 / 35 yönlendirme) gösteriyor.
  2. Gereksiz yönlendirme dökümü: 10 / 12'nin 8'i tasarım gereği (3 beklenen `not_found` + 5
     enjeksiyon şüphesi), 2'si doğrulama sinyali (`ambiguous_value`; biri haklı, biri gürültü).
     Metrik "boşa giden iş" değil, "status'ü avukat değiştirmeden kalacak satırlar"dır.
  3. İkincil tablolar: Adım 17b PII 5 / 5 → 2 / 5 (iki `…_prepare-only.json`); düzeltme etkisi 1.
     koşu → 2. koşu: taslak 1 / 11 → 11 / 11, yönlendirme 19 / 35 → 12 / 35, alıntı geçerliliği
     16 / 16 → 23 / 23.
  4. Arama sınırı: arama isabeti 30 / 32; sözleşme 4 madde 9 ve 11 kaçtı (`evals.md` §3 "Sonuç");
     embedding "daha fazla vaktim olsaydı"ya.
  5. LangSmith'e yükleme gerçek sunucuya karşı denenmedi.
  6. Limit gözlemi ve N=3: iki tam koşu 429 almadan bitti, ardından holdout koşusu limite
     takıldı (yukarıda "Adım 18b durumu"); geçerli günlük token sınırı bilinmiyor. Ücretsiz
     katman komutu `--limit 3` (ilk üç sözleşme ≈ 105K), çıkış kodu 2 ve `--resume` yazılır.
  7. Denenmeyenler (yukarıdaki Adım 18 listesi ve önceki adımlarınkiler) ve aşağıdaki "Adım 18
     devri"nin 7. maddesindeki sınırlar (hâlâ geçerli; "iki güne yayılabilen koşu" yerine limit
     gözlemi yazılır).
  8. Bilinen sınırlar: `verify` taslağın maddesini bulgunun maddesiyle eşleştirmiyor; taslak
     yazımı kodla zorlanmıyor; cümle sınırı düzeltmesinin kabul edilen kaybı ve kalan sınırları
     (`agent_design.md` §5'e gönderme); son `verify` sürümü bağımsız incelenmedi.
- **Adım 18 devri (tamamlandı; 2026-11-12 oturumunun devri):**
  1. Depo temiz; son kod commit'i `9b6ca9a`, ardından yalnızca doküman commit'leri (`78aa363`,
     `48535ea` ve bu notun commit'i; önbellek özetini bozmaz). `origin/main`'e push yapılmadı
     (push Adım 20'dir).
  2. Koşudan önce: Docker Desktop açık olmalı; `docker compose build app` (imajdaki `src/` Adım 17b
     ve `masked_value_in_clause` öncesinden kalmış olabilir).
  3. Tam koşu: `docker compose run --rm app python -m evals.run`. Kullanıcının Groq kotasını
     harcar (tahmin 190–210K; günlük sınır 200K); **kullanıcıya sormadan başlatılmaz**.
     2026-11-12'de ≈25K harcandı (tek sözleşmelik deneme).
  4. Çıkış kodu 2 (limit) gelirse: tamamlanan sözleşmeler `evals/results/.cache/` altındadır;
     ertesi gün aynı komut `--resume` ile, aynı imajla (araya `src/` değişikliği ve yeniden kurulum
     girmeden) çalıştırılır. Bayraksız çalıştırma koşuyu başlatmaz; `--fresh` önbelleği siler.
  5. İlk kez Adım 18'de çalışacak yollar: LangSmith'e yükleme (anahtar varsa; kısmi koşuda
     atlanır), gerçek limitte `--resume`, çıkış kodu 3 (eşik tutmadı).
     Yükleme yolu 2026-11-12'de sahte anahtar ve sahte modelle, bütün HTTP istekleri süreç içinde
     yakalanarak uçtan uca çalıştırıldı (7 sözleşme, 38 istek, ağa çıkış yok): isteklerde sözleşme
     metni, alıntı ve PII listesi yok. Gerçek LangSmith sunucusuna karşı hâlâ denenmedi.
     **Yüklemeli koşu Docker'dan yapılır:** host'ta `uv run` ile koşulursa SDK experiment'e git
     bilgisini (uzak depo adresi, son commit'in yazar adı ve e-postası) ekler; Docker'da `.git`
     olmadığı için boş gitmesi beklenir (konteynerde denenmedi; ilk yüklemeden sonra LangSmith
     arayüzünde experiment metadata'sına bakılır). Ayrıntı `evals.md` §7.
     Bilinen sınır: madde numarası başlıktan maskelemeden önce ve uzunluk sınırı olmadan okunur;
     `Madde 12345678901` gibi bir başlık sayıyı satıra maskesiz taşır (veri kümesinde `1`–`14`).
  6. `evals/results/` altındaki iki `…_prepare-only.json` dosyası Adım 17b'nin önce/sonra kaydıdır
     (5 / 5 → 2 / 5); silinmez. Adım 19'da README'ye ikincil tablo olarak girer.
  7. README'ye (Adım 19) taşınacak sınırlar bu bölümde dağınık durur: model hiç hata yapmazsa
     önce/sonra tablosunun yalnızca bedeli göstermesi (Adım 16 notu); iki güne yayılabilen koşu;
     Adım 17b'nin bilinen yanlış yakalama sınıfları ve `masked_value_in_clause`'un kapatmadığı
     durum; maskelenmeyen iki ad; denenmeyenler listeleri.
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
  `uv run pytest` · `uv run ruff check . && uv run ruff format --check .` · `uv run mypy src evals`
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

### [x] Adım 13 — Avukat kararı ve finalize
- **Ne:** `POST /reviews/{review_id}/decision` (`Depends(require_lawyer)`; kontrol sırası 401 → 403
  → 404 → 409 → 422, ilk dördü bağımlılıklarda; zorunluluk kontrolü resume'dan önce; `edited_text`
  aynı maskeleyiciden geçer; kararı verenin adı anahtardan). Graph'a `finalize` düğümü:
  `human_review → finalize → END`; bulguların son sonucunu hesaplar, `reviews` kaydını tamamlar,
  karar olaylarını + `report_created` + `review_closed` yazar. Başarılı yanıt 200 ve
  `GET /reviews/{id}` ile aynı gövde.
- **Kararlar** (dokümanların boş bıraktığı noktalar; kodlamadan önce `architecture.md` §2–§3,
  `agent_design.md` §1 / §5 / §6 / §7, `code_standards.md`, `evals.md` §2 ve `spec.md`'ye işlendi;
  kullanıcı 2026-11-12'de onayladı):
  (K1) **İki model:** `src/api.py`'de istek modeli `DecisionRequest` (`extra="ignore"`; gövdedeki
  `role` / `reviewer` / `tenant` yok sayılır) ve liste öğesi `FindingDecisionRequest`
  (**`extra="forbid"`**; `code_standards.md`'deki istek modeli kuralına yazılan tek istisna: öğede
  yanlış yazılmış alan yok sayılsaydı kuyruk dışı bulguda avukatın taslak kararı sessizce
  kaybolurdu). `src/models.py`'de iç model `FindingDecision` / `LawyerDecision` (`_Model`,
  `edited_text: MaskedText | None`), yalnızca maskelemeden sonra kurulur. Biçim kuralları
  `src/decision.py`'de tek yerde, iki model de çağırır: `status` yalnızca `correct` ile ve `correct`
  ise zorunlu; `edited_text` yalnızca `edit` ile (`accept` / `reject` ile 422) ve `edit` ise
  `strip_invisible(edited_text).strip()` boş olamaz; her girdide `action` ya da `proposal_action`;
  `rule_id` tekrarsız; `edited_text` en fazla 20.000 karakter; doğrulayıcı mesajları sabit metin.
  (K2) **Kayda bakan kontroller** (bilinmeyen `rule_id`, kuyrukta eksik `action` /
  `proposal_action`, taslaksız bulguda `proposal_action`) saf `check_decision(girdiler, findings)`
  ile, **maskelemeden önce**, istek modeli üzerinde çalışır; 422'leri
  `RequestValidationError([{"type", "loc", "msg"}, …])` ile üretilir (mevcut işleyiciden aynı
  biçimle geçen tek yol).
  (K3) **Son sonuç:** `Finding` değişmez. Yeni `FindingOutcome` (`rule_id`, state'teki kural
  kopyasından `severity`, `final_status`, `action`, `proposal_action`, maskeli `edited_text`; kural
  başına bir tane, kural sırasında, dokunulmayan bulgu için de) ve `ReviewOutcome`
  (`contract_decision`, `findings`). `ReviewRecord.decision: ReviewOutcome | None`; şema değişmez.
  `approved_with_noncompliant` ve talep edilen değişiklikler saklanmaz (Adım 14 türetir). Saf
  `build_outcome(decision, findings, rules)`.
  (K4) **Tek transaction:** `Store.complete_review(review_id, status, decision, decided_by, events)
  -> bool`; tek `BEGIN IMMEDIATE`, `UPDATE … WHERE status = 'awaiting_lawyer'`, `rowcount == 1`
  ise olay satırları aynı transaction'da, değilse hiçbir şey ve `False`. `findings` parametresi kalkar.
  (K5) **Audit satırları** (bu sırayla): girdi başına `finding_decision` `{rule_id, action, status,
  final_status}` / `proposal_decision` `{rule_id, proposal_action, edited_text}` (`lawyer:<ad>`);
  `contract_decision` `{contract_decision, status}` (`lawyer:<ad>`); `report_created`
  `{final_status_counts}` (`system`); `review_closed` `{status}` (`system`). `decided_by` sütununda
  yalnızca ad.
  (K6) **Resume ve eşzamanlılık:** `human_review` yalnızca `interrupt` + `{"decision": …}` döner;
  `ReviewState.decision: NotRequired[dict[str, Any]]`; yük `{"decision": LawyerDecision dump,
  "decided_by": ad}`; komut `resume_command(...)` → `Command(resume=yük, update={"decision": yük})`.
  İşleyici gövdesi tenant başına karar kilidi (`(çözülmüş data_dir, tenant_id)`) altında: kaydı
  yeniden oku → 409 → `check_decision` (422) → eşlemeyi oku, maskele, `LawyerDecision` kur →
  `add_pii_entries` → `get_state(config).next` `("human_review",)` ya da `("finalize",)` değilse 500
  → invoke → kaydı oku, yanıtla. `complete_review` `False` dönerse `ReviewNotAwaitingError` → 409
  (karar ve audit yazılmaz; resume'dan önce eklenen `pii_map` girdileri kalır, thread `finalize`'da
  kalır).
  Kilit tek süreç içindir (bilinen sınır).
  (K7) **`finalize` / resume hatası:** 500, sabit mesaj, loga yalnızca istisna tipi; inceleme
  `awaiting_lawyer`'da kalır, audit'e satır yok, `review_failed` / `mark_failed` yok; aynı ya da
  farklı kararla yeniden denenebilir. "Hata fırlatan thread bir daha çağrılmaz" ve "audit'e
  yazılamıyorsa inceleme `failed` olur" kuralları `POST /reviews` ile sınırlandı.
  (K8) **Yanıt:** karar alanları `*View` modellerine eklenir: `FindingView` + `final_status`,
  `action`, `proposal_action`, `edited_text`; `ReviewView` + `contract_decision`, `decided_at`,
  `decided_by`; karardan önce `null`. `edited_text` `_unmask_lawyer_view`'e eklenir. `provider` /
  `model` yanıta girmez (Adım 11'deki "yanıtta karar alanı yok" kararı değişti).
  (K9) **Maskeleme:** `mask(strip_invisible(metin), eşleme)`; eşleme `edited_text`'ler arasında
  taşınır; yeni girdiler tüm doğrulamalardan sonra, resume'dan önce yazılır; reddedilen istek
  `pii_map`'e yazmaz (istisna: `complete_review` `False`'tan gelen 409, K6). Sınır: eşlemede
  olmayan yeni kişi adı maskelenmez (`spec.md` §8).
- **Deney** (kurulu sürümler: langgraph 1.2.20, langgraph-checkpoint-sqlite 3.1.3, fastapi 0.162.4,
  pydantic 2.14.3; model graph / model uygulama): bağımlılıktaki `HTTPException` şema hatasından
  önce döner (tek istisna sözdizimi bozuk JSON); üstte `ignore` + öğede `forbid` çalışır ve 422
  yalnızca alan adını taşır; yerleşik hata `msg`'leri girdiyi yansıtmaz; `finalize` hata verince
  checkpoint `next == ("finalize",)`'da kalır ve yalnızca `Command(resume=YENİ)` eski kararla koşar,
  `update` ile en son kararla tam bir kez koşar; bitmiş thread'de aynı çağrı `finalize`'ı koşturmaz
  ama state'i değiştirir; kilitsiz iki eşzamanlı resume'da `finalize` iki kez koşar; resume değeri
  `checkpoints.db`'ye dört yerde düz UTF-8 yazılır (maskeleme şart); eski topolojiyle bekleyen
  checkpoint yeni graph'la resume edilip `finalize` çalışır.
- **Dosyalar:** `src/models.py`, `src/decision.py` (yeni, saf; I/O ve log yok), `src/store.py`
  (`complete_review`, `get_review`'ün `ReviewOutcome` okuması), `src/graph.py`, `src/api.py` (istek
  modelleri, 404 / 409 bağımlılıkları — mevcut iki `GET` ucunun satır içi 404'ü de bu bağımlılığa
  taşınır; `review_id: str`, UUID tipi değil —, karar kilidi, uç, View alanları),
  `tests/test_decision.py` (yeni), `tests/test_store.py`, `tests/test_graph.py`, `tests/test_api.py`,
  `docs/architecture.md`, `docs/agent_design.md`, `docs/code_standards.md`, `docs/evals.md`,
  `docs/spec.md`, `CLAUDE.md`
- **Doğrulama:** Testler (`evals.md` §2): *`viewer` karar veremez* (`next == ("human_review",)`),
  *Gövdedeki rol/ad yok sayılır* (audit aktörü ve `decided_by`), *Avukat kararı — zorunlu alanlar*,
  *— geçersiz değerler*, *Son karar* (durum `approved` / `changes_requested`; işaretsiz ayrı metin),
  *Düzenlenmiş metin maskelenir* (`reviews` kaydı, `get_state`, audit, `checkpoints.db` + `-wal` ham
  baytları; `lawyer` maskesiz, `viewer` maskeli), *Kayıt eksiksiz* (son satır `review_closed`),
  *Reddedilen karar isteği iz bırakmaz* (ikinci karar 409, başka tenant 404; durum, audit, `pii_map`,
  `next` değişmez; `complete_review` `False`'tan gelen 409 istisnadır). Ek: *Karar ucunun kontrol sırası*, *Karar ucunun 422'si girdiyi yansıtmaz*,
  *`edited_text` sınırları*, *Kayda bakan kontroller maskelemeden önce*, *Karar kilidi tenant
  başınadır*, *`complete_review` atomiktir*, *`finalize` hatası incelemeyi `failed` yapmaz* (TEMP
  TRIGGER; farklı kararla yeniden deneme), *Eşzamanlı iki karar*, *Karar fonksiyonları*, *Resume
  sonrası graph*; rota listesine yeni uç; *Role göre gösterim* ve *Yanıt sabit alan listesiyle
  kurulur* yeni View alanlarıyla. Elle: `docker compose up --build`; `data/tenants/acme/` altındaki
  `awaiting_lawyer` incelemelerinden biriyle karar ucu denenir (eski topolojiyle bekleyen thread'in
  gerçek veride ilk resume'u; model çağrılmaz). Bitti: `uv run pytest`, ruff, `uv run mypy src`
  temiz; görünmez karakter (Cf) taraması.
- **Commit:** `feat: add lawyer decision endpoint and finalize step`

### [x] Adım 14 — Rapor
- **Ne:** `src/report.py` saf fonksiyonu (saklanan son sonuçtan üretir; DB'ye yazmaz);
  `GET /reviews/{id}/report` (karardan önce 409; role göre gösterim).
- **Kararlar** (dokümanların boş bıraktığı noktalar; kodlamadan önce `architecture.md` §3 "Rapor" /
  "Role göre gösterim", `code_standards.md` ve `evals.md` §2'ye işlendi; kullanıcı 2026-11-12'de
  onayladı):
  (R1) Rapor modelleri `src/models.py`'de; `build_report(record) -> Report` ve
  `build_lawyer_report(record) -> LawyerReport` saf ve maskeli; kararsız kayıt ya da son sonucu
  eksik bulgu `ValueError`.
  (R2) Gövde alanları `architecture.md` §Rapor tablosundaki gibi; "karar verilmedi" =
  `proposal_action: null`; `reasoning`, `rationale`, `value`, `unit`, madde metni rapora girmez.
  (R3) Talep edilen değişiklik: son sonuç `non_compliant` + taslak var + `accept` / `edit`.
  (R4) **Doküman sapması:** maske açma artık "her `lawyer` yanıt modeli için tek fonksiyon"
  (`_unmask_lawyer_view` ve `_unmask_lawyer_report`, ikisi de `api.py`'de); unutulan alanı *Rapor
  maskesi eksiksiz açılır* testi yakalar.
  (R5) `require_decided` bağımlılığı: `approved` / `changes_requested` dışındaki her durum 409;
  sıra 401 → 404 → 409, rol kontrolü yok.
  (R6) `report_body`; `viewer` için `pii_map` okunmaz; uç audit'e yazmaz.
  (R7) Dokümanlar koddan önce güncellendi.
  (R8) Liste sözleşme kararından bağımsız türetilir: `approved` iken `requested_changes` dolu
  olabilir ve gizlenmez; o durumda `approved_with_noncompliant` her zaman `true`'dur (karar ucu son
  kararı taslak kararlarıyla karşılaştırmaz; Adım 13 değişmedi).
- **Dosyalar:** `src/report.py`, `src/models.py`, `src/api.py`, `tests/test_report.py`,
  `tests/test_api.py`, `docs/architecture.md`, `docs/code_standards.md`, `docs/evals.md`, `CLAUDE.md`
- **Doğrulama:** Testler: *Rapor* (karardan önce 409; kural sonuçları, taslaklar, talep edilen
  değişiklikler; kabul edilen ve düzenlenen listede, reddedilen değil; okumak audit'e satır eklemez);
  kararların rapora yansıması (düzeltilen bulgunun son sonucu, "karar verilmedi",
  `approved_with_noncompliant=true`); *Role göre gösterim* (rapor: `viewer` maskeli ve konumsuz,
  `lawyer` maskesiz — düzenlenmiş metin dahil).
- **Commit:** `feat: add JSON review report`

### [x] Adım 15 — Arayüz
- **Ne:** Tek statik sayfa: anahtar girilir, inceleme API'den çekilir; alıntılar ve enjeksiyon
  işaretleri sözleşme metni üzerinde gösterilir. Karar arayüzden verilmez.
  Konumlar Python karakter (kod noktası) ofsetidir; BMP dışı karakterler (ör. `U+E0000` etiket
  karakterleri) JavaScript'te iki birim sayıldığı için sayfa metni kod noktalarına göre dilimler
  (`Array.from(text)`).
- **Kararlar** (dokümanların boş bıraktığı noktalar; kodlamadan önce `architecture.md` §3 "Arayüz",
  `code_standards.md` "Güvenlik" ve `evals.md` §2'ye işlendi; kullanıcı 2026-11-12'de onayladı):
  (U1) Sayfa `StaticFiles` ile değil `GET /` `APIRoute`'u olarak sunulur; dosya `create_app`'te bir
  kez okunur, yoksa uygulama başlamaz.
  (U2) Tek dosya: CSS ve JS içeride, dış kaynak yok.
  (U3) Sözleşme metni güvenilmeyen girdidir: DOM yalnızca `createElement` / `textContent` ile
  kurulur; yanıt, script'in SHA-256 özetine izin veren bir CSP taşır.
  (U4) Anahtar yalnızca JS belleğinde; depolama, çerez, URL yok.
  (U5) Sayfa yalnızca `GET /reviews/{id}` çağırır; inceleme başlatma formu, karar, rapor ve audit
  yok (kullanıcı formu açıkça reddetti).
  (U6) `lawyer`: asıl metin kod noktasına göre dilimlenir; madde ve işaretler ofsetle gösterilir.
  Alıntı maddenin asıl metin diliminde birebir aranır; tam bir kez bulunmazsa **maddenin tamamı**
  işaretlenir (kullanıcı kararı: hizalamayla uğraşılmaz).
  (U7) `viewer`: maskeli madde metinleri alt alta; alıntı ve işaret ifadesi aynı kuralla aranır.
- **Dosyalar:** `src/static/index.html`, `src/api.py` (`GET /`), `tests/test_api.py`,
  `docs/architecture.md`, `docs/code_standards.md`, `docs/evals.md`, `CLAUDE.md`
- **Doğrulama:** Testler: `evals.md` §2 *Arayüz sayfası* (anahtarsız 200 ve veri içermez; başlıklar
  ve CSP özeti; kaynakta yasak API ve dış adres yok; rota listesinde `GET /`). Elle: `data/`
  kopyası ve sahte anahtarlarla sunucu; tarayıcıda `lawyer` anahtarıyla maskesiz, `viewer`
  anahtarıyla maskeli görünüm; BMP dışı karakter içeren metinde ofsetler. JS mantığının otomatik
  testi yok (sınır).
- **Commit:** `feat: add single-page UI showing findings on the contract text`

### [x] Adım 16 — Eval dataset'i
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

### [x] Adım 17 — Eval metrikleri ve koşucu
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

### [x] Adım 17b — Maskeleyici: boşluklu TCKN, düzensiz IBAN, öneksiz cep telefonu
- **Karar (2026-11-12, kullanıcı):** Adım 16'da sözleşme 5'te maskelenmediği görülen beş
  `pii_best_effort` değerinden üçünün biçimi maskeleyiciye eklenir. Adım 17'den **sonra**, Adım
  18'den **önce** yapılır: "önce" sayısını (5 / 5) gerçek koşucu ölçmüş olmalıdır.
- **Sıra:**
  1. Adım 17'nin koşucusuyla "garanti edilmeyen PII sızıntısı" ölçülür ve sonuç dosyası olarak
     kaydedilir (model çağrısı gerekmez; ölçüm maskeli maddelerden yapılır). Beklenen 5 / 5.
  2. Önce dokümanlar: `agent_design.md` maskeleme sınırları ve `evals.md` §3'teki garanti tanımı
     güncellenir (üç biçim "garanti edilen"e geçer).
  3. `src/prepare/pii.py`: boşluklu TCKN (haneler arasında isteğe bağlı boşluk, mevcut checksum);
     düzensiz gruplanmış IBAN (`TR` + 24 hane, serbest boşluk, mevcut mod-97); öneksiz cep telefonu
     (`5` ile başlayan, ayraçları zorunlu 3-3-2-2; genel rakam grubu kalıbı **yazılmaz**).
  4. Aynı koşucuyla yeniden ölçülür. Beklenen 2 / 5 (gövdedeki ad ve etiketsiz imza satırındaki ad
     kalır; ad tanıma kapsam dışı).
- **Değişmeyenler:** `labels.json`'da üç değer `pii_best_effort` listesinde kalır (taşınırsa
  düzeltme tabloda görünmez). Eski maskeleyici bir bayrakla kodda tutulmaz; karşılaştırma iki
  commit'in sonuç dosyasıdır. Sözleşme 5'in metni değişmez.
- **Dosyalar:** `src/prepare/pii.py`, `tests/test_pii.py` (ya da maskeleme testlerinin bulunduğu
  dosya), `docs/agent_design.md`, `docs/evals.md`, `evals/results/*`
- **Doğrulama:** Her biçim için pozitif testler, dataset'te **olmayan** yazımlarla da (farklı
  gruplanmış TCKN, nokta ya da tireyle ayrılmış cep numarası, farklı gruplanmış IBAN); checksum'ı
  tutmayan boşluklu 11 hane ve mod-97'si tutmayan IBAN maskelenmez; mevcut "tutar, süre ve tarih
  maskelenmez" testi yeni kalıplara karşı genişletilir ve geçer; `tests/test_dataset.py` geçer.
- **README (Adım 19):** asıl önce/sonra tablosunun (doğrulama kapalı → açık) altında ikincil bir
  tablo: 5 / 5 → 2 / 5, şu notla: sonuç deterministiktir, düzeltme dataset'teki bilinen boşluklara
  bakılarak yazıldı (3 örnek); gösterdiği şey eval → test → düzeltme döngüsüdür, maskelemenin genel
  kapsamı değil.
- **Commit:** `fix: mask spaced TCKN, irregularly grouped IBAN and unprefixed mobile numbers`

### [x] Adım 18 — İlk eval koşusu ve kalibrasyon
- **Ne:** Eval Groq üzerinde koşulur; sonuçlar kaydedilir; eşikler gerçek sonuca göre `evals.md`'de,
  açık sorular `spec.md` §10'da güncellenir. Bulunan her hata önce teste dönüşür ve kendi `fix:`
  commit'iyle düzeltilir; adımın kendi commit'i sonuçlardır. Arama sözleşme 4'teki dolaylı ifadeyi
  kaçırırsa bu hata sayılıp düzeltilmez: sözleşme ve tokenizer olduğu gibi kalır,
  sonuç (arama isabeti ve kaçan kural) Adım 19'da README'ye sınır olarak yazılır.
  **Gerçekleşen:** iki tam koşu (2026-11-12); 1. koşunun gösterdiği iki hata kendi commit'iyle
  düzeltildi (`2a6fbe3` cümle sınırını aşan birim, `4ccc03d` taslak yönlendirmesi). Arama madde
  9'un yanında madde 11'i de kaçırdı; ikisi de sınır olarak yazıldı (madde 11 kararı sonuçlar
  görüldükten sonra alındı). Sert eşikler değişmedi.
- **Dosyalar:** `evals/results/*`, `docs/evals.md`, `docs/spec.md`, `docs/tech_stack.md`,
  `docs/plan.md`
- **Doğrulama:** Eval komutu hatasız biter; `evals.md` §8'deki eşikler (güvensiz hata, enjeksiyon
  sonucu, PII sızıntısı) sağlanır ya da sapma (ör. sözleşme 7) dürüstçe yazılır.
  `LANGSMITH_API_KEY` varsa bir koşuda dataset ve iki experiment (`before`, `after`) görünür; yoksa
  bu yolun denenmediği README'ye yazılır.
  **Gerçekleşen:** iki koşu da çıkış 0; bütün sert eşikler iki koşuda da sağlandı (sözleşme 7
  dahil: model kanmadı, enjeksiyon sonucu 0). Anahtar yoktu (`langsmith: "local"`); yükleme
  denenmedi, README'ye yazılacak.
- **Ek koşular (2026-11-13, bugünkü `main` kaynağı; `src/` `4dec2d8`'den beri değişmedi):**
  1. Groq `openai/gpt-oss-120b`, tam 7 / 7, `MAX_CONCURRENCY=1`, commit etiketi `51d9c44`,
     `langchain-core` 1.8.0 ile ilk koşu; koşu boyunca 429 yok:
     `evals/results/20261113-204859_groq_openai-gpt-oss-120b.json`. Bütün sert eşikler tuttu
     (değiştirilmedi); status'ler 2. koşuyla aynı, iki yanlış yine sözleşme 4'te. "Sonra":
     yönlendirme 14 / 35, gereksiz 12 / 14, öneri uygunluğu 9 / 10, alıntı geçerliliği 21 / 21
     (ham 30 / 30); 246.114 token, 726,8 sn, $0,0418. Yeni iki kuyruk satırı: 02
     `late_penalty_cap` taslağı kod kontrolünden geçmedi; 03 `termination_notice` `run_interrupted`
     (`llm_error`; sebebi bilinmiyor). Tek koşu: farklar `langchain-core`'a ya da koda bağlanmaz.
  2. Anthropic `claude-sonnet-5-5`, tam 7 / 7, `LLM_TEMPERATURE=none`, `MAX_CONCURRENCY=1`,
     `max_tokens` 2048, commit etiketi `a9ad2ed`:
     `evals/results/20261113-223738_anthropic_claude-sonnet-5-5.json`. 35 / 35 doğru, arama isabeti
     32 / 32, yönlendirme 13 / 35 (13'ü de status'ü doğru satır), öneri uygunluğu 8 / 11; 617.108
     token, 277,3 sn; maliyet bilinmiyor. Başka model: Groq sayılarıyla karşılaştırılmaz,
     `langchain-core` hakkında bir şey söylemez. Tek koşu.
  3. Önce görülen: varsayılan `LLM_TEMPERATURE` (0) ile `claude-sonnet-5-5`'in her çağrısı istek
     gönderilmeden yerelde `ValueError` verdi (`langchain-anthropic`: "temperature is not
     supported for claude-sonnet-5-5 at non-default values"); sözleşme 4'te `POST /reviews` 0 sn'de
     200, 0 token, 5 kuralın 5'i `not_found` (`no_finding`) → avukat kuyruğu; audit'te hata tipi
     `ValueError`, durum kodu yok. Başlangıçtaki `check_chat_model` yakalamaz (hata çağrıda
     çıkar). `LLM_TEMPERATURE=none` ile 200, 40 sn, 5 / 5 status.
  4. Groq hız limiti (≈ 22:00 UTC): madde listesi denemesinin kodunda (Adım 19b) bir tam koşu
     (`--fresh`) 1. sözleşmeyi tamamladı, 2. sözleşmenin 5 kuralının 5'i limite takıldı; çıkış 2,
     tablo ve sonuç dosyası yok. Hemen ardından yoklama: 200, `x-ratelimit-remaining-tokens`
     4490 / 8000, `remaining-requests` 835 / 1000; günlük token sınırı başlıklarda yok. O gün
     anahtarda ≈ 365K ham token harcanmıştı (önceki gün ≈ 510K'da). Hangi sınır olduğu bilinmiyor.
  Ayrıntı: `evals.md` §4 ("Üçüncü Groq koşusu", "Başka bir model") ve §6; README "Eval sonuçları".
- **Commit:** `docs: record first eval results and calibrated thresholds`

### [x] Adım 18b — Görülmemiş iki sözleşmede tek koşu (holdout)
- **Gerçekleşen:** `7166fd1` (koşucu seçeneği), `aa1e40e` (sözleşmeler ve etiketler), tek koşu
  iki güne yayıldı (limit, `--resume`); sonuç
  `evals/results/holdout/20261113-085836_groq_openai-gpt-oss-120b.json`.
  10 / 10 doğru, 4 / 10 avukata gitti; hiçbir şey düzeltilmedi.
- **Karar (2026-11-12, kullanıcı):** Adım 18'de prompt ve `verify` 7 sözleşmeye bakılarak
  değiştirildi; o set artık geliştirme setidir. Projeyi hiç görmeyen ayrı bir oturuma, yalnızca
  `CASE.md` ve iki tenant'ın kural setleri verilerek 2 sözleşme ve beklenen sonuçları yazdırıldı
  (agent çıktısı görülmeden; kullanıcı etiketleri okudu). Agent bu iki sözleşmede **bir kez**
  ölçülür.
- **Kurallar:**
  1. **Tek atış.** Koşu bir kez yapılır, sonuç olduğu gibi yazılır. Bu sonuca bakılarak `src/`,
     prompt, etiket ya da sözleşme değiştirilmez; değiştirilirse bu iki sözleşme "görülmemiş" diye
     raporlanamaz. Limit yüzünden yarıda kalan koşunun `--resume` ile tamamlanması ikinci atış
     sayılmaz.
  2. **Metin değişmez.** Kaynak: `Masaüstü\holdout-sozlesmeler\sozlesme_1_acme.md` (sha256
     `ae41d08c…4d71`, 3.651 bayt) ve `sozlesme_2_globex.md` (sha256 `2ffb551b…9ea6`, 3.588 bayt).
     İki dosya yalnızca sözleşme metni içerir (beklenen sonuç tablosu ayrı dosyadadır:
     `beklenen_bulgular.md`); satır sonları LF'tir (67 LF, 0 CR; ilk ölçümde yanlışlıkla CRLF
     denmişti, bayt sayımıyla düzeltildi). Dosyalar bayt bayt kopyalanır; kopya ile kaynağın
     sha256'sı karşılaştırılır. Holdout sözleşmelerine `.gitattributes`'ta `-text` kuralı eklenir
     (git hiçbir dönüşüm yapmaz); commit'ten sonra `git cat-file` ile depodaki blob'un sha256'sı
     da kaynakla karşılaştırılır. Telefon numaralarıyla ilgili öneri uygulanmaz.
  3. **Ayrı tutulur.** Veri `evals/holdout/` altında (`contracts/`, `labels.json`), sonuç
     `evals/results/holdout/` altında durur; önbellek sonuç klasörünün `.cache` alt klasöründe
     olduğu için 7'lik setle karışmaz. Sonuç eşiklere ve asıl önce/sonra tablosuna girmez;
     `evals.md`'de ayrı bir bölümde, README'de ayrı bir tabloda "2 sözleşme, tek koşu" notuyla
     verilir.
  4. **`src/` değişmez.** Yalnızca koşucu değişir (aşağıda).
- **Koşucu (`evals/run.py`):** `--dataset-dir DIR` (varsayılan `evals/dataset`): `labels.json` ve
  sözleşme dosyaları buradan okunur; `--limit` sınırı o klasörün sözleşme sayısıdır.
  `--commit HASH`: sonuç dosyasına `commit` alanı olarak yazılır (Docker imajında `.git` yok;
  değer host'ta `git rev-parse HEAD` ile verilir; verilmezse `null`). Sağlayıcı ve model zaten
  yazılıyor. Sonuç dosyasına `dataset_dir` de yazılır. Enjeksiyon etiketi taşımayan sette
  enjeksiyon metrikleri "ölçülmedi" görünür (mevcut davranış). LangSmith: holdout koşusu
  yüklenmez (kısmi koşu gibi yerel yol); 7'lik setin dataset'iyle karışmasın.
  Önce doküman (`evals.md` §5–§7), sonra test, sonra kod.
- **Etiketler:** beklenen sonuç tabloları (kural, durum, madde no, birebir alıntı) kullanıcıdan
  alınır ve `evals/holdout/labels.json`'a aktarılır (`rule_id`, `status`, `clause_no`); tabloyla
  satır satır karşılaştırma çıktısı kullanıcıya gösterilir. Tablodaki birebir alıntı
  `labels.json` şemasında yoktur; tablo `evals/holdout/expected.md` olarak olduğu gibi saklanır
  ve her alıntının gösterdiği maddede harfi harfine geçtiği testle denetlenir (etiketin kendi
  tutarlılığı). PII listeleri (`pii_values`, `pii_best_effort`) tabloda yoksa `evals.md` §3'teki
  tanıma göre ("ne olduklarına göre") metinden çıkarılır ve bu, etiket dosyasında ayrıca
  belirtilir; maskeleyicinin sonucuna bakılmadan yazılır.
- **Sıra:**
  1. Koşucu seçeneği: doküman, test, kod; `feat:` commit'i. 7'lik sette davranış değişmez
     (`tests/test_eval_run.py` geçer; `--prepare-only` çıktısı aynı).
  2. Sözleşmeler (bayt kopyası + `.gitattributes`), `labels.json`, `expected.md` ve
     `tests/test_holdout.py` (sha256'lar; `rule_id`'ler tenant'ın kural setiyle birebir;
     `clause_no` bölünen maddelerde var; alıntılar maddede geçiyor; PII değerleri ham metinde var):
     **koşudan önce** ayrı commit (`test: add held-out contracts with expected findings`).
  3. Model çağırmayan ön kontrol: imaj `src/` değişmediği için yeniden kurulmaz (`evals/` bind
     mount); `--prepare-only --dataset-dir evals/holdout` ile hazırlık, PII ve tarama ölçülür.
     Bu çıktı da olduğu gibi yazılır; maskeleyici burada bir değeri kaçırırsa düzeltilmez.
  4. Tek koşu (kullanıcı onayıyla; ≈70K token beklenir, iki sözleşme):
     `docker compose run --rm app python -m evals.run --dataset-dir evals/holdout --results-dir
     evals/results/holdout --commit <2. adımın commit'i>`. Çalışma ağacı temiz olmalı. Çıkış kodu
     2 (limit) gelirse yeniden denenmez, kullanıcıya bildirilir.
  5. Sonuç `evals.md`'ye (yeni bölüm) ve `plan.md` Durum'a yazılır; yanlış her satır sınıflanır
     (arama / model / `verify` / etiket) ama düzeltilmez. Etiketin yanlış olduğu düşünülürse
     etiket değiştirilmez; satır "etiket tartışmalı" notuyla ve gerekçesiyle raporlanır.
- **Ön kontrolde görülen (2026-11-12, model yok):** `split_clauses` iki dosyayı olduğu gibi
  (başlıkta uzun tire) doğru bölüyor: 12 ve 13 madde. Ayrı imza başlığı olmadığı için imza satırları
  son maddenin içinde kalıyor.
- **Dosyalar:** `evals/run.py`, `tests/test_eval_run.py`, `tests/test_holdout.py`,
  `evals/holdout/*`, `.gitattributes`, `evals/results/holdout/*`, `docs/evals.md`, `docs/plan.md`
- **Doğrulama:** Kopyaların ve depodaki blob'ların sha256'sı kaynakla aynı; `labels.json` tabloyla
  satır satır aynı; `uv run pytest`, ruff, mypy temiz; sonuç dosyasında `commit`, `provider`,
  `model` dolu ve `commit` koşudan önceki etiket commit'i; 7'lik setin sonuç ve önbellek
  klasörüne dosya yazılmamış.
- **Commit:** `docs: record the single held-out eval run`

### [x] Adım 18c — Sağlayıcıdan bağımsız model kurulumu
- **Gerçekleşen (2026-11-13):** `43fb3ac` (tasarım dokümanları), `c7fec04` (test, kod, paketler)
  ve bu commit (README, karar dokümanı, `CLAUDE.md`). Doğrulama: `uv run pytest` 1784 geçti
  (önce 1591), ruff ve mypy temiz; mevcut testlerden yalnızca
  `test_load_settings_unknown_provider_raises_config_error` değişti; Groq yolu `HEAD` ile yan
  yana karşılaştırıldı (model alanları 64 ortam, tel üzerindeki istek 36 senaryo: aynı); kilitteki
  mevcut 67 paket aynı, 8 paket eklendi; varsayılan imaj 743 MB (önce 611 MB; `LLM_EXTRAS` boşken
  611 MB). Modelli eval koşulmadı (Groq isteği değişmedi).
- **Elle deneme (2026-11-13, kod `c7fec04`, varsayılan imaj; Gemini ücretsiz katman anahtarı,
  OpenAI uyumlu adres, `LLM_PROVIDER=openai`; `02_ihlalli` sözleşmesi, `POST /reviews`, `data/`
  kopyasında; model başına tek koşu, ölçüm değil):**
  1. `gemini-3.5-flash-lite`: başlangıç kontrolü geçti; tekil istekler kabul edildi
     (`max_completion_tokens`, beş tool şeması) ve model ilk tool çağrısını yaptı. Tool sonucunu
     geri gönderen ikinci istek her kuralda 400 aldı (`INVALID_ARGUMENT`, "Function call is
     missing a thought_signature"): Gemini 3.x tool çağrısının düşünce imzasının geri
     gönderilmesini istiyor, langchain-openai 1.7.4 göndermiyor. Sonuç: 200, 7 sn, 5 kuralın 5'i
     `no_finding` ile avukat kuyruğunda; hiçbir kural avukatsız geçmedi. İnceleme bu modelle
     çalışmadı, sistem güvenli tarafta kaldı.
  2. `gemini-2.5-flash-lite` ve `gemini-2.5-flash`: model listesinde var, her çağrı 404 →
     `config` → inceleme `failed`, API 502, audit'te kod 404, ≈ 1 sn.
  **Sonuç:** Groq ve Ollama dışında hiçbir sağlayıcı bir incelemeyi uçtan uca tamamlamadı.
  Gerçek üçüncü sağlayıcıda görülenler: başlangıç, isteğin kabulü, ilk tool çağrısı ve iki hata
  yolu (400 → kural avukata; 404 → inceleme `failed`, 502). **Denenmedi:** OpenAI ve Anthropic
  (anahtar yok); yerel `google_genai` entegrasyonu; çevrimdışı mod bu kodla; modelli eval.
  İlk koşunun token toplamı kaydedilmedi (özet betiği veri kopyasındaki eski incelemelerle
  birlikte saydı; ikinci koşudan önce düzeltildi).
- **Karar (2026-11-13, kullanıcı):** `LLM_PROVIDER` bugün yalnızca `groq` ve `ollama` kabul ediyor;
  başka bir sağlayıcıyı kullanmak isteyen kodu değiştirmek zorunda. Kod sağlayıcıdan ve modelden
  bağımsız hale getirilir: sağlayıcı, model ve anahtar yalnızca env ile verilir. "Ölçüldü" denebilen
  tek yapılandırma yine Groq `openai/gpt-oss-120b`'dir; diğerleri "çalışır, ölçülmedi" diye yazılır.
- **Ne zaman:** Adım 18b'nin `--resume` koşusu bittikten sonra. Bu adım `src/`, `pyproject.toml` ve
  `uv.lock`'ı değiştirir; önce yapılırsa holdout önbelleği geçersiz olur ve "tek atış" bozulur.
- **Değişmeyenler:** `src/config.py` ve `src/llm.py` dışındaki kod (agent, graph, tool'lar,
  `verify`, maskeleme) zaten yalnızca `BaseChatModel` görür; dokunulmaz. Groq ve Ollama'ya giden
  istek aynı kalır (aynı parametreler, aynı varsayılan modeller, `GROQ_API_KEY` ve
  `OLLAMA_BASE_URL` çalışmaya devam eder). Maskeleme sırası ve "modele maskesiz PII gitmez" kuralı
  sağlayıcıdan bağımsızdır.
- **Tasarım:**
  1. **Sağlayıcı listesi kalkar.** `LLM_PROVIDER` `init_chat_model`'in `model_provider` değeridir;
     boşsa `groq`. Config yalnızca biçimine bakar (küçük harf, rakam, `_`, `-`). `Provider` tipi
     `Literal` olmaktan çıkar.
  2. **Bilinen sağlayıcı tablosu** (`groq`, `ollama`) kalır ama kapı değildir: varsayılan model,
     birim fiyat ve o sağlayıcıya özel parametreler (`reasoning_effort`, `num_ctx`, `seed`,
     `reasoning=False`) buradadır. Tabloda olmayan sağlayıcıda `LLM_MODEL` zorunludur (boşsa
     `ConfigError`), eşzamanlılık varsayılanı 1'dir, eval'in maliyet tahmini "bilinmiyor" yazar.
  3. **Anahtar ve adres:** iki yeni env. `LLM_API_KEY` verilmişse `api_key` olarak, `LLM_BASE_URL`
     verilmişse `base_url` olarak geçirilir (OpenAI uyumlu servisler bu ikisiyle çalışır). `groq`
     için anahtar `GROQ_API_KEY` ya da `LLM_API_KEY`'dir; ikisi de yoksa bugünkü hata verilir.
     Birden çok kimlik bilgisi isteyen sağlayıcılar (Bedrock, Azure, Vertex) kendi standart env
     değişkenlerini kendileri okur; bu, "env'e yalnızca `config.py` dokunur" kuralının yazılı
     istisnasıdır. `LLM_API_KEY` `GROQ_API_KEY` gibi loglanmaz, `repr`'de görünmez.
  4. **Ortak parametreler:** tabloda olmayan sağlayıcıya yalnızca `max_tokens`, `timeout`,
     `max_retries` ve `temperature` verilir. `LLM_TEMPERATURE` yeni env'dir: boşsa 0; `none` ise
     parametre hiç gönderilmez (bazı modeller `temperature`'ı 400 ile reddediyor).
  5. **Hata sınıflandırma** istisna tipine değil HTTP durum koduna bakar (istisnanın
     `status_code`'u, yoksa `response.status_code`'u): 401 / 403 / 404 → `config`, 429 →
     `rate_limit`, gerisi `other`. Groq ve Ollama için sonuç bugünküyle aynı kalmalıdır (Ollama'da
     aynı kalmadı: 401 / 403 → `config`, 429 → `rate_limit`; aşağıda "Planlama kararları 2" ve
     "Ön doğrulama sonuçları 12").
  6. **Başlangıç kontrolü:** model, giriş noktaları (API ve eval koşucusu) açılırken bir kez
     kurulur. Paket kurulu değilse ya da kimlik bilgisi eksikse uygulama, eksik paketin ya da
     değişkenin adını söyleyen `ConfigError` ile durur; hata ilk incelemeye kalmaz.
  7. **Paketler:** `pyproject.toml`'a isteğe bağlı gruplar eklenir (`openai`, `anthropic`,
     `google`); hepsi `uv.lock`'ta kilitlenir. İmaj varsayılan olarak `openai` ve `anthropic` ile
     kurulur; başka grup Docker build argümanıyla (`LLM_EXTRAS`) eklenir. Grubu olmayan bir
     sağlayıcı için kullanıcı paketi `pyproject.toml`'a kendisi ekler; README bunu yazar.
  8. **`http_client`** (testin sahte taşıyıcısı) yalnızca onu kabul ettiği bilinen sağlayıcılarda
     geçirilir (`groq`, `openai`); diğerlerinde bugünkü gibi `ValueError`.
- **Planlama kararları (2026-11-13; kod okunarak bulunan boşluklar):**
  1. `tests/test_config.py` `test_load_settings_unknown_provider_raises_config_error`
     (`LLM_PROVIDER=openai` → `ConfigError`) tasarımla çelişir ve değişir: modelsiz `openai` hatası
     `LLM_MODEL`'i söyler; biçimi bozuk sağlayıcı adı `LLM_PROVIDER`'ı söyler. Groq / Ollama isteğini
     ölçen bir test değildir; aşağıdaki "Eval" kuralını tetiklemez. Bunun dışında mevcut Groq /
     Ollama testlerine dokunulmaz (`git diff` ile denetlenir).
  2. Durum koduna göre sınıflandırma Ollama'da davranışı genişletir: bugün yalnızca 404 `config`,
     yeni kuralda 401 / 403 de `config` (kimlik doğrulamalı uzak Ollama). Kabul edildi;
     `agent_design.md` §6'ya yazılır.
  3. `LLM_PROVIDER=ollama` iken `LLM_BASE_URL` verilirse `ConfigError` ("`OLLAMA_BASE_URL` kullan"):
     compose `OLLAMA_BASE_URL`'i her zaman doldurduğu için değer sessizce yok sayılırdı.
  4. Paket grupları `[project.optional-dependencies]` altındadır (Docker `uv sync --extra …`).
     `langchain-openai` `dev` grubuna da girer; `openai` sahte taşıyıcı testi yerelde atlanmadan
     koşar. "Paket kurulu değil" testi import'u monkeypatch ile düşürür (ortamda hangi grubun
     kurulu olduğuna bağlı kalmaz).
  5. Başlangıç kontrolü `create_app`'e değil iki `main()`'e konur (`src/api.py`, `evals/run.py`);
     `--prepare-only` modeli kurmaz.
  6. Commit'ler üçe bölünür ("önce doküman"): `docs:` (tasarım dokümanları), `feat:` (test + kod +
     paketler), `docs:` (README, karar dokümanı, `CLAUDE.md`).
- **Kod yazmadan önce doğrulanacaklar** (LangChain dokümanı ve küçük betikle; tahminle yazılmaz):
  `init_chat_model`'in paket eksikken verdiği hata ve mesajı; `max_tokens` / `timeout` /
  `max_retries` / `api_key` / `base_url` adlarının `openai`, `anthropic` ve `google` entegrasyonlarında
  kabul edildiği; bu üç SDK'nın hata sınıflarında durum kodunun hangi alanda durduğu; yeniden deneme
  sayısının anlamı (Groq'ta `max_retries=3` dört istek demek). Tutmayan varsayım tasarıma ve bu
  adıma yazılır.
- **Ön doğrulama sonuçları (2026-11-13; model ve gerçek sağlayıcı çağrısı yok):** scratchpad'de ayrı
  ortamda (langchain 1.4.10, langchain-openai 1.7.4, langchain-anthropic 1.7.10,
  langchain-google-genai 4.5.0) altı grup betikle denendi; her grubu ayrı bir agent yeniden
  üretmeye / çürütmeye çalıştı. Tasarımı değiştirenler:
  1. **Hata sınıflandırma (tasarım 5 tutmadı).** Google 401 / 403 / 404 / 429'da üstteki istisnada
     `status_code` da `response` da yok; kod yalnızca `__cause__` üzerinde. Yeni kural: önce
     `langchain_core.exceptions` sınıfları (`ModelAuthenticationError`, `ModelPermissionDeniedError`,
     `ModelNotFoundError` → `config`; `ModelRateLimitError` → `rate_limit`; kilitli 1.7.7'de var;
     openai / anthropic / google sarmalayıcıları bunlardan türüyor, groq ve ollama türemiyor), sonra
     durum kodu: istisnanın ve `__cause__`'un `status_code`'u, yoksa `response.status_code`'u, yoksa
     tam sayı `code`'u (`bool` ve metin `code` sayılmaz; openai'de `code` metindir). `__context__`
     yürünmez. Mevcut 12 satırlık test tablosu bu kuralla değişmeden geçiyor. Aynı okuma
     `src/agent.py` `_error_payload`'da da kullanılır (yoksa audit satırında kod boş kalır); bu,
     "yalnızca `config.py` ve `llm.py` değişir" maddesinin yazılı istisnasıdır.
     Bilinen yanlış sınıflar: OpenAI'de kota / bakiye bitmesi 429 → `rate_limit`; Gemini'de
     faturalama hatası 400 → `other`; `int` `status_code` taşıyan sağlayıcı dışı istisna da
     sınıflanır.
  2. **`LLM_TEMPERATURE=none` (tasarım 4 eksikti).** Parametreyi atlamak yetmez: Google'da atlanan
     `temperature` 0,7 gider. `temperature=None` açıkça verilir (üç entegrasyonda da gövdede alan
     yok).
  3. **Başlangıç kontrolü (tasarım 6 eksikti).** Anahtarsız kurulum OpenAI ve Google'da hata verir
     (ilgisiz üç istisna tipi, mesaj `LLM_API_KEY`'i değil sağlayıcının kendi değişkenini söyler),
     Anthropic'te vermez (ilk çağrıda 401). Boş anahtar `None` olarak geçerse entegrasyon kendi
     env değişkenine sessizce düşer. Karar: bilinen tablo `openai` ve `anthropic` ile genişler
     (anahtar zorunlu; `LLM_API_KEY` yoksa `ConfigError`); tabloda olmayan sağlayıcıda anahtar
     isteğe bağlıdır, kurulum hataları (`ImportError`, `ValueError`, diğerleri) `ConfigError`'a
     çevrilir. Eksik pakette mesaj, paket grubunu ve build argümanını da söyler.
  4. **Sağlayıcı adı.** LangChain adı `strip` etmez; `-`'yi `_` yapar ve küçültür. Config aynı
     normalleştirmeyi yapar (`strip`, küçük harf, `-` → `_`) ve `[a-z][a-z0-9_]*` ister; yoksa
     `Groq` ya da `google-genai` kendi tablomuzu ıskalar. Bilinmeyen ad LangChain'de `ValueError`
     (30 adlık kapalı liste) verir.
  5. **`http_client` (tasarım 8 doğrulandı).** `groq` ve `openai`'de eşzamanlı yolda geçerli;
     Anthropic'te çağrı anında `TypeError`, Google'da sessizce yok sayılıp gerçek ağa çıkıyor →
     ikisinde de baştan `ValueError`.
  6. **`max_retries`'ın anlamı sağlayıcıya göre değişir.** groq / openai / anthropic: 3 → 4 istek;
     `google_genai`: 3 → 3 istek. `timeout` deneme başınadır, toplam süre sınırı değildir;
     Anthropic `retry-after` ne derse o kadar bekler. Dokümana yazılır, kodla eşitlenmez.
  7. **OpenAI uyumlu servis.** `base_url` isteğin gittiği adrestir (`…/chat/completions`); sınır
     alanı `max_completion_tokens` olarak gider; adında `codex` geçen ya da `gpt-5-pro` ile
     başlayan modeller `/responses`'a gider. `LLM_BASE_URL` verilmişse `use_responses_api=False`
     geçirilir.
  8. **Paketler (tasarım 7 tutmadı).** `google` grubu kilide girince grup seçilmese de herkesin
     kurduğu iki paket değişiyor: `langchain-core` 1.7.7 → 1.8.0, `tenacity` 9.2.1 → 9.1.4
     (langchain-google-genai 4.x → google-genai → `tenacity<9.2`). Yalnızca `openai` + `anthropic`
     grupları eklenince kilitteki 67 kaydın 67'si aynı kalıyor. uv'nin düz `conflicts` ayarı
     çözmüyor (grupsuz kurulum yine 1.8.0 alıyor). Seçenekler ve karar: aşağıda "Açık karar".
  9. **Docker.** `uv sync --frozen` bayat kilidi fark etmiyor → `--locked`. RUN satırı:
     `set -f; set --; for e in $LLM_EXTRAS; do set -- "$@" --extra "$e"; done; uv sync --locked
     --no-dev "$@"` (dash'te denendi, boş liste dahil). Compose'da `${LLM_EXTRAS-openai anthropic}`
     (iki noktasız: boş değer "grup yok" demek) ve iki servis (`app`, `ollama-pull`) aynı build
     argümanını YAML çapasıyla alır.
  10. **Gemini (elle deneme için).** Sağlayıcı adı `google_genai`; ücretsiz katmanda Google
      gönderilen içeriği ürün geliştirmede kullanabilir ve insan inceleyebilir (README'ye yazılır;
      giden metin maskelidir). Ücretsiz katmanın model başına limitleri açık sayfalarda yok
      (AI Studio'da görünür). Düşünen modellerde düşünme token'ları `max_tokens` içindedir: 2048
      sınırı boş yanıtla (`MAX_TOKENS`) bitebilir ve bu bizde sağlayıcı hatası değil `no_submit`
      olur. Karar: `LLM_MAX_TOKENS` env'i eklenir (boşsa 2048; Groq varsayılanı değişmez).
  11. **Doküman denetiminde çıkanlar.** `--resume` önbelleğinin parmak izi yalnızca sağlayıcı,
      model ve `LLM_NUM_CTX`'i kapsıyordu; `LLM_TEMPERATURE`, `LLM_MAX_TOKENS` ya da
      `LLM_BASE_URL` değişince eski sonuç yeniden kullanılırdı. Üçü parmak izine girer;
      `evals/cache.py` ve testi bu adımın dosyalarına eklenir. `LLM_API_KEY` Ollama'ya geçirilmez
      ve hata değildir. Audit `review_started` sağlayıcı adını yazar, `LLM_BASE_URL`'in host'unu
      yazmaz (OpenAI uyumlu adreste kayıtta `openai` görünür): bilinen sınır, kod değişmez.
  12. **Kod incelemesinde çıkanlar (üç bağımsız inceleme; Groq yolu `HEAD` ile yan yana: model
      alanları 64 ortam, tel üzerindeki istek 36 senaryo, bayt bayt aynı).** (a) `openai` /
      `anthropic`'te `LLM_BASE_URL` boşken entegrasyon `OPENAI_BASE_URL` gibi kendi env
      değişkenini okuyordu; anahtar ve maskeli metin `LLM_BASE_URL`'in hiç söylemediği bir adrese
      gidebilirdi. Karar: bu iki sağlayıcıda adres boşsa resmî adres açıkça geçirilir. Groq'a
      dokunulmaz (`GROQ_BASE_URL` düşüşü `HEAD`'deki gibi kalır; bilinen sınır). (b) Ollama 429
      artık `rate_limit` (eskiden `other`): genel kuralın sonucu, kabul edildi ve dokümana
      yazıldı; yukarıdaki "Planlama kararları 2" yalnızca 401 / 403 diyordu. (c) Groq ile
      `LLM_TEMPERATURE=none` kurulamaz (ChatGroq `None` kabul etmez, atlanırsa 0,7 gönderir):
      `config.py`'de `LLM_TEMPERATURE`'ı söyleyen `ConfigError`. (d) `LLM_BASE_URL` `repr`'de
      görünmez.
  **Doğrulanamayanlar:** gerçek bir OpenAI uyumlu sunucunun `max_completion_tokens`'ı kabul edip
  etmediği; Gemini'nin geçersiz anahtarda 400 mü 401 mi döndüğü; eşzamansız ve akışlı yollar
  (Anthropic, Google); Linux imaj boyutu.
  **Kaza (kayıt):** ilk doğrulama koşusunda bir agent başarısız bir `cd`'den sonra `uv lock`'ı
  depoda çalıştırdı ve `uv.lock`'ta `langchain-core`'u 1.8.0 yaptı; `git checkout -- uv.lock` ile
  geri alındı (`.venv` 1.7.7'de kaldı). İkinci koşudan sonra `git status` yalnızca bu dosyayı
  gösteriyor.
- **Karar (2026-11-13, kullanıcı): (a).** Kilide yalnızca `openai` ve `anthropic` grupları girer;
  `google` grubu yoktur, imajın varsayılanı `LLM_EXTRAS="openai anthropic"` olarak kalır. Gemini
  denemesi OpenAI uyumlu adres üzerinden yapılır; anahtarı `.env`'e kullanıcı yazar. Tasarım 7'deki
  "`google` grubu" bu kararla düşer. Tartılan seçenekler: (a) kilitte yalnızca `openai` + `anthropic`; Gemini
  OpenAI uyumlu uç üzerinden denenir (`LLM_PROVIDER=openai` + `LLM_BASE_URL`), yerel
  `google_genai` için paketi kullanıcı ekler ve README bunun `langchain-core`'u değiştirdiğini
  yazar. (b) `google` grubu + sabitleme grubu (`pins`, `default-groups`, `conflicts`): varsayılan
  kurulum aynı kalır, Google imajı `--no-group pins` ile ayrı bir `langchain-core` üzerinde
  çalışır; sabitler elle güncel tutulur. (c) `google` grubu düz eklenir, `langchain-core` herkes
  için 1.8.0 olur ve Groq eval'i yeniden koşulur.
- **Önce dokümanlar:** `tech_stack.md` (model kurulumu satırı, env listesi, paketler, "sağlayıcıya
  özel parametreler"), `spec.md` (FR-12; "kapsam dışı"ndaki "Groq dışındaki bulut sağlayıcıları"
  maddesi kalkar, yerine "ölçülmedi" sınırı gelir), `architecture.md` (ADR-010; "veri nereye
  gidiyor" tablosunda "Groq" yerine "seçilen sağlayıcı"; sıfır veri saklama notu Groq'a özeldir ve
  öyle yazılır), `agent_design.md` §6 (hata sınıfları), `code_standards.md` (anahtar kuralı, env
  istisnası), `evals.md` §2 (zorunlu testler), `decisions.md` (3. ve 4. soru), `README.md`,
  `.env.example`, `CLAUDE.md` "Proje".
- **Dosyalar:** `src/config.py`, `src/llm.py`, giriş noktaları (`src/api.py`, `evals/run.py`;
  yalnızca başlangıç kontrolü), `evals/metrics.py` (maliyet "bilinmiyor"), `pyproject.toml`,
  `uv.lock`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `tests/test_config.py`,
  `tests/test_llm.py`, yukarıdaki dokümanlar
- **Doğrulama:** Mevcut Groq ve Ollama testleri **değiştirilmeden** geçer (istek gövdesi,
  parametreler, hata sınıfları). Yeni testler: tabloda olmayan sağlayıcı modelle kabul edilir,
  modelsiz `ConfigError`; `LLM_API_KEY` `repr`'de yok; `openai` sağlayıcısı sahte taşıyıcıyla
  kurulur ve istek gövdesinde yalnızca ortak parametreler vardır (`reasoning_effort` yok);
  `LLM_TEMPERATURE=none` iken gövdede `temperature` yok; `LLM_BASE_URL` isteğin gittiği adrestir;
  durum kodu taşıyan sahte istisnalar doğru sınıflanır (401 / 403 / 404 / 429 / 400 / 500, kodsuz
  istisna); kurulu olmayan paket başlangıçta paketin adını söyleyen hatayla durur; graph testi
  sahte modelle üç ayarda da geçer. `docker compose build` varsayılan gruplarla ve `LLM_EXTRAS`
  ile çalışır. pytest, ruff, mypy temiz.
- **Eval:** Groq'a giden istek değişmediği için ortak kuraldaki tam eval bu adımda koşulmaz;
  mevcut Groq testlerinden biri değişmek zorunda kalırsa kural geçerlidir ve koşu kullanıcıya
  sorulur. `src/` değiştiği için 7'lik setin `--resume` önbelleği geçersiz olur (beklenen).
- **Elle deneme:** kullanıcının üçüncü bir sağlayıcı anahtarı varsa tek sözleşmeyle, veri
  kopyasında bir inceleme (kullanıcı onayıyla). Yoksa README'ye "Groq ve Ollama dışında gerçek
  sağlayıcıyla denenmedi" yazılır. **2026-11-13, kullanıcı:** ücretsiz katman Gemini anahtarı
  verilecek. Deneme `feat:` commit'inden sonra, varsayılan imajda, OpenAI uyumlu adres üzerinden
  yapılır (`LLM_PROVIDER=openai`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`); anahtarı
  `.env`'e kullanıcı yazar (`.env` okunmaz); koşudan önce yine sorulur. Sonuç README'ye "tek
  sözleşme, tek koşu, ölçüm değil" diye yazılır.
- **README (Adım 19 ile birlikte kapanır):** sağlayıcı değiştirme bölümü (dört env, paket grubu,
  build argümanı), "ölçüldü / çalışır ama ölçülmedi" ayrımı, maskeli metnin seçilen sağlayıcıya
  gittiği ve o sağlayıcının saklama koşullarının kullanıcının sorumluluğunda olduğu.
- **Commit:** `feat: accept any chat model provider through env`

### [x] Adım 18d — Gemini kendi paketiyle (`google_genai`)
- **Gerçekleşen (2026-11-13):** `514a718` (tasarım dokümanları), `4dec2d8` (test, kod, paketler)
  ve bu commit (sonuç: plan, tasarım dokümanları, README, karar dokümanı, `CLAUDE.md`).
  Doğrulama: `uv run pytest` 1858 geçti (önce 1784), ruff ve mypy temiz. Mevcut testlerden
  tasarımın zorunlu kıldığı iki satır değişti (aşağıda "Doğrulama"); Groq / Ollama beklentisi
  değişmedi. Kilit: herkesin kurduğu iki paket `langchain-core` 1.7.7 → 1.8.0, `tenacity`
  9.2.1 → 9.1.4; eklenen `langchain-google-genai` 4.5.0, `google-genai` 3.3.0 ve 7 geçişli paket.
  İki ayrı inceleme Groq ve Ollama yolunu 1.7.7 ve 1.8.0'da sahte taşıyıcılarla karşılaştırdı
  (giden istekler, ayrıştırılan yanıtlar, hata sınıfları, gerçek graph üzerinde bütün bir
  inceleme): fark yok. Bu sahte taşıyıcıyla karşılaştırmadır, model koşusu değildir. İncelemenin
  bulup düzeltilenleri: boş olmayan `GOOGLE_GENAI_CLIENT_MODE` başlangıçta reddedilir (SDK'nın
  kayıt istemcisi isteği anahtar başlığıyla stdout'a basıyordu); Gemini'ye "çalışır" diyen iki
  doküman satırı (`architecture.md` §6, `tech_stack.md` "Diğer sağlayıcılar") düzeltildi; düşünce
  imzasının tool sonucundan sonra geri gönderildiğini tel üzerinde sınayan test eklendi. Varsayılan
  imaj (`LLM_EXTRAS` verilmemiş: `"openai anthropic google"`) 829 MB (önce 743 MB); `LLM_EXTRAS`
  boşken 611 MB eski kilitte ölçüldü, yeniden ölçülmedi. **Eval:** modelli eval yeniden koşulmadı;
  README'deki ölçülen sayılar `4ccc03d` koduna ve `langchain-core` 1.7.7'ye aittir. `evals.md` §1
  kuralı gereği koşunun yapılıp yapılmayacağı kullanıcıya sorulacak (açıktı; 2026-11-13'te kapandı: Adım 18
  "Ek koşular").
- **Elle deneme (2026-11-13, konteyner içinde, `data/` kopyasında, `POST /reviews`;
  `LLM_PROVIDER=google_genai`, `LLM_MODEL=gemini-3.5-flash-lite`, `LLM_BASE_URL` boş,
  `MAX_CONCURRENCY=1`, Gemini ücretsiz katman anahtarı; eval sözleşmesi `02_ihlalli`
  (`contract-02`), 5 kural; tek koşu, ölçüm değil):**
  1. Sonuç: HTTP 200, 39 sn; inceleme `awaiting_lawyer`, 5 bulgu; eval etiketlerine göre status
     5'te 4 doğru. Bulguların 3'ü avukat kuyruğuna gitti.
  2. `termination_notice`: `non_compliant` (beklenen `non_compliant`), alıntı var, taslağın kod
     kontrolü geçti, `verify` kuyruğa göndermedi. `late_penalty_cap`: `non_compliant` (beklenen),
     taslağın kod kontrolü geçmedi → kuyruk (`proposal_noncompliant`). `insurance_minimum`:
     `non_compliant` (beklenen), taslak geçti, kuyruk yok. `liability_cap`: beklenen `compliant`
     iken `non_compliant` → kuyruk (`missing_proposal`, `run_interrupted`); bu koşunun audit'inde
     `GoogleRateLimitError` 429 ile iki `model_call` satırı var (ücretsiz katman limiti), yani bu
     kuralın koşusu hız limitiyle kesildi. Yanlış status'un kesintiden mi modelden mi geldiği
     **bilinmiyor**. `governing_law`: `not_found` (beklenen), kuyruk (`no_finding`).
  3. OpenAI uyumlu yolu bozan düşünce imzasıyla tool döngüsü gerçek Gemini API'siyle tamamlandı.
  4. Entegrasyon her çağrıda uyardı: "Model 'gemini-3.5-flash-lite' uses fixed sampling
     defaults; the sampling parameter(s) temperature will be ignored." Bu modelde
     `LLM_TEMPERATURE` etkisizdir (`temperature` 0 uygulanmaz); koşuların tekrarlanabilir olması
     beklenmez.
  5. Koşunun token toplamı kaydedilmedi (deneme betiğinin satır süzgeci veri kopyasındaki önceki
     koşuların satırlarını da kattı).
  **Sonuç:** Gemini (`google_genai`) tek sözleşmede denendi, ölçülmedi. Ölçülen tek yapılandırma
  yine Groq `openai/gpt-oss-120b`'dir. **Denenmedi:** OpenAI ve Anthropic (anahtar yok); modelli
  eval `langchain-core` 1.8.0 ile.
  Adım 18c'deki OpenAI uyumlu adres denemesi olduğu gibi kayıtta kalır.
- **Karar (2026-11-13, kullanıcı):** "Gemini'yi ekle, denemek istiyorum." Adım 18c'deki (a) kararı
  (kilitte `google` grubu yok) geri alınır: Gemini OpenAI uyumlu adreste tool döngüsünü
  tamamlayamadı (düşünce imzası); yerel `langchain-google-genai` entegrasyonu imzayı taşır (sahte
  taşıyıcıyla görüldü; gerçek Gemini ile bu adımın elle denemesinde sınanır).
- **Bedeli (bilinerek):** `google` grubu kilide girince herkesin kurduğu iki paket değişir:
  `langchain-core` 1.7.7 → 1.8.0, `tenacity` 9.2.1 → 9.1.4 (Adım 18c "Ön doğrulama 8"). Sabitleme
  grubu (18c seçenek b) kurulmaz; tek kilit, tek `langchain-core`.
- **Tasarım:**
  1. `pyproject.toml`: `google = ["langchain-google-genai>=4.5"]`; paket `dev` grubuna da girer
     (testler gerçek sarmalayıcı hata sınıflarıyla ve `client_args` taşıyıcısıyla koşar).
     `LLM_EXTRAS` varsayılanı `"openai anthropic google"` olur (`docker compose up` Gemini için
     yeniden kurulum istemesin).
  2. Bilinen sağlayıcı tablosuna `google_genai` girer: varsayılan model yok, `LLM_API_KEY` zorunlu
     (boş anahtar entegrasyonu `GOOGLE_API_KEY` / `GEMINI_API_KEY`'e düşürürdü), birim fiyat yok,
     eksik pakette mesaj `google` grubunu söyler. Ortak parametreler dışında bir şey verilmez;
     `http_client` reddi, `temperature=None` ve hata sınıflandırma 18c'de Google'a göre yazıldı.
  3. Ortam düşüşleri kapatılır ya da sınır olarak yazılır (kod yazmadan önce betikle bakılır):
     `GOOGLE_GENAI_USE_VERTEXAI` (kurulumda Vertex'e ve ağa çıkıyor) ve varsa adres değişkeni.
  4. `max_retries=3` Google'da 3 istek demektir; eşitlenmez (18c kararı).
- **Önce dokümanlar:** `architecture.md` ADR-012 (karar ve gerekçe güncellenir), `tech_stack.md`
  (paketler, tablo, `LLM_EXTRAS`), `spec.md` §8, `evals.md` §2 (testler), `.env.example`.
- **Doğrulama:** kilit farkı yalnızca beklenen iki paket + eklenenler; `uv run pytest`, ruff, mypy
  temiz (test takımı `langchain-core` 1.8.0 ile ilk kez koşar); mevcut testlerde tasarımın zorunlu kıldığı iki
  değişiklik dışında bir şey değişmez: `test_common_parameters_only_outside_groq_and_ollama`'nın
  `google_genai` satırı artık `base_url`'i de bekler; `test_load_settings_provider_name_is_normalized`
  ortamına `LLM_API_KEY` eklendi (`google-genai` satırları artık anahtar ister). Hiçbir Groq /
  Ollama beklentisi değişmedi; Groq
  isteği bu adımdan önceki kodla (`b650a97`) yan yana karşılaştırılır (model alanları, tel
  üzerindeki istek); imaj kurulur.
- **Eval:** `langchain-core` değiştiği için `evals.md` §1 kuralı geçerlidir (istek aynı kalsa da
  yanıtın ayrıştırılması bu pakettedir). Koşu kullanıcıya sorulur; koşulmazsa README sayıların
  `langchain-core` 1.7.7 ve `4ccc03d` koduna ait olduğunu yazar. **Gerçekleşen (2026-11-13):**
  koşuldu (Groq, bir tam koşu; Adım 18 "Ek koşular").
- **Elle deneme:** `LLM_PROVIDER=google_genai`, `LLM_MODEL=gemini-3.5-flash-lite` (18c'de seçilen),
  `02_ihlalli`, `data/` kopyasında, tek koşu; anahtar `.env`'deki `LLM_API_KEY` (okunmaz). Sonuç
  olduğu gibi yazılır; boş yanıt (`MAX_TOKENS`) görülürse `LLM_MAX_TOKENS` yükseltilmeden önce
  sorulur.
- **Commit:** `docs:` (tasarım), `feat: add the google_genai provider group`, `docs:` (sonuç).

### [x] Adım 19 — README ve karar dokümanı
- **Gerçekleşen:** `3e55346` (README, karar dokümanı), `1c961e2` (holdout sonucu),
  `01f3f70` (denetim düzeltmeleri) ve bu commit. Doğrulama: temiz klonda modelsiz yürüyüş ve
  gerçek modelle tek uçtan uca akış ("Durum"); README tabloları sonuç dosyalarıyla betikle
  karşılaştırıldı; karar dokümanı 5 soruyu sırasıyla yanıtlıyor, 816 sözcük.
- **Ne:** README (kurulum, eval sonuçları ve önce/sonra tablosu, düşük Groq limiti notu, bilinen
  sınırlar, "daha fazla vaktim olsaydı"). Karar dokümanı: CASE.md'deki 5 soru sırasıyla, en fazla 2 sayfa.
- **README — ücretsiz Groq anahtarıyla:** tam eval'in günlük limite sığmadığı ve çalışacak komut
  açıkça yazılır: `docker compose run --rm -e MAX_CONCURRENCY=1 app python -m evals.run --limit 3`.
  N=3: Adım 18'de ilk üç sözleşme ≈ 105K token tuttu (2. koşu), 200K günlük sınır uygulanıyorsa
  sığar; tam koşu 206–242K ile sığmaz. Adım 18'in iki tam koşusu limitsiz bitti, aynı gün
  holdout koşusu limite takıldı (geçerli günlük sınır bilinmiyor); README bunu koşullu yazar.
  Yanında iki seçenek daha:
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

### [x] Adım 19b — Madde listesi denemesi (geri alındı)
- **Neden (2026-11-13):** Groq'ta sözleşme 4 tek başına (`main` kodu) aynı iki kuralı yine
  `not_found` döndürdü (iki tam koşuyla birlikte üçüncü kez). Audit: `termination_notice`
  "fesih bildirim süresi", "fesih", "bildirim süresi" diye aradı, madde "Madde 11 - Sözleşmenin
  Sona Ermesi … haber vererek … sona erdirebilir"; `confidentiality` "gizlilik", "gizli bilgi",
  "confidential", "gizlilik yükümlülüğü" diye aradı, madde "Madde 9 - Sır Saklama … sırları".
  Sebep: yalnızca anahtar sözcükle arama ve aynı kökü değiştirerek deneyen model. İki kural da
  avukat kuyruğuna gitti.
- **Fikir:** her kural koşusunun ilk `search_contract` sonucu bütün maddelerin ilk satırını da
  listeler (80 karakter, en fazla 60 madde); başka sözcüklerle yazılmış madde de görünür.
- **Kanıt:**
  1. Groq, sözleşme 4 tek başına, erken biçim (liste **her** aramada): 5 / 5 status, kaçan iki
     madde bulundu, 50.854 token; tek koşu.
  2. Son biçim Groq'ta tamamlanmadı: tam eval 1. sözleşmeden sonra hız limitine takıldı (Adım 18
     "Ek koşular" 4).
  3. Anthropic `claude-sonnet-5-5` tam eval, listesiz (`main`, `a9ad2ed`) ve listeli (deneme
     kodu, `8c3ae06`): 35 kuralın 35'inde status, madde, taslak sonucu ve kuyruk aynı (ikisinde
     de 35 / 35; arama isabeti 32 / 32; yönlendirme 13 / 35; öneri uygunluğu 8 / 11). Tek içerik
     farkı: listeli koşuda sözleşme 4 `payment_term`'e ek `ambiguous_value`. Arama çağrısı
     89 → 80; girdi token'ı 593.334 → 733.345 (+%24); süre 277 → 261 sn.
  İncelemelerin kaydettiği bedel: bir maddenin ilk satırındaki, taramanın yakalamadığı bir
  talimat her kurala ulaşır.
- **Karar (kullanıcı):** geri alındı. Maddeyi zaten bulan modelde faydası yok, bedeli girdi
  token'ının yaklaşık dörtte biri, Groq'taki tek olumlu gözlem doğrulanmadı. İlk eklenecek şey
  yine embedding'dir (ADR-001).
- **Nerede:** dokümanlar, kod, testler (1886 geçti) ve incelemeler `exp/clause-outline` dalında;
  listeli Anthropic sonucu yalnızca o dalda
  (`evals/results/20261113-221845_anthropic_claude-sonnet-5-5.json`). `main`'in kodu bu
  denemeyle değişmedi (testler 1858).
- **Eksik:** aynı modelde (Groq) son biçimin ölçümü.

### [x] Adım 19c — Sonnet koşusunun gösterdiği üç hatanın düzeltilmesi ve yeniden ölçüm
Bu adım yeni bir oturumda, bu metinden çalıştırılmak üzere yazıldı (kullanıcı, 2026-11-14). Önceki
oturumun konuşması gerekmez; gereken her şey burada ve aşağıdaki dosyalardadır.

- **Kullanıcının isteği (2026-11-14):** aşağıdaki üç hata için çözüm; ardından Anthropic
  `claude-sonnet-5-5` ile tam eval ve sonucun mevcut Sonnet koşusuyla karşılaştırılması. Plan
  `fable-orchestration` skill'iyle yürütülür.
- **Başlangıç durumu:** `main` = bu planın commit'i; kod `598a948` ile aynı (1858 test). Karşılaştırma
  tabanı: `evals/results/20261113-223738_anthropic_claude-sonnet-5-5.json` (bugünkü `main` kodu,
  `LLM_TEMPERATURE=none`, 35 / 35, yönlendirme 13 / 35, öneri uygunluğu 8 / 11, garanti edilmeyen
  PII 2 / 5).
- **Başlarken kontrol:** `git status` temiz (yalnızca `?? .claude/`) ve `git log -1` bu planın
  `docs:` commit'i olmalı; değilse durulur ve sorulur. Oturum bu çalışma kopyasında açılır
  (worktree ya da klon değil): skill `.claude/skills/fable-orchestration/SKILL.md`'dedir ve
  izlenmez; yüklenemezse aşağıdaki "Yürütme" tablosu yeterlidir. `.claude/` hiçbir commit'e
  girmez: `git add` dosya adlarıyla ya da `git add -A -- . ':!.claude'` ile yapılır. Dal:
  `git switch -c step-19c` (`main`'den).
- **Araştırma kayıtları (commit edilmez, depoda izlenmeyen klasör):** `.claude/plans/19c/`
  altında üç rapor (`RESULT-drafts.md`, `RESULT-unverifiable.md`, `RESULT-pii.md`) ve
  araştırmada koşulan betikler (`drafts/`, `unverifiable/`, `pii/`). Klasör yoksa adım yine
  yürütülebilir; aşağıdaki özetler yeterlidir, betikler yeniden yazılır. Notların içindeki
  scratchpad yolları geçersizdir; betikler `.claude/plans/19c/<konu>/` altındadır.

#### Üç hata ve kök nedenleri (2026-11-14 araştırması; model çağrılmadan, kod koşularak)
- **A — Kod denetiminden kalan taslaklar.** Sonnet koşusunda üç satır: sözleşme 2 ve 7
  `late_penalty_cap`, sözleşme 4 `payment_term` (`code_check=failed` → `proposal_noncompliant`).
  Aynı kural Groq (2026-11-13) ve Gemini denemesinde de kaldı.
  - *Koşularak gösterildi:* `_proposal_check` (`src/verify.py` ≈988–994) taslaktaki kuralın
    türündeki değerlerin **hepsinin aynı** olmasını ister (`_single`). Prompt ise "maddenin tamamı"nı
    ister (`src/prompts/rule_agent.md` 4. adım). Maddenin tamamı kurala uyan ikinci bir değer taşır:
    gecikme cezasında günlük oran (%1) ile üst sınır (%10) → okunan `[1, 10]` → `failed`;
    ödemede "otuz (30) gün … ithal 45 gün" → `[30, 30, 45]` → `failed`. Yani uygun taslak
    reddediliyor; çıkarım (`extract`) doğru okuyor, reddeden tasarımdaki tek değer şartı.
    Başlık satırı ("## Madde 5 - …"), "kırk beş (45)" yazımı ve IBAN rakamları kalmaya yol açmıyor.
  - *Çıkarım (gösterilmedi):* Sonnet'in üç taslağının metni yok (eval geçici klasörü silindi).
    Sözleşme 2 ve 7 büyük olasılıkla bu biçimdeydi. Sözleşme 4'te taslak ithal süresini 45
    yazdıysa aynı durum; 75'i koruduysa ya da andıysa taslak gerçekten uygunsuzdur ve kalması
    doğrudur.
- **B — Maskelenmeyen iki kişisel veri (sözleşme 5, garanti edilmeyen 2 / 5).** Modelden bağımsız.
  - *Koşularak gösterildi:* kaçan iki değer iki addır: "Elif Soylu" (gövdede, "depo sorumlusu"
    unvanından sonra; madde 11 ve 12) ve "Kemal Uçar" (etiketsiz imza satırı: `Kemal Uçar\nİmza:`).
    Ad keşfi yalnızca etiketlerden başlar ("Ad Soyad:", "Yetkili:", "temsilen" …;
    `src/prepare/pii.py` `_LABEL`, `_discover_names`); iki ad da hiçbir etiketin yanında geçmez.
    Bulunmuş adın bütün metinde yeniden aranması zaten var ve çalışıyor (pii.py ≈711–723); sorun
    keşifte.
- **C — Kodun denetleyemediği taslak avukat kuyruğuna düşmüyor.** Sözleşme 5 `governing_law`
  (Sonnet ve Groq'un son iki koşusu): `code_check=unverifiable`, `needs_review=false`.
  - *Koşularak gösterildi:* `_verify_one` yalnızca `failed` için sebep ekler. `check_decision`
    yalnızca kuyruktaki bulgular için karar ister; avukat taslağa hiç karar vermeden sözleşmeyi
    kapatabilir. Karar verilmemiş taslak raporda `proposals[]` içinde `proposal_action: null` ile
    durur; `requested_changes`'e **girmez**. İhlali kelimesi kelimesine tekrar eden bir taslak da
    aynı sonucu alır. İkinci etki: `check`'i olmayan bir kuralda (acme `liability_cap`,
    acme `governing_law`, globex `confidentiality`) **yanlış** bir "uygun değil" kararı, model
    taslak yazdıysa kuyruğa düşmez.

#### Kararlar (her biri tek öneri; gerekçesiyle)
1. **C için: yeni sebep `proposal_unverifiable`.** `code_check == "unverifiable"` olan her bulgu
   kuyruğa girer; mevcut karar kuralı (`missing_action`, `missing_proposal_action`) avukatı taslağa
   karar vermeye zorlar. Reddedilenler: karar ucunda kuyruk dışı ikinci bir "karar şart" sınıfı
   (eval'de görünmez, tek işaret ilkesini bozar); yalnızca arayüzde işaret (kimseye yükümlülük
   getirmez). Bedeli: koşu başına bir "gereksiz yönlendirme" daha.
2. **A için: taslak denetimi "taslakta kuralı çiğneyen değer yok" olur.** `_proposal_check`'te
   `_single` şartı yerine: en az bir değer var, okunamayan ifade yok, **her** değer kurala uyuyor.
   Değişiklik tek yönlüdür (yalnızca `failed` → `passed`; bugün geçen her taslak yine geçer).
   Reddedilen / ertelenen: prompt'u sıkılaştırmak (her kuralın taslağını değiştirir, etkisi
   denetim değişikliğinden ayrılamaz; bu adımda **yapılmaz**); `propose_change`'te geri bildirim
   döngüsü ("tool yalnızca şemayı denetler" tasarımını değiştirir; yalnızca bu adımdan sonra
   gerçekten uygunsuz taslak kalırsa ayrı adım olur). Bilinen sınır (dokümana yazılır): kod hangi
   değerin "düzenlenen" değer olduğunu bilmez ve yalnızca okuduğu değerleri denetler; okunan her
   değer kurala uyduğu sürece, üst sınırı silen ya da kuralı değer olmayan bir ifadeyle delen
   taslak (koşularak gösterildi: "… ancak 20 günü aşan gecikmede ceza sınırsızdır", "… 45 günü
   izleyen ayın sonunda") geçer ve kuyruğa düşmez; bugün de tek değerli taslakta aynısı geçer.
   Böyle bir taslak avukat kabul etmedikçe değişiklik talebine girmez (`src/report.py`).
3. **B için: iki yeni keşif yolu, garanti listesi genişlemez.**
   - *E-posta bağı:* bulunan bir e-postanın yerel kısmı (`ad.soyad`) aynı satırdaki büyük harfle
     başlayan sözcüklerle, Türkçe harf katlamasıyla eşleşirse o sözcükler addır ("elif.soylu@" →
     "Elif Soylu"). Birim sözcükleri (Birimi, Ekibi, Departmanı, Müdürlüğü, Servisi) içeren
     eşleşme atlanır (`satis.birimi@` "Satış Birimi"ni maskelemesin). Sözleşme 5'te e-posta yalnızca madde 11'de
     adla aynı satırdadır; madde 12'deki "Elif Soylu", bulunan adın bütün metinde yeniden
     aranmasıyla maskelenir.
   - *Etiketsiz imza satırı:* satırın tamamı 2–4 sözcüklük bir ad dizisiyse, şirket işareti ve
     unvan içermiyorsa ve hemen sonraki satır "İmza" ile başlıyorsa o satır addır. "Kaşesi" ve
     "Mührü" **yalnızca bu yeni yolda** form sözcüğü sayılır (`Tedarikçi Kaşesi\nİmza:`
     maskelenmesin); genel `_FORM_WORDS` listesine eklenmez, etiket yolu değişmez (eklenirse
     "Yetkili: Ahmet Yılmaz Kaşesi" bugünkünden farklı maskelenir ve fark testi değişmezi bozulur).
   - İki ad `labels.json`'da `pii_best_effort`'ta **kalır** (Adım 17b'deki gibi; garanti iddiası
     doğmaz, sözleşme özeti değişmez). Reddedilenler: unvandan sonra gelen diziyi ad saymak
     (koşularak gösterildi: "Haftalık Raporu", "İstanbul Ofisinde" maskeleniyor); "rol: ad," kalıbı
     (yalnızca holdout'u etkiler, "Acme Teknoloji,"yi maskeler; ayrı karar).
   - Bilinen fazla maskeleme (dokümana yazılır): `Acme Teknoloji\nİmza:` gibi şirket işareti
     taşımayan iki sözcüklü şirket adı imza satırında ad sayılır.
4. **Sıra ve ayrıştırma:** C → A → B, her biri kendi `docs:` + `feat:` / `fix:` commit çiftiyle;
   sonra tek Sonnet koşusu. Üçü de modelin gördüğünü ya değiştirmez (A, C: `verify` ajandan sonra
   çalışır) ya da yalnızca sözleşme 5'in üç parçasını değiştirir (B), bu yüzden tek koşuda satır
   satır ayrıştırılabilirler.
5. **Taslak metinleri koşu sırasında saklanır.** Karşılaştırmanın (A) "taslak gerçekten uygunsuz
   mu, denetim mi okuyamadı" sorusunu cevaplayabilmesi için koşucuya `--keep-drafts <dosya>`
   seçeneği eklenir: her `propose_change` çağrısının maskeli `proposed_text`'ini, sözleşme ve kural
   kimliğiyle, **sonuç klasörünün dışındaki** verilen dosyaya JSONL olarak yazar. Sonuç dosyası ve
   önbellek metin tutmamaya devam eder; dosya `.gitignore`'a girer, commit edilmez.
6. **Dal:** iş `step-19c` dalında yapılır; `main`'e karşılaştırma görüldükten ve kullanıcı
   onayladıktan sonra alınır (19b'deki gibi geri dönüş noktası `main`'dir).

#### Alt adımlar
**Durma noktaları.** Alt adımlar `CLAUDE.md`'deki anlamda ayrı plan adımı değildir: 19c-1 → 19c-4
arasında onay için durulmaz. Durulan yerler: (1) 19c-5 başlamadan önce "Ölçüm"deki tek soru;
(2) 19c-6'nın commit'inden sonra (`main`'e alma ve Adım 20 için); (3) bir alt adımın bağımsız
incelemesi buradaki bir kararı değiştiren bulgu verirse (o zaman kullanıcıya sorulur).
**Eval kuralına istisna.** "Her adım için ortak kurallar"daki "davranışı değiştiren her commit'ten
önce eval" bu adımda uygulanmaz: kullanıcı tek bir Sonnet koşusu istedi (2026-11-14); 19c-1 … 19c-3
eval'siz commit edilir, modelli tek koşu 19c-5'tir, arada yalnızca modelsiz `--prepare-only` koşulur.

Her alt adımda: önce doküman, sonra test ve kod; `uv run pytest`, `ruff check .`,
`ruff format --check .`, `mypy src evals` temiz; bağımsız inceleme; sonra commit. `verify` ve
maskeleyici çekirdek modüldür: davranışları hakkında söylenen her şey koşularak gösterilir ve
**eski kodla fark testi** olmadan commit edilmez.

- **19c-1 — `proposal_unverifiable` (C).**
  - *Dokümanlar:* `agent_design.md` §5 sinyal tablosu ve "Taslak kontrolü" tablosu (unverifiable
    satırı "Yok" değil artık), §6 hata tablosundaki taslak satırı; `architecture.md` §3 "Veri akışı" adım 5
    (verify), §3 "Avukat kararı" (unverifiable taslak her zaman kuyruktadır), ADR-003 "Sınır"; `spec.md` §4
    Senaryo 1 "Avukat kuyruğuna düşen durumlar", §5 FR-4 ve §8 "Bilinen sınırlar"ın ilk maddesi; `evals.md` §2 "Taslak kontrolü" ve §4'e "önceki
    koşular değişiklikten öncedir" notu (eski sayılar yeniden yazılmaz).
  - *Kod:* `src/models.py` `ReviewReason` literal'ine `proposal_noncompliant`'tan hemen sonra
    `proposal_unverifiable`; `src/verify.py` `_verify_one`'da `elif code_check == "unverifiable"`;
    `src/static/index.html` `REASONS` haritasına etiket ("Taslak kodca doğrulanamadı"). CSP özeti
    çalışma zamanında hesaplanır, saklı özet yoktur. Not: `proposal_unverifiable` aynı zamanda
    mevcut bir eval metriğinin adıdır (`metrics.after.proposal_unverifiable`); dokümanda ve
    karşılaştırmada "sebep `proposal_unverifiable`" ile "Öneri uygunluğu (unverifiable)" ayrı
    adlandırılır.
  - *Değişecek mevcut testler:* `tests/test_verify.py`
    `test_verify_proposal_rule_without_check_unverifiable_not_queued` (beklenti tersine döner),
    `test_verify_non_compliant_masked_number_not_signalled` (check'siz kural + taslak kullanıyor),
    `tests/test_eval_run.py` `test_run_broken_hard_threshold_exits_3_with_table_and_file` (eşiği
    başka yoldan kırar: `liability_cap`'te yanlış "uygun"). Yeniden bakılacak dört API testi
    (check'siz kural + taslaktan geçenler): `test_role_based_presentation_review_and_audit`,
    `test_response_fixed_field_lists_per_role`,
    `test_report_mask_fully_opened_for_lawyer_and_kept_for_viewer[accept]` ve `[edit]`.
  - *Yeni testler:* sebep "uygun değil" ve "uygun" bulgudaki taslakta eklenir; `enabled=False`
    iken eklenmez; `passed` / `failed` taslakta eklenmez; sebep sırası (literal sırası); karar ucu
    kuyruktaki unverifiable taslak için `missing_proposal_action` verir; `REASONS` haritasında her
    `ReviewReason` için etiket var.
  - *Fark testi:* eski `verify` (`git show main:src/verify.py`) ile yenisi rastgele üretilmiş
    ≥ 60.000 girdide yan yana; çıktılar yalnızca `enabled` ve yeni `code_check == "unverifiable"`
    olan satırlarda farklıdır ve fark tam olarak "eski sebepler + yeni sebep (literal sırasında),
    `needs_review=True`"dir. Sebebi yanlış sıraya koyan bir mutant yakalanmalıdır.
  - *Commit:* `docs: design the proposal_unverifiable review reason` ·
    `feat: queue findings whose draft the code cannot check`
- **19c-2 — Taslak denetimi (A).**
  - *Dokümanlar:* `agent_design.md` §5 "Taslak kontrolü" (`passed` tanımı; `failed`'den "birden çok
    farklı değer var" çıkar; gerekçe; yukarıdaki bilinen sınır); `evals.md` §2 "Taslak kontrolü"
    satırı ve metrik tablosunda "Öneri uygunluğu"nun anlamı; `spec.md` §5 FR-4'teki taslak denetimi
    cümlesi ("sayıyı" → "sayıları").
  - *Kod:* `src/verify.py` `_proposal_check`: `not _single(extraction)` yerine
    `not extraction.values or extraction.unreadable`; `_evaluate` zaten her değer geçerse `True`
    döner. Başka satıra dokunulmaz.
  - *Değişecek mevcut test:* `tests/test_verify.py` taslak denetimi tablosunda
    "Bildirim 30 ila 60 gün önceden yapılır." (≥ 30 gün) `failed` → `passed`.
  - *Yeni testler (hepsi araştırmada koşuldu):* günlük %1 + üst sınır %10 (≤ %10) geçer; günlük %1
    + %20 kalır; "otuz (30) gün … ithal … 45 gündür" (≤ 45 gün) geçer; aynısı 75 ile kalır;
    "(önceki 75 gün yerine)" kalır; "45 iş günüdür" kalır (okunamayan); "%10 (yüzde on)" geçer;
    "15 ila 60 gün" (≥ 30) kalır.
  - *Fark testi:* (1) eski ve yeni `_proposal_check` her eval ve holdout maddesi × iki tenant'ın
    her `check`'li kuralı (araştırmada 9 sözleşme dosyasında 945 hücre, 8'i değişti;
    `evals/holdout/expected.md` sözleşme değildir, sayıma girmez) ve rastgele üretilmiş taslaklar
    üzerinde; değişen her hücrede eski sonuç `failed`, yeni sonuç `passed` olmalı ve hücre "en az
    iki farklı değer, okunamayan yok, her değer kurala uyuyor" koşulunu sağlamalıdır; başka yönde
    tek bir fark hatadır. (2) `verify`'ın tamamı, 19c-1'in `feat:` commit'indeki `src/verify.py`
    (19c-2 commit'inden önce `git show HEAD:src/verify.py`) ile yan yana: fark yalnızca
    `proposal.code_check`'in `failed` → `passed` olduğu satırlardadır; bu satırlarda
    `review_reasons`'tan yalnızca `proposal_noncompliant` düşer ve `needs_review` kalan sebeplerden
    yeniden hesaplanır; başka alan değişmez.
  - *Commit:* `docs: redefine the draft check as "no value breaks the rule"` ·
    `fix: accept drafts that keep the clause's other compliant values`
- **19c-3 — Ad keşfi (B).**
  - *Dokümanlar:* `agent_design.md` "Girdi güvenliği" → "PII maskeleme" → "Kişi adı" (iki yeni
    yol, korumaları, fazla maskeleme sınırları) ve "Sınır" metni; `spec.md` §8; `evals.md` §3
    "Bilinçli istisna" (adlar `pii_best_effort`'ta kalır) ve §2 zorunlu testler.
  - *Kod:* `src/prepare/pii.py` ad keşfine iki yol (yaklaşık 80–120 satır; Adım 17b 15 satır
    tahmin edilip 175 olmuştu, bir-iki inceleme turu beklenir). `labels.json` değişmez.
  - *Değişecek mevcut testler:* `tests/test_pii.py`
    `test_mask_unlabelled_name_everywhere_nothing_masked_documented_limit` (beklenti döner:
    "AHMET YILMAZ\nİmza:" artık maskelenir), `tests/test_dataset.py`
    `test_dataset_contract_05_step_17b_formats_masked_names_not` (maskelenen indeksler `{2, 3, 4}` →
    `{0, 1, 2, 3, 4}`; test adı da değişir), `tests/test_eval_run.py`'de 2 / 5 bekleyen
    `test_run_prepare_only_calls_no_model_and_measures_pii` ve
    `test_run_contract_05_outputs_carry_no_raw_pii`. İkincisi silinmez: beklentisi
    `best_effort_leaked_count == 0` ve boş liste olur; stdout'ta ve sonuç dosyasında hiçbir
    `pii_values` / `pii_best_effort` değeri bulunmadığını sınayan döngü aynen kalır.
  - *Yeni testler:* e-posta + aynı satırda ad (ucar ↔ Uçar, tore ↔ Töre), ekli ad, büyük harfli
    yazım; imza satırı "İmza:", "IMZA", "İmza / Kaşe:", girintili; olumsuzlar: `info@`,
    `a.yilmaz@`, küçük harfli eşleşme, şirket işaretli dizi, yalnızca unvan satırı, tek sözcük,
    ad ile İmza arasında boş satır, "Tedarikçi Kaşesi", birim sözcüklü posta kutuları.
  - *Fark testi:* `git show main:src/prepare/pii.py` ayrı modül olarak yüklenir ve yenisiyle
    karşılaştırılır: (1) 7 eval sözleşmesi, 2 holdout sözleşmesi ve `tests/fixtures/sample_contract.md`
    için hazırlık çıktısı — beklenen tek fark sözleşme 5'in madde 11, madde 12 ve İmzalar parçası;
    (2) test takımındaki her `mask()` çağrısı; (3) ≥ 80.000 rastgele girdi. Değişmez: eski kodda
    maskelenen her aralık aynıdır ya da yeni bir `KISI` aralığıyla örtülüdür; numara, e-posta ve
    IBAN aralıkları bayt bayt aynıdır; `unmask(mask(x)) == x`. (4) Modelsiz ölçüm:
    `--prepare-only` iki veri kümesinde (geçici `--results-dir` ile): varsayılan kümede garanti
    edilen sızıntı 0, garanti edilmeyen 2 / 5 → 0 / 5; holdout 4 / 4 değişmez (holdout model ile
    **yeniden koşulmaz**).
  - *Commit:* `docs: design two more name discovery paths` ·
    `feat: discover names from e-mail addresses and unlabelled signature lines`
- **19c-4 — Koşucuya `--keep-drafts <dosya>` (karar 5).** `evals/run.py` `_run_contract` içinde,
  doğrulanmış bulgular ("sonra") hesaplandıktan sonra, taslağı olan her bulgu için bir JSONL
  satırı: `contract_id`, `rule_id`, `clause_id`, `proposed_text` (maskeli), `code_check`. Kaynak
  audit'teki tool çağrıları değil, doğrulanmış bulgudur (kod denetiminin baktığı metin odur).
  Dosyaya **eklenir** (baştan yazılmaz): `--resume`'da önbellekten gelen sözleşmenin satırları
  önceki koşudan kalır. Yol `--results-dir`'in içindeyse argüman hatası (çıkış kodu 1). Docker'da
  yalnızca `/app/evals` ve `/app/data` kalıcıdır; yol bu yüzden `/app/evals/drafts/` altındadır
  ve `.gitignore`'a `evals/drafts/` eklenir. `evals/` bağlı klasör olduğu için bu değişiklik
  imaj yeniden kurulmadan kapsayıcıda görünür (`src/` değişiklikleri için kurulum yine gerekir).
  Dokümanlar: `docs/evals.md` §6. Testler: seçenek verilmezse hiçbir şey yazılmaz; verilirse
  alanlar yukarıdaki gibidir ve dosya eklemeyle büyür; sonuç klasörü içindeki yol reddedilir;
  sonuç dosyası ve önbellek yine metinsizdir. Karşılaştırmada her taslak için `main`'deki
  `_proposal_check` sonucu da hesaplanır (dosyaya yazılmaz). *Commit:* `feat: keep draft texts of an eval run in a local file`
- **19c-5 — Sonnet tam eval ve karşılaştırma.** Aşağıda "Ölçüm".
- **19c-6 — Dokümanlar ve kapanış.** `evals.md` §4 (yeni koşu ayrı satır / alt başlık; eski
  sayılar yerinde), README (eval bölümü, "Bilinen sınırlar", "Daha fazla vaktim olsaydı"),
  `decisions.md` (şu an tam 900 sözcük: eklenen her sözcük kadar çıkarılır, `wc -w` ≤ 900), `CLAUDE.md` "Şu an", bu adımın "Gerçekleşen" ve
  "Ölçüm" kayıtları; adımın kutusu bu `docs:` commit'inde işaretlenir. Sonra dur: `main`'e alma
  (onaylanırsa `git switch main && git merge --ff-only step-19c`) ve Adım 20 kullanıcı
  onayıyladır.

#### Ölçüm (19c-5)
- **Önkoşul (kullanıcıya sorulur, tek soru):** `.env`'de `LLM_API_KEY` hâlâ Anthropic anahtarı mı?
  `.env` okunmaz. Koşu kullanıcının isteğinde yer alıyor (2026-11-14), ama anahtarın yerinde
  olduğu ve ≈ 620K token'lık koşunun şimdi başlatılacağı koşudan hemen önce teyit edilir.
- **İmaj:** `src/` değiştiği için `docker compose build app`.
- **Komut (Git Bash; sağlayıcı ve model komutta verilir, `.env`'e dokunulmaz):**
  `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e LLM_PROVIDER=anthropic -e LLM_MODEL=claude-sonnet-5-5 -e LLM_TEMPERATURE=none -e LLM_BASE_URL= -e MAX_CONCURRENCY=1 app python -m evals.run --fresh --commit $(git rev-parse --short HEAD) --keep-drafts /app/evals/drafts/19c-sonnet.jsonl`
  (`--commit` değeri 19c-4'ün commit'inden sonra alınır; imaj o commit'ten kurulmuş olmalıdır.)
  Arka planda, çıktısı scratchpad'e yönlendirilerek. Bilinenler: `LLM_TEMPERATURE=none` olmadan
  her çağrı yerelde `ValueError` verir ve bütün kurallar "bulunamadı" olur; önceki iki Sonnet
  koşusu 261–277 sn ve 617K–757K token tuttu; maliyet satırı "bilinmiyor" çıkar. Çıkış kodu 2
  (hız limiti) gelirse aynı komut `--resume` ile.
- **Karşılaştırma:** taban dosya ile yeni dosya kural kural (35 satır): `status`, `clause_no`,
  `needs_review`, `review_reasons`, `code_check`, `search_calls`; sonra metrik tablosu ve
  sözleşme başına token / süre.
- **Beklenen farklar (yalnızca bunlar değişikliklere bağlanabilir):**

  | Satır | Tabanda | Beklenen | Hangi değişiklik |
  |---|---|---|---|
  | Sözleşme 5 `governing_law` | kuyruk dışı, `unverifiable` | kuyrukta, sebep `proposal_unverifiable` | 19c-1 |
  | Sözleşme 2 `late_penalty_cap` | `failed`, kuyrukta | büyük olasılıkla `passed`, kuyruk dışı | 19c-2 |
  | Sözleşme 7 `late_penalty_cap` | `failed`, kuyrukta | büyük olasılıkla `passed`, kuyruk dışı | 19c-2 |
  | Sözleşme 4 `payment_term` | `failed`, kuyrukta (tek sebep `proposal_noncompliant`) | taslak ithal süresini 45 yazdıysa `passed`, kuyruk dışı; 75'i koruduysa ya da andıysa `failed` kalır, kuyrukta | 19c-2 |
  | Garanti edilmeyen PII | 2 / 5 | 0 / 5 | 19c-3 |
  | Yönlendirme | 13 / 35 | sözleşme 4 taslağı kalırsa 12 / 35 (bir yeni, iki eksik), geçerse 11 / 35 (bir yeni, üç eksik); diğer satırlar aynı kalırsa | toplam |
  | Gereksiz yönlendirme | 13 / 13 | aynı koşulla 12 / 12 ya da 11 / 11 | toplam |
  | Öneri uygunluğu | 8 / 11 | 10 / 11 ya da 11 / 11 | 19c-2 |

  Sert eşikler yine tutmalı (güvensiz hata 0, PII sızıntısı 0, alıntı geçerliliği %100).
- **Nasıl okunacak:** taban da yeni koşu da **tek koşudur**; model `temperature` almadan çalışır
  ve koşudan koşuya değişir. Beklenen satırların dışında bir satır değişirse değişikliğe
  bağlanmaz: önce o satırın hangi değişiklikten etkilenebileceğine bakılır (A ve C ajandan sonra
  çalışır, modelin gördüğünü değiştirmez; B yalnızca sözleşme 5'in madde 11, 12 ve İmzalar
  parçasını değiştirir), etkilenemiyorsa "koşu oynaklığı" diye yazılır. Beklenen satırlar da yalnızca şu koşulla
  değişikliğe bağlanır: 19c-2 satırları için saklanan taslak `git show main:src/verify.py`'deki
  `_proposal_check` ile `failed`, yeni kodla `passed` olmalıdır (eski kodla da `passed` ise
  değişiklik model oynaklığıdır: model bu kez yalnızca üst sınırı yazmış olabilir); 19c-1 satırı
  için satırda taslak olmalı ve `code_check == "unverifiable"` olmalıdır (taslak yoksa yönlendirme
  `missing_proposal`'dandır, bu değişiklikten değil). Yönlendirme toplamı satır satır sebeplerden
  yeniden kurulur. Garanti edilmeyen PII modelden bağımsızdır; `--prepare-only` ile koşudan önce
  gösterilir. Kalan her `failed` taslak
  için saklanan metin `extract` ile okunur ve "gerçekten uygunsuz" / "denetim okuyamadı" diye
  sınıflanır; geçen çok değerli taslaklarda düzenlenen değerin (üst sınırın) gerçekten yazıldığı
  elle okunur (karar 2'deki bilinen sınırın gerçekleşip gerçekleşmediği).
- **Yapılmayacaklar:** eşikler değiştirilmez; holdout modelle koşulmaz; Groq koşusu bu adımın
  parçası değildir (günlük limit; istenirse ayrıca sorulur); sonuç beğenilmezse kod sonuca göre
  ayarlanıp yeniden koşulmaz — bulgu yazılır ve kullanıcıya sorulur.

#### Yürütme (`fable-orchestration`)
Ana döngü yargı, brief ve sentez içindir; her `Agent` / `Workflow` çağrısında `model:` açıkça
verilir. Bir delegenin raporu iddiadır: diff, çıktı ya da örnek kontrol edilmeden "bitti" denmez.

| İş | Kime | Brief |
|---|---|---|
| 19c-1, 19c-2, 19c-3 uygulaması (doküman + test + kod; çekirdek modül, çok dosya) | Opus | Gevşek: hedef, yukarıdaki karar ve sınırlar, "bitti" tanımı, fark testi şartı |
| 19c-4 (`--keep-drafts`; tasarımı belli, tek dosya ağırlıklı) | Sonnet | Sıkı: dosyalar, adımlar, alan adları, dokunulmayacaklar |
| Her alt adımın bağımsız incelemesi (fark testi, mutant, doküman–kod uyumu) | Opus, taze ajan | Akran sorusu: kod ve iddialar verilir, sonuç verilmez; kapsam raporu zorunlu |
| Kapanış dokümanlarındaki sayıların sonuç dosyalarından yeniden hesaplanması | Opus | Kendi betiğiyle, yazanınkini kullanmadan |
| Mekanik doküman taramaları (eski ifade kalıntısı, satır sonu, sözcük sayısı) | Sonnet | Tam belirlenmiş komut ve kabul ölçütü |
| Kararlar, bulguların hükme bağlanması, karşılaştırmanın yorumu, kullanıcıya rapor | Ana döngü | — |

Brief'lere her seferinde yazılacak kurallar: `.env` okunmaz; model sağlayıcısına çağrı yok
(19c-5 dışında, onu ana döngü başlatır); `uv lock` / `uv sync` / docker / git durumunu değiştiren
komut yok ("salt okunur" brief'te ayrıca: depo içinde `cd` ile komut koşma değil, yalnızca
okuma); kaçış dizisi içeren kaynak Write / Edit ile yazılır; uzun bulgular şema yerine rapor
dosyasına ya da son mesaja; inceleyici boş bulgu listesiyle değil kapsam raporuyla döner.

- **Doğrulama (adımın tamamı):** üç komut temiz; üç fark testi yukarıdaki koşulları sağlıyor;
  `--prepare-only` garanti edilmeyen PII'yi 0 / 5 gösteriyor; Sonnet koşusu bitti ve karşılaştırma
  tablosu `docs/evals.md`'de; beklenen satırların her biri için "oldu / olmadı / neden" yazılı;
  hiçbir sayı sonuç dosyasından yeniden hesaplanmadan dokümana girmedi.
- **Commit:** alt adımlardaki çiftler; sonuç dosyası için `chore:`; kapanış için `docs:`.

#### Gerçekleşen (2026-11-14)
- **Commit'ler (`step-19c`, `main` = `afb97d5`):** 19c-1 `648b2c1` (docs) + `6ee631d` (feat);
  19c-2 `f85e5ed` (docs) + `a9cacf1` (fix); 19c-3 `04382c0` (feat) + `7f7cbf9` (fix, inceleme
  bulguları); 19c-4 `6e2f230` (feat); 19c-3 ve 19c-4'ün dokümanları `40dd4d5`; sonuç dosyası
  `8f08ab0` (chore); kapanış bu `docs:` commit'i. Testler: 1858 → 1959 (tam takım
  `8f08ab0`'da koşuldu: 1959 geçti).
- **Sapma 1 — hızlandırma (kullanıcının talimatı, 2026-11-14, 19c-1'den sonra):** 19c-2, 19c-3 ve
  19c-4 için alt adım başına değil, tek bir bağımsız inceleme yapıldı. 19c-3 ve 19c-4 paralel
  uygulandı ve dokümanları koddan sonra yazıldı (`40dd4d5`; planlanan `docs: design two more name
  discovery paths` commit'i yok). Alt adımlarda yalnızca dokunulan modülün testleri koşuldu; tam
  takım incelemede bir kez (1932 test, `6e2f230`'da) ve kapanışta bir kez koşuldu.
- **Sapma 2 — 19c-1:** `test_run_broken_hard_threshold_exits_3_with_table_and_file` planın yazdığı
  "`liability_cap`'te yanlış uygun" ile kurulamadı (kural yalnızca acme'de var; etiket dört acme sözleşmesinde de `compliant`). Eşik
  artık sözleşme 2 `governing_law`'da (`check`'siz, etiket `not_found`) yanlış "uygun" ile kırılıyor;
  test iki sözleşmeyle koşuyor (payda 5 → 10). Eski senaryo (`check`'siz kuralda taslaklı yanlış
  "uygun değil") yeni `test_run_wrong_noncompliant_with_unverifiable_draft_is_queued_not_unsafe`
  testinde ters beklentiyle duruyor: satır kuyruğa girer, güvensiz hata eşiği tutar, yanlış bulgu
  eşiği kırılır. Fark testi: 60.000 girdi, 8.355 farklı satır, hepsi hedeflenen fark; bağımsız
  inceleme 60.000 + 20.000 çağrıda ihlal 0, mutantlar yakalandı.
- **Sapma 3 — 19c-2:** fark testi (1) 945 hücrede 8 değişen, hepsi `failed` → `passed`;
  (2) rastgele 60.000 girdide 876 satır `failed` → `passed`, başka fark yok. Bağımsız inceleme
  ters yönde 0 fark buldu; "boş değer listesi" mutantı eşdeğer (yakalanamaz, davranış aynı).
- **Sapma 4 — 19c-3:** planda olmayan daraltıcı korumalar: e-posta yolunda şirket korumasının
  sonraki sözcüklere uzanması ve unvan reddi; imza yolunda birim sözcükleri, "Adına", iki noktasız
  etiket sözcükleri. Bağımsız inceleme üç kritik bulgu verdi, `7f7cbf9` ile düzeltildi: (a) yeni
  bulunan ad bitişik bir e-postanın yerini alıyordu → "yol verme" kuralı (`mask(…, unlabelled=)`,
  `unlabelled_names`); (b) e-posta yolunda karesel süre (ana döngünün ölçümü: 36 KB girdi
  83,7 sn → 0,18 sn; koruma en çok 12 sözcük bakar); (c) imza yolu taraf başlıklarını ad
  sayıyordu → rol sözcükleri ve tanımlı terim koruması. Düzeltme ayrı bir bağımsız incelemeden
  geçmedi; ana döngü inceleyicinin örnekleri ve ilgili testlerle doğruladı. Açık kalan iki nokta
  (bitişik ad + e-posta: etiket yolu ve avukatın düzenlediği taslak) `spec.md` §10'da açık soru.
- **Sapma 5 — 19c-4:** planlandığı gibi uygulandı; bilinen sınırları (çiftlenen satırlar,
  Windows'a özgü yol biçimleri) `evals.md` §6'da.
- **Dokümanlar (bu commit):** `evals.md` §4 "Adım 19c'den sonra" alt başlığı (eski koşuların
  sayıları yerinde), §5 ve §6'da gönderme; README eval bölümü, "Bilinen sınırlar", "Denenmeyenler",
  "Daha fazla vaktim olsaydı"; `decisions.md` (900 sözcük); `CLAUDE.md` "Şu an"; bu bölüm ve "Durum".

#### Ölçüm sonucu (19c-5, 2026-11-14)
- **Koşu:** `evals/results/20261114-032329_anthropic_claude-sonnet-5-5.json`, commit etiketi
  `7f7cbf9`, yukarıdaki "Ölçüm" komutuyla (`LLM_TEMPERATURE=none`, `MAX_CONCURRENCY=1`, `--fresh`,
  `--keep-drafts /app/evals/drafts/19c-sonnet.jsonl`); çıkış 0, `resumed: false`.
- **Sonuç (taban → yeni):** status 35 / 35 → 35 / 35; yönlendirme 13 / 35 → 11 / 35; gereksiz
  yönlendirme 13 / 13 → 11 / 11; öneri uygunluğu 8 / 11 → 11 / 11 (`unverifiable` 1 / 12 iki
  koşuda da); garanti edilmeyen PII 2 / 5 → 0 / 5; alıntı geçerliliği 22 / 22 → 24 / 24 (ham
  32 / 32 iki koşuda da); güvensiz hata 0 / 35; PII sızıntısı 0 / 52; token 617.108 → 630.660
  (girdi 593.334 → 607.134, çıktı 23.774 → 23.526); süre 277,3 → 253,4 sn. Bütün sert eşikler
  tuttu; eşikler değiştirilmedi.
- **Beklenen farklar:** hepsi oldu. Çıkan üç satır (02 ve 07 `late_penalty_cap`, 04
  `payment_term`) ve giren bir satır (05 `governing_law`, tek sebep `proposal_unverifiable`)
  "Nasıl okunacak"taki koşulla değişikliklere bağlandı: üç taslak `git show main:src/verify.py`'deki
  `_proposal_check` ile `failed`, bugünkü kodla `passed` (okunan `[%1, %10]`, `[%1, %10]`,
  `[30, 30, 45, 45 gün]`); 05'te taslak var ve `code_check=unverifiable`. Sözleşme 4 taslağı
  ithal süresini 45 yazıyor. Tabanın taslak metinleri saklanmadığı için tabanda aynı metnin
  yazılıp yazılmadığı bilinmiyor.
- **Beklenmeyen farklar:** 35 satırın 11'i farklı, `status` ve `clause_no` hiçbirinde değişmedi.
  Altı satırda yalnızca `search_calls` ve 06 `confidentiality`'de `agent_escalated`'in düşmesi
  (satır `injection_suspected` ile kuyrukta kaldı): koşu oynaklığı. 05 `governing_law`
  `search_calls` 3 → 2: 19c-3'ün maskeli metni değiştirmesi dışlanamıyor, belirsiz.
- **Taslaklar:** 12 satır, çift yok; 11 `passed`, 1 `unverifiable`, `failed` yok. 12'si de elle
  okundu: çok değerli geçen taslaklarda düzenlenen değer (üst sınır, ithal süresi) yazılmış; karar
  2'deki bilinen sınır bu koşuda gerçekleşmedi. Hiçbir `labels.json` PII değeri sonuç ve taslak
  dosyalarında yok.
- **Ayrıntı:** `docs/evals.md` §4 "Adım 19c'den sonra". Karşılaştırma betiği ve ham çıktı
  `.claude/plans/19c/compare/` (commit edilmez).
- **Yapılmayanlar:** Groq koşusu, holdout koşusu (planın "Yapılmayacaklar"ı).

### [x] Adım 19d — Ad, katı ad dışı değere yol verir
- **Kullanıcının kararı (2026-11-14):** Adım 19c'nin açık bıraktığı bitişik ad + e-posta sorusu
  ("Elif Soylu-elif@ornek.com": ad e-postanın yerini alıyor, e-postanın kalanı ham kalıyordu;
  etiket yolunda `main`'de de vardı, avukatın düzenlediği metinde de) kapatılır.
- **Uygulanan kural (B):** bir KISI aralığı **katı** ad dışı bir değerle (e-posta, bitişik ya da
  standart gruplu IBAN, önekli telefon, bitişik TCKN) örtüşürse ad kaybeder; etiket yolunun ve
  eşlemenin adları dahil. Zayıf (Adım 17b) biçimler ve parantezli telefon eskisi gibi ada yol
  verir. Kullanıcı "ad her zaman kaybeder" (A) demişti; A, etiketli adın `TR` ile bittiği ve
  ardından boşluklu serbest gruplu IBAN geldiği yazımda adı ham bırakıp 17b değişmezini ve testini
  bozduğu için uygulanmadı; B iki hedef durumu da kapatır (kullanıcıya bildirildi).
- **Kod:** `src/prepare/pii.py` `_resolve` beş tur (katı ad dışı → adlar → zayıf → parantezli →
  yalnızca yeni yolların bulduğu adlar); `src/api.py` değişmedi. `unlabelled` listesi artık
  yalnızca ad ↔ ad ve zayıf değer durumları için gerekir.
- **Doğrulama:** eski kodla fark testi (80.000 rastgele girdi): çıktı yalnızca bir adın katı bir
  değerle örtüştüğü 11.144 girdide değişti; ham kalan katı değer 11.144 → 0; 7 eval + 2 holdout
  sözleşmesi ve örnek sözleşmenin hazırlık çıktısı bayt bayt aynı; `--prepare-only` 0 / 5 ve
  holdout 4 / 4 değişmedi. Bağımsız inceleme (iki Opus; 82.000 girdi, 328.559 çağrı): kritik bulgu
  yok, 17b değişmezi tuttu, "kural yalnızca etiketsiz adlar" ve "ad zayıf değere de kaybeder"
  mutantları yakalandı. Model çağrılmadı; eval yeniden koşulmadı (hazırlık çıktısı aynı).
- **Kalan sınırlar** (`docs/agent_design.md` "Sınır (bitişik yazım)", `docs/spec.md` §8 ve §10):
  yol veren adın örtüşmeyen kalanı ham kalır ("Elif ", "Kemal Uç"); e-postaya yapışık zayıf IBAN
  ("…@ornek.com.TR 33 0006 …") ham kalır — eskiden adın örttüğü bu değer, ad hiç bilinmeseydi de
  hamdı (fark testinde 80.000 girdide 4); ada boşluksuz yapışık IBAN ("Elif SoyluTR33…") önceden
  olduğu gibi hiç maskelenmez (açık soru).
- **Commit:** `docs: let names give way to strict non-name values` ·
  `fix: mask an e-mail glued to a known name`

### [ ] Adım 19e — Gerçek bir sözleşmenin gösterdiği beş kod sınırı
- **Kullanıcının isteği (2026-11-14):** dışarıdan verilen tek sözleşmeyle (Sonnet koşusu;
  `docs/evals.md` §4 "Dışarıdan verilen tek sözleşme") görülen beş sınır çözülür. Tasarım
  kararlarını ana döngü verir, kullanıcı beğenmediğini geri aldırır (2026-11-14). Sözleşme depoda
  değildir; her sınır bildirimden yeniden kurulan en küçük girdilerle koşularak üretildi.
  Araştırma kayıtları `.claude/plans/19e/` altındadır (commit edilmez).
- **Araştırmanın bildirimi düzelttiği yerler:** (3) kök neden sıra ya da "Türk lirası" değil —
  tutar tasarım gereği hiç sözcükle okunmaz ve sayı sözcükleri 1–999'dur; (5) asıl engel
  `verify` değil, kuralın `check.unit` şemasıdır (saat birimli kural yüklenmez); (4) hata sonuç
  tablosunda değil, o sözleşmenin model çağrılarından hemen sonra çıkar ve mesajı kullanıcıya
  ulaşmaz; (1) "hiçbiri yanlış uygun üretmiyor" doğru değil: başlık bulunamayınca sözleşme
  paragraflara bölünür, "4.1" ve "4.2" ayrı madde olur ve `verify` alıntı dışındaki kuralı çiğneyen
  değeri komşu paragrafta göremez (koşularak gösterildi) — düzeltme güvenli yöndedir.

#### Kararlar
1. **Başlık (sınır 1):** `src/prepare/clauses.py`'ye ikinci bir başlık kalıbı; **yalnızca metinde
   hiç "Madde N" başlığı yoksa** kullanılır: satır başında Markdown `#` işareti, isteğe bağlı
   `**`, 1–3 basamaklı sayı, ardından rakam gelmeyen `.`, sonra başlık metni ya da satır sonu
   (`## 4. Fesih`, `## **4.** Fesih`). `clause_no` üst düzey sayıdır (yazıldığı gibi; tip `str`
   kalır). Başlık sayılan satırların `#` sayısı ilk numaralı başlığınkiyle aynı olmalıdır (alt
   düzeydeki `### 1.` listesi madde bölmez). Bölmeyenler: alt madde `4.1.`, tarih, tutar, 3
   basamaktan uzun sayı, işaretsiz `4. Başlık`, numaralı liste, Roma rakamı. Reddedilenler: alt
   maddeyi ayrı madde saymak ("Madde 3.2" de başlık değildir; maddenin tamamı tek parçada kalınca
   `verify` çiğneyen alt maddeyi görür); işaretsiz "N. BAŞLIK" (taraf listesi madde oluyor,
   koşularak gösterildi). "Madde N" başlıklı ya da hiçbir başlığı olmayan her metnin bölünmesi
   bayt bayt aynı kalır. Paragraf kipindeki açık (Roma rakamı, işaretsiz başlık) bilinen sınır
   olarak yazılır.
2. **Eval ön kontrolü (sınır 4):** `evals/run.py` `run_eval`, seçilen her etiketin beklenen
   `clause_no`'larının sözleşmenin bölünmüş maddelerinde bulunduğunu **model çağrısından, önbellekten
   ve geçici klasörden önce** denetler (`--prepare-only`'de de); bulunmazsa `EvalError`, çıkış 1,
   mesajda yalnızca sözleşme / kural kimliği ve numaralar. Saf kısım `evals/metrics.py`'dedir.
   `search_hit`'teki `ValueError` kalır (ön kontrolden sonra ulaşılamaz; sessiz "ıskalama" yanlış
   metrik üretirdi).
3. **Modelin birimi (sınır 2):** `_model_unit`'te bugünkü tam eşleşme önce çalışır; `None`
   dönecekken, kural süre türündeyse ve birim tam iki sözcükse, ilki okuyucunun köprü sözcüğü
   (`takvim`) ve ikincisi okuyucunun kapalı süre biçimlerindense o biçimin birimi sayılır (aynı
   tablolar; ikinci bir liste yazılmaz). `iş günü`, tek başına ekli biçimler (`günlük`), `TRY`,
   `yüzde` tanınmamaya devam eder. Reddedilen: okuyucunun tamamını modelin birimine uygulamak
   (mevcut bir testi çevirir, geniş `lira` kökünü miras alır, etki alanı ≈ 5 kat); tool şemasını
   ya da prompt'u değiştirmek (her sağlayıcıya giden isteği değiştirir).
4. **Yazıyla tekrarlanan tutar (sınır 3):** yalnızca **rakamla yazılmış bir TL tutarının parantez
   içinde yazıyla aynen tekrarı** tanınır (`2.400.000 TL (iki milyon dört yüz bin Türk lirası)`,
   `1.000 (bin) TL`, `₺1.000 (bin)`); yazı, kanonik Türkçe tutar dilbilgisiyle (milyar / milyon /
   bin / grup; üst sınır 999.999.999.999) okunur ve rakamla **aynı** değeri söylüyorsa tek değer
   sayılır. Yazı farklı bir tutar söylüyorsa, okunamıyorsa ya da biçim tutmuyorsa bugünkü gibi
   okunamayan kalır ("okunamayan değer atılmaz" ilkesi değişmez). Sözcükle yazılmış tek başına
   tutar okunmaz; sayı sözcükleri genel olarak genişletilmez; süre ve yüzde değişmez. Bilinen
   sınır olarak kalır (önceden var): parantezde birimi yazılmamış çelişen değer görülmez
   (`1.200.000 TL (1.300.000)`).
5. **Saat birimi (sınır 5):** `CheckUnit`'e `saat`; süre ailesinde saat ↔ gün / hafta kesin
   (24 / 168), saat ↔ ay / yıl aralıklı (mevcut gün aralıklarının 24 katı). Saat ifadeleri
   **yalnızca kuralın birimi `saat` iken** değer olarak okunur; öteki bütün kurallarda bugünkü gibi
   okunamayan kalır (her süre kuralında okumak 30.000 rastgele vakanın 309'unda güvensiz yönde
   fark verdi). Saat:dakika yazımı (`08:30 saatinde`) ve `saatine / saatinde / saatinden` biçimleri
   değer okunmaz; "iş saati", "mesai saati" okunamayan kalır. Model `unit: "saat"`'i yalnızca saat
   kuralında yazabilir. Tenant kurallarına saat kuralı eklenmez (eval etiketleri değişmesin);
   dakika ve `sene` kapsam dışıdır.

#### Alt adımlar
Her biri: test + kod, eski kodla fark testi (`verify` ve madde bölme çekirdek modüldür;
güvensiz yönde tek fark kabul edilmez), dokunulan modülün testleri, ruff, mypy; sonra commit.
Dokümanlar en sonda tek commit'te (kullanıcının 19c'deki talimatı). Tek bağımsız inceleme en sonda.
- **19e-1 (karar 1):** `fix: split contracts with numbered Markdown headings into clauses`
- **19e-2 (karar 2):** `fix: check expected clause numbers before any model call in the eval`
- **19e-3 (karar 3):** `fix: accept "takvim" + duration unit in the model's unit field`
- **19e-4 (karar 4):** `fix: read an amount restated in words in parentheses as one value`
- **19e-5 (karar 5):** `feat: support hour as a rule unit`
- **19e-6:** dokümanlar (`agent_design.md` §4–§5, `spec.md`, `evals.md` §2 / §6, `architecture.md`,
  README), bu adımın "Gerçekleşen" kaydı, `CLAUDE.md`.
- **Yapılmayacaklar:** model çağıran eval koşulmaz (kullanıcıya sorulur); sözleşme depoda olmadığı
  için sınırlar gerçek metinle yeniden ölçülmez — bu, "Gerçekleşen"e yazılır.

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
| Çalıştırma: sağlayıcı env ile değişir | 2, 7, 12, 18c |
| Çalıştırma: tenant ayrımı + bunu gösteren test | 3, 11 |
| Karar dokümanı (zorunlu) | 19 |
| Bonus: prompt injection savunması + ölçen testler | 4, 6, 16–18 |
| Bonus: bulguları metin üzerinde gösteren arayüz | 15 |
| AI oturumları (zorunlu) | Plan dışı; kullanıcı kendisi ekler |
| README (kurulum, eval sonuçları, "daha fazla vaktim olsaydı") | 19 |
| GitHub reposu | 20 (push) |

## Uçtan uca doğrulama (plan bitince)
1. `uv run pytest` · `uv run ruff check . && uv run ruff format --check .` · `uv run mypy src evals`
2. `docker compose up` → `POST /reviews` (lawyer) → `POST /reviews/{id}/decision` →
   `GET /reviews/{id}/report` ve `/audit`; aynı okuma uçları `viewer` anahtarıyla maskeli, karar ucu 403.
3. `docker compose run --rm app python -m evals.run` → önce/sonra tablosu.
