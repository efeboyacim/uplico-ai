# Kod Standartları

> Somut, kontrol edilebilir kurallar. Amaç: küçük, okunur, test edilebilir bir kod tabanı.

## Genel
- Tüm fonksiyonlarda type hint zorunlu (`mypy src` temiz).
- Kod ve tanımlayıcılar İngilizce; kullanıcıya dönük metinler ve prompt Türkçe.
- `tech_stack.md`'deki "KULLANMA" listesindeki kütüphaneler eklenmez.
- ruff'ta `RUF` kuralları açıktır; Türkçe metinler için yalnızca `ı` harfi serbest bırakılır
  (`allowed-confusables`). Başka benzer karakter serbest bırakılmaz.

## Hata yönetimi
- Çıplak `except:` yok; yakalanan hata ya ele alınır ya da loglanıp yeniden fırlatılır.
- Bir kural agent'ının **model çağrısı** hatası `review_rule` düğümünde `no_finding` bulgusuna
  çevrilir; agent hatadan önce bulgu bildirmişse bulgu korunur ve `run_interrupted` ile avukata gider.
  Hata tipine göre değil, nereden geldiğine göre ayrılır: yalnızca model çağrısını saran middleware'in
  fırlattığı `ModelCallFailed` bu yola girer (`agent_design.md` §6).
  Şunlar bulguya çevrilmez, graph'tan dışarı fırlar ve inceleme `failed` olur: sağlayıcı kimlik ya da
  yapılandırma hatası (Groq 401 / 403 / 404, Ollama 404; API 502), audit yazımından gelen
  `sqlite3.Error` (eksik kayıtla tamamlanmış inceleme olmaz) ve model çağrısının dışından gelen her
  beklenmeyen hata (tool gövdesi, middleware, kütüphane; API 500). Bir kod hatası `llm_error`
  diye avukata gitmez.
- Model çağrısının dışında genel `except Exception` ile hatayı bulguya çeviren kod yazılmaz.
- API hataları FastAPI `HTTPException` ile döner: tanınmayan anahtar **401**, yetersiz rol **403**,
  başka tenant'ın kaynağı **404** (403 değil), yanlış durum **409**, geçersiz gövde **422**,
  sağlayıcı kimlik ya da yapılandırma hatası **502**, graph'tan fırlayan başka her hata **500**
  (`POST /reviews`'te inceleme `failed`; karar ucunda inceleme `awaiting_lawyer`'da kalır, aşağıda
  "Async / eşzamanlılık").
- Kayda bakan gövde kontrollerinin 422'si elle `HTTPException(422)` ile değil,
  `RequestValidationError([{"type", "loc", "msg"}, …])` fırlatılarak üretilir (üç anahtar da
  zorunlu); böylece şema hatasıyla aynı işleyiciden ve aynı biçimle döner, girdi değeri taşımaz.
- API hata yanıtına ve loga hata metni konmaz (sözleşme metni ya da PII taşıyabilir); yanıtta sabit
  bir Türkçe mesaj, logda yalnızca hata sınıfının adı olur.
- Sözdizimi bozuk JSON gövdesi kimlik kontrolünden önce 422 alır (FastAPI gövdeyi bağımlılıklardan
  önce ayrıştırır); bu bilinen tek sıra istisnasıdır (`architecture.md` "Kimlik ve rol").
- Doğrulanamayan bulgu ya da taslak silinmez; `needs_review` ve sebebiyle işaretlenir.
- Son karardan önce rapor istenirse **409** döner.

## LLM / agent kodu
- Model yalnızca `src/llm.py`'de kurulur; başka yerde `ChatGroq(...)` ya da
  `ChatOllama(...)` yazılmaz. Sağlayıcıya özel parametreler yalnızca o sağlayıcı seçiliyken verilir.
- Model adı ve sağlayıcı env'den okunur. Model adı yalnızca `src/config.py`'deki sağlayıcı
  varsayılanlarında sabit yazılır; başka yerde yazılmaz.
