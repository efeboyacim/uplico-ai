# Kod Standartları

> Somut, kontrol edilebilir kurallar. Amaç: küçük, okunur, test edilebilir bir kod tabanı.

## Genel
- Tüm fonksiyonlarda type hint zorunlu (`mypy src evals` temiz; `pyproject.toml`'da mypy kapsamı
  `src` ve `evals`).
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
  yapılandırma hatası (`config` sınıfı: HTTP 401 / 403 / 404 ya da `langchain_core` kimlik / izin /
  bulunamadı hatası; bilinen yanlış sınıflar `agent_design.md` §6; API 502), audit yazımından gelen
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
- Model yalnızca `src/llm.py`'de `init_chat_model` ile kurulur; başka yerde `ChatGroq(...)`,
  `ChatOllama(...)` ya da başka bir sohbet modeli sınıfı yazılmaz. Sağlayıcıya özel parametreler
  yalnızca o sağlayıcı seçiliyken verilir; `groq` ve `ollama` dışındaki her sağlayıcıya (`openai`,
  `anthropic` ve `google_genai` dahil) yalnızca ortak parametreler verilir (`tech_stack.md`
  "Sürüm sabitleri / kısıtlar"). Kodda kapalı sağlayıcı listesi yoktur; sağlayıcıya göre ayrılan kod yalnızca bilinen tablonun sağlayıcıları içindir.
- Model adı, sağlayıcı, anahtar ve adres env'den okunur. Model adı yalnızca `src/config.py`'deki
  sağlayıcı varsayılanlarında sabit yazılır; başka yerde yazılmaz.
- Env yalnızca `src/config.py`'de okunur ve başlangıçta doğrulanır: `LLM_PROVIDER=groq` iken ne
  `GROQ_API_KEY` ne `LLM_API_KEY` varsa, `openai` / `anthropic` / `google_genai` için
  `LLM_API_KEY` boşsa, sağlayıcı adı bozuksa, varsayılan modeli olmayan sağlayıcıda `LLM_MODEL`
  boşsa, `LLM_TEMPERATURE` ya da `LLM_MAX_TOKENS` geçersizse, `groq` ile `LLM_TEMPERATURE=none`
  verilmişse (`ChatGroq` sıcaklıksız istek göndermez), `ollama` ile `LLM_BASE_URL` verilmişse,
  `google_genai` ile `GOOGLE_GENAI_USE_VERTEXAI` / `GOOGLE_GENAI_USE_ENTERPRISE` açıksa ya da
  `GOOGLE_GENAI_CLIENT_MODE` boş değilse ya da
  `TENANT_API_KEYS` boşsa uygulama değişkenin adını söyleyen hatayla durur. Varsayılan değere ya
  da başka sağlayıcıya sessizce düşülmez; boş anahtar `api_key` olarak geçirilmez (entegrasyon
  kendi env değişkenine düşerdi). Aynı sebeple `LLM_BASE_URL` boşken `openai`, `anthropic` ve
  `google_genai`'ye genel adres açıkça verilir (`tech_stack.md` "Ortam değişkenleri"). **Yazılı
  istisna:** `LLM_API_KEY` verilmemişse bilinen tablonun dışındaki sağlayıcının (ve birden çok kimlik bilgisi isteyen her
  sağlayıcının; ör. Bedrock, Azure, Vertex) entegrasyon paketi kendi standart env değişkenlerini
  kendisi okur; Groq'un adres değişkenleri ve entegrasyonların ikincil değişkenleri (organizasyon,
  vekil) de bilinen sınırdır (`tech_stack.md`). Env'i `src/config.py`'den başka bir şeyin okuduğu
  yerler yalnızca bunlardır; uygulama kodu bu değişkenleri okumaz.
- Giriş noktaları (`src/api.py` ve `evals/run.py`'nin `main()`'i) sohbet modelini başlangıçta bir
  kez kurar; kurulum hatası (`ImportError`, `ValueError`, reddedilen kimlik bilgisi) `ConfigError`'a
  çevrilir. `--prepare-only` modeli kurmaz. Kontrol `create_app`'e konmaz.
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
- Yeniden deneme yalnızca modelin `max_retries=3` ayarıyla yapılır (SDK yapar; Ollama'da yoktur);
  `while` / `sleep` ile elle yeniden deneme, LangGraph `RetryPolicy` ya da yeniden deneme
  middleware'i eklenmez (üst üste binmesin). `max_retries`'ın sağlayıcıya göre farklı anlamı kodla
  eşitlenmez. Denemeler tükenince hata `review_rule`'da `no_finding`'e çevrilir.
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
  çağırmaz. Tek istisna eval koşucusudur (`evals/run.py`): kendi geçici veri dizinini silmeden
  önce, hata durumunda da (`finally`), `close_graphs()` ve `close_all()`'u çağırır.
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
- `src/llm.py`'nin kurduğu modeli istek düzeyinde sınayan testler sahte HTTP taşıyıcısını
  `http_client` ile verir; bu yalnızca `groq` ve `openai`'de mümkündür (diğer sağlayıcılarda
  `build_chat_model` `ValueError` fırlatır, çünkü taşıyıcı ya çağrıda hata verir ya da sessizce yok
  sayılıp istek gerçek ağa çıkar). `google_genai`'nin isteği, testte `init_chat_model`
  sarılarak entegrasyonun `client_args={"transport": …}` parametresiyle sınanır; üretim koduna
  yalnızca testin kullandığı bir parametre eklenmez. Paket kurulu olmama durumu, ortamda hangi
  grubun kurulu olduğuna bağlı kalmasın diye import monkeypatch ile düşürülerek sınanır.