- Env yalnızca `src/config.py`'de okunur ve başlangıçta doğrulanır: `LLM_PROVIDER=groq` iken
  `GROQ_API_KEY` boşsa, sağlayıcı bilinmiyorsa ya da `TENANT_API_KEYS` boşsa uygulama eksik değişkenin
  adını söyleyen hatayla durur. Varsayılan değere ya da başka sağlayıcıya sessizce düşülmez.
- Ayarlar `load_settings(env=None)` saf fonksiyonuyla yüklenir; import anında env okunmaz ve süreç
  genelinde ayar önbelleği yoktur. Hata `ConfigError`'dır: tek satır, Türkçe, değişkenin adını
  içerir, hiçbir anahtar değerini içermez. Giriş noktaları `load_settings_or_exit()` çağırır (mesaj
  stderr'e, çıkış kodu 1, traceback yok).
- Ayarları yalnızca giriş noktaları yükler (`python -m src.api`, `python -m evals.run`,
  `python -m src.config`; liste `architecture.md` §4'tedir). Kütüphane
  modülleri (`store`, `llm`, `graph`, `prepare`) `Settings`'i ya da ihtiyaç duydukları değeri
  parametre olarak alır; kendileri env okumaz, ayar yüklemez. Testler `load_settings(env={...})`
  ile kurar, süreç env'ine dokunmaz.
- API modül düzeyinde `app` tanımlamaz; `create_app(settings)` fabrikasıyla kurulur.
- Yeniden deneme yalnızca Groq modelinin `max_retries=3` ayarıyla yapılır (SDK yapar); `while` /
  `sleep` ile elle yeniden deneme, LangGraph `RetryPolicy` ya da yeniden deneme middleware'i eklenmez
  (üst üste binmesin). Denemeler tükenince hata `review_rule`'da `no_finding`'e çevrilir.
- Dokümanda ve kod yorumunda "veri makineden çıkmaz" yalnızca çevrimdışı mod için yazılır.
- Prompt koda gömülmez; `src/prompts/` altında dosyadır.
- Sözleşme metni asla system prompt'a konmaz; yalnızca tool sonucu olarak, sınırlayıcı etiketlerle verilir.
- Tool'lar `tenant_id` / `review_id`'yi argüman olarak almaz; `ToolRuntime.context`'ten okur.
- Agent'a onay, değişiklik talebi ya da veri silme yapan tool **eklenmez.**
- Karar mantığı (`needs_review`, alıntı, sayı, taslak kontrolü) `src/verify.py`'de saf Python'dur;
  LLM çağırmaz. Uygulamada doğrulamayı kapatan bir ayar yoktur.
- Sayıyı ve birimi alıntıdan ve taslaktan kod çıkarır. Modelin verdiği `value` hiçbir karşılaştırmada
  girdi olarak kullanılmaz; yalnızca kodun çıkardığıyla kıyaslanır.
- Kod birden çok aday değer arasında seçim yapmaz; belirsizlik avukat kuyruğuna gider.
- Taslağı ya da bulguyu değerlendirmek için ikinci bir LLM çağrısı eklenmez.
- `needs_review` tek bir boolean'dır, sebepler `review_reasons` listesindedir; öncelik puanı ya da
  sıralama alanı eklenmez.
- LLM çıktısı pydantic modeliyle (`Finding`) doğrulanmadan kullanılmaz.
- Graph'a ham sözleşme metni verilmez; hazırlık (`prepare_contract`) graph'tan önce çalışır.
- `human_review` düğümünde `interrupt()`'tan önce yan etki (DB yazma) yapılmaz; bu düğüm resume'da
  baştan çalışır. `awaiting_lawyer` durumunu graph döndükten sonra API yazar.
- İndeks gibi serileştirilemeyen nesneler graph state'ine konmaz. Pydantic nesneleri de konmaz:
  state düz sözlük tutar (`model_dump(mode="json")`), düğümler girişte doğrular
  (`agent_design.md` §1).

## Avukat aşaması
- Bulgu ve taslak kararlarını ve son kararı alan tek uç `POST /reviews/{id}/decision`'dır; bulgu
  başına ayrı uç eklenmez.
- Karar gövdesi iki modelle taşınır: `src/api.py`'deki istek modeli (`DecisionRequest` +
  `FindingDecisionRequest`) ve maskelemeden sonra kurulan iç model `src/models.py::LawyerDecision`
  (`edited_text: MaskedText`). Değerler: bulgu için `accept | correct`, taslak için
  `accept | edit | reject`, son karar için `approve | request_changes`. Başka değer 422'dir.
  Biçim kuralları (`architecture.md` §3 "Avukat kararı") `src/decision.py`'de tek yerde yazılır;
  iki model de onları çağırır, kopyası yazılmaz. `edited_text` pydantic `max_length=20000` taşır.
- Zorunluluk ve kayda bakan diğer kontroller (kuyruktaki her bulgu ve taslak için karar, `rule_id`'nin
  incelemeye ait olması) graph devam ettirilmeden önce API'de, saf `check_decision` fonksiyonuyla
  yapılır; `reviews` tablosunu okur, checkpoint'i okumaz. **Maskelemeden önce** çalışır.
- Avukatın yazdığı `edited_text`, saklanmadan ve `Command(resume=...)`'a verilmeden önce
  hazırlıktaki aynı maskeleme fonksiyonundan, incelemenin mevcut eşlemesiyle geçer. İkinci bir
  maskeleme yolu yazılmaz. Yeni eşleme girdileri tüm kontroller geçtikten sonra yazılır.
- Son sonuç ve karar olayları saf fonksiyonlarla (`build_outcome`, `decision_events`) kurulur ve
  `finalize` onları tek `Store.complete_review` çağrısıyla yazar.
- Rapor `src/report.py`'de saf fonksiyondur: saklanan son sonuçtan üretilir, DB'ye yazmaz, LLM çağırmaz.
- Rapor yalnızca JSON'dur.

## Async / eşzamanlılık
- Kod senkrondur: senkron FastAPI endpoint'leri + `SqliteSaver`. `async` ile karıştırılmaz.
- Paralellik yalnızca LangGraph `Send` + `max_concurrency` ile yapılır (`max_concurrency` dalları
  gerçekten sınırlar; semafor yazılmaz).
- SQLite bağlantıları `check_same_thread=False` ile açılır (endpoint'ler ve dallar thread'de çalışır).
- `app.db`'ye eşzamanlı yazım: tenant başına tek bağlantı (`check_same_thread=False`, `timeout=30`)
  ve tek `threading.Lock` süreç içinde paylaşılır; `Store(data_dir, tenant_id)` örnekleri aynı
  bağlantıyı ve kilidi kullanır. Paylaşılan bağlantı, kilit ve derlenmiş graph `(data_dir, tenant_id)`
  ile anahtarlanır (testler ve eval ayrı `data_dir` kullanır).
- `Store` her okuma ve yazımı bu kilidin içinde yapar; her yazım kendi transaction'ında hemen commit
  edilir (`BEGIN IMMEDIATE` … `COMMIT`; hata olursa, `COMMIT` hatası dahil, geri alınır ki paylaşılan
  bağlantı açık transaction'da kalmasın).
- Aynı olayın çok sayıda audit satırı (`injection_flagged`) tek tek değil, `append_audit_many` ile
  **tek transaction'da** yazılır: her işaret yine kendi satırını alır, ama ya hepsi yazılır ya hiçbiri.
  Satır başına ayrı commit, işaret sayısıyla doğrusal büyüyen bir süre demektir (4000 işaret ≈ 26 sn);
  sözleşmeyi yazan kişi bu sayıyı belirlediği için bu bir meşgul etme yoludur.
- Durum değişikliği ile onu anlatan audit satırı ayrı transaction'lara bölünmez: `save_findings`
  `awaiting_lawyer` durumunu ve `awaiting_lawyer` satırını birlikte yazar; `complete_review` son
  durumu, son sonucu ve tüm karar / kapanış satırlarını (`finding_decision`, `proposal_decision`,
  `contract_decision`, `report_created`, `review_closed`) birlikte yazar.
- **Karar kilidi:** karar ucunun işleyici gövdesi tenant başına bir `threading.Lock` altında çalışır;
  kilit `Store` ve graph önbelleğiyle aynı `(çözülmüş data_dir, tenant_id)` anahtarıyla tutulur
  (süreç genelinde tek kilit bir tenant'ın isteğinin diğerini bekletmesine yol açardı). Kilit
  altında kayıt yeniden okunur ve durum yeniden denetlenir; kayda bakan kontroller ve ancak onlardan
  sonra maskeleme bu kilidin içindedir. Kilit tek süreç içindir; uygulama tek süreç (tek uvicorn
  worker) çalışır.
- Testler paylaşılan bağlantıları `close_all()` ile, derlenmiş graph'ları ve checkpoint
  bağlantılarını `close_graphs()` ile kapatır (`tests/conftest.py`, her testten sonra, autouse
  fixture); Windows'ta açık bağlantı `tmp_path` dosyalarını kilitler. Uygulama kodu ikisini de
  çağırmaz.
- `interrupt`'tan önce hata fırlatan bir graph thread'i (`POST /reviews`, `failed` inceleme) bir
  daha çağrılmaz: checkpoint'i biten dalların bulgularını ve bekleyen dalı tutar, ama `failed`
  inceleme sondur (`agent_design.md` §1). Resume'da (`finalize`) hata fırlatan thread ise
  `awaiting_lawyer` incelemenindir ve karar ucundan yeniden devam ettirilir; `mark_failed`
  çağrılmaz, `review_failed` yazılmaz (`agent_design.md` §6).
- `app.db`'de WAL açılmaz (`./data` host'tan bağlanır). `checkpoints.db`'yi `SqliteSaver` kendisi
  WAL'a alır; `-wal` / `-shm` dosyaları da `.gitignore`'dadır.

## Test
- Deterministik modüllerin (`prepare`, `verify`, `report`, `store`) ve API'nin (tenant izolasyonu,
  rol kontrolü, role göre gösterim, karar doğrulama) testi vardır: `tests/test_<modül>.py`.
- Yeni bir yazma endpoint'i eklendiğinde `viewer` → 403 testi kendiliğinden onu da kapsar
  (test, uygulamanın rota listesini dolaşır); bu test atlanmaz ya da endpoint hariç tutulmaz.
- Test adı: `test_<ne>_<koşul>_<beklenen>` (ör. `test_verify_fabricated_quote_needs_review`).
- Unit testlerde gerçek LLM çağrısı yok; agent çıktısı elle verilen bulgularla taklit edilir.
  Gerçek model yalnızca eval'de çalışır.

## Güvenlik
- Secret'lar yalnızca env'den okunur; `.env` commit edilmez, `.env.example` edilir.
- Ham PII ve PII eşlemesi loglanmaz, trace'e, audit log'a ve graph state'ine yazılmaz.
- Model, indeks ve checkpoint yalnızca maskeli metin görür. Varsayılan modda model dış sağlayıcıdır
  (Groq); maskelemeyi atlayan ya da sağlayıcıya göre değiştiren bir kod yolu yazılmaz.
- Maskeli metnin tipi `MaskedText`'tir ve onu yalnızca `mask` üretir. Uygulama kodunda başka hiçbir
  yerde `MaskedText(...)` yazılmaz (testler hariç); `Clause` yalnızca `mask` çıktısıyla kurulur.
- İndeks kaydına yalnızca `(tenant_id, review_id)` ile erişilir; `tenant_id` `Store`'dan gelir.
- Yalnızca kimlik bilgileri maskelenir (TCKN, IBAN, telefon, e-posta, kişi adı). Tutar, süre, tarih
  ve yüzdeleri yakalayabilecek genel bir "rakam grubu" kalıbı yazılmaz; telefon kalıbı `+90` ya da
  `0` öneki ister.
- Hazırlık asıl sözleşme metnini değiştirmez; asıl metin saklanır. Modelin kopyasından yalnızca
  görünmez karakterler çıkarılır, başka hiçbir metin silinmez.
- Maske açma tek bir fonksiyondadır ve yalnızca yanıt üretilirken, `lawyer` rolü için çağrılır.
  `viewer` yanıtlarında ve audit yanıtlarında çağrılmaz. Endpoint içinde elle maske açılmaz.
- Yanıtlar sabit alan listesiyle kurulur: yanıt modelleri alanlarını tek tek adlandırır; bir kayıt
  ya da `Finding` dökülüp içinden alan silinerek yanıt üretilmez. `lawyer`'a özel alanlar ayrı ve
  açıkça eklenir. `viewer` için `get_pii_map` çağrılmaz.
- İstek modelleri bilinmeyen alanı yok sayar (`extra="ignore"`); `_Model`'den (`extra="forbid"`)
  türetilmez. Gövdedeki `tenant` / `role` gibi alanlar 422 üretmez, hiçbir şeyi de değiştirmez.
  **Tek istisna:** karar gövdesinin liste öğesi `FindingDecisionRequest` `extra="forbid"`'dir;
  öğede yanlış yazılmış bir alan yok sayılsaydı avukatın kararı sessizce kaybolurdu. Üst düzey
  `DecisionRequest` yine `extra="ignore"`'dur. 422 yalnızca alanın adını (`loc`) taşır, değerini değil.
- `GROQ_API_KEY` loglanmaz, hata mesajına ve audit log'a yazılmaz.
- Tüm DB erişimi `Store(data_dir, tenant_id)` üzerinden yapılır.
- `tenant_id` ve rol yalnızca API anahtarından çözülür (`get_principal` bağımlılığı). Hiçbir endpoint
  gövdeden, sorgudan ya da yoldan `tenant`, `role` ya da karar veren adı okumaz; pydantic istek
  modellerinde bu alanlar yoktur.
- `GET` dışındaki her endpoint `Depends(require_lawyer)` taşır; rol kontrolü kaynak aranmadan ve
  graph çağrılmadan önce yapılır. Endpoint içinde elle `if role == ...` yazılmaz.
- `tenant_id` dosya yoluna girmeden önce bilinen tenant listesine karşı doğrulanır.
- SQL yalnızca parametreli sorgularla yazılır.
- `audit_log` uygulama üzerinden değiştirilemez: tablo `UPDATE` ve `DELETE`'i reddeden SQLite
  tetikleyicileriyle oluşturulur ve kodda bu tabloya yalnızca `INSERT` yazılır. Tetikleyiciyi
  düşüren ya da atlayan kod yazılmaz. Dokümanda "değiştirilemez kayıt" denmez; "uygulama üzerinden
  değiştirilemez" denir. Üçüncü bir tetikleyici var olan bir `id`'ye yapılan `INSERT`'i reddeder
  (`INSERT OR REPLACE` eski satırı `DELETE` tetikleyicisini çalıştırmadan siler).
- `Store`'da sütun ya da tablo adını argümandan alan genel bir güncelleme yöntemi yazılmaz; her
  yazım sabit SQL'li, amaca özel bir yöntemdir.
- Bilinen tenant listesi `data_dir/tenants/` altında `rules.yaml`'ı olan klasör adlarıdır;
  `tenant_id` bu kümede birebir aranır ve eşleşmeden yol kurulmaz.
- `GET` uçları audit log'a yazmaz.

## Git
- Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
- Commit mesajları İngilizce yazılır.
- `data/tenants/*/*.db*` commit edilmez (`.gitignore`; PII eşlemesi içerir).
- Çalışma zamanı çıktılarından yalnızca `evals/results/` commit edilir.

## Komutlar
- Format: `uv run ruff format .`
- Lint: `uv run ruff check .`
- Typecheck: `uv run mypy src`
- Test: `uv run pytest`
- Çalıştır: `docker compose up`
- Eval: `docker compose run --rm app python -m evals.run`