## Güvenlik
- Secret'lar yalnızca env'den okunur; `.env` commit edilmez, `.env.example` edilir.
- Ham PII ve PII eşlemesi loglanmaz, trace'e, audit log'a ve graph state'ine yazılmaz.
- Model, indeks ve checkpoint yalnızca maskeli metin görür. Varsayılan modda model dış sağlayıcıdır
  (Groq); env ile seçilen başka bir sağlayıcı da (Ollama dışında) dış sağlayıcı olabilir.
  Maskelemeyi atlayan ya da sağlayıcıya göre değiştiren bir kod yolu yazılmaz.
- Maskeli metnin tipi `MaskedText`'tir ve onu yalnızca `mask` üretir. Uygulama kodunda başka hiçbir
  yerde `MaskedText(...)` yazılmaz (testler hariç); `Clause` yalnızca `mask` çıktısıyla kurulur.
- İndeks kaydına yalnızca `(tenant_id, review_id)` ile erişilir; `tenant_id` `Store`'dan gelir.
- Yalnızca kimlik bilgileri maskelenir (TCKN, IBAN, telefon, e-posta, kişi adı). Tutar, süre, tarih
  ve yüzdeleri yakalayabilecek genel bir "rakam grubu" kalıbı yazılmaz; telefon kalıbı `+90` ya da
  `0` öneki ister.
- Hazırlık asıl sözleşme metnini değiştirmez; asıl metin saklanır. Modelin kopyasından yalnızca
  görünmez karakterler çıkarılır, başka hiçbir metin silinmez.
- Maske açma her `lawyer` yanıt modeli için tek bir fonksiyondadır (inceleme yanıtı ve rapor;
  ikisi de `src/api.py`'de) ve yalnızca yanıt üretilirken, `lawyer` rolü için çağrılır.
  `viewer` yanıtlarında ve audit yanıtlarında çağrılmaz. Endpoint içinde elle maske açılmaz.
- Yanıtlar sabit alan listesiyle kurulur: yanıt modelleri alanlarını tek tek adlandırır; bir kayıt
  ya da `Finding` dökülüp içinden alan silinerek yanıt üretilmez. `lawyer`'a özel alanlar ayrı ve
  açıkça eklenir. `viewer` için `get_pii_map` çağrılmaz.
- İstek modelleri bilinmeyen alanı yok sayar (`extra="ignore"`); `_Model`'den (`extra="forbid"`)
  türetilmez. Gövdedeki `tenant` / `role` gibi alanlar 422 üretmez, hiçbir şeyi de değiştirmez.
  **Tek istisna:** karar gövdesinin liste öğesi `FindingDecisionRequest` `extra="forbid"`'dir;
  öğede yanlış yazılmış bir alan yok sayılsaydı avukatın kararı sessizce kaybolurdu. Üst düzey
  `DecisionRequest` yine `extra="ignore"`'dur. 422 yalnızca alanın adını (`loc`) taşır, değerini değil.
- `GROQ_API_KEY` ve `LLM_API_KEY` loglanmaz, hata mesajına ve audit log'a yazılmaz; `Settings`'in
  `repr`'inde görünmez (`field(repr=False)`).
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
- **Arayüz sayfası (`src/static/index.html`):** sözleşme metni güvenilmeyen girdidir ve avukatın
  anahtarının durduğu sayfada gösterilir.
  - DOM yalnızca `createElement` / `textContent` / `createTextNode` ile kurulur; `innerHTML`,
    `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval` ve `new Function` kullanılmaz.
  - Sayfa tek dosyadır: CSS ve JS içindedir, dış kaynak (CDN, font, resim, harici istek) yoktur.
  - API anahtarı yalnızca JS belleğinde durur; `localStorage`, `sessionStorage`, çerez ve URL'ye
    yazılmaz. İstekler `cache: "no-store"` ile, anahtar `X-API-Key` başlığında gider.
  - Sayfa yalnızca `GET` isteği atar; inceleme başlatma ya da karar formu eklenmez.
  - `GET /` yanıtı CSP başlığı taşır; `script-src` yalnızca sayfadaki tek `<script>` bloğunun
    başlangıçta hesaplanan SHA-256 özetine izin verir (`'unsafe-inline'` script için yazılmaz).
  - Sayfa `StaticFiles` ile bağlanmaz; `GET /` bir `APIRoute`'tur (rota dolaşan testler onu görür).

## Git
- Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
- Commit mesajları İngilizce yazılır.
- `data/tenants/*/*.db*` commit edilmez (`.gitignore`; PII eşlemesi içerir).
- Çalışma zamanı çıktılarından yalnızca `evals/results/` commit edilir.

## Komutlar
- Format: `uv run ruff format .`
- Lint: `uv run ruff check .`
- Typecheck: `uv run mypy src evals`
- Test: `uv run pytest`
- Çalıştır: `docker compose up`
- Eval: `docker compose run --rm app python -m evals.run`
