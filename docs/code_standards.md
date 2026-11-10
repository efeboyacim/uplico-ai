# Kod Standartları

> Somut, kontrol edilebilir kurallar. Amaç: küçük, okunur, test edilebilir bir kod tabanı.

## Genel
- Tüm fonksiyonlarda type hint zorunlu (`mypy src` temiz).
- Kod ve tanımlayıcılar İngilizce; kullanıcıya dönük metinler ve prompt Türkçe.
- `tech_stack.md`'deki "KULLANMA" listesindeki kütüphaneler eklenmez.

## Hata yönetimi
- Çıplak `except:` yok; yakalanan hata ya ele alınır ya da loglanıp yeniden fırlatılır.
- Bir kural agent'ının hatası `review_rule` düğümünde yakalanır ve `no_finding` bulgusuna çevrilir.
  Tek istisna: sağlayıcı kimlik doğrulama hatası (401) yakalanmaz, yeniden fırlatılır; inceleme
  `failed` olur.
- API hataları FastAPI `HTTPException` ile döner: tanınmayan anahtar **401**, yetersiz rol **403**,
  başka tenant'ın kaynağı **404** (403 değil), yanlış durum **409**, geçersiz gövde **422**,
  sağlayıcı kimlik hatası **502**.
- Doğrulanamayan bulgu ya da taslak silinmez; `needs_review` ve sebebiyle işaretlenir.
- Son karardan önce rapor istenirse **409** döner.

## LLM / agent kodu
- Model ve embedding yalnızca `src/llm.py`'de kurulur; başka yerde `ChatGroq(...)` ya da
  `ChatOllama(...)` yazılmaz. Sağlayıcıya özel parametreler yalnızca o sağlayıcı seçiliyken verilir.
- Model adı ve sağlayıcı env'den okunur. Model adı yalnızca `src/config.py`'deki sağlayıcı
  varsayılanlarında sabit yazılır; başka yerde yazılmaz.
- Env yalnızca `src/config.py`'de okunur ve başlangıçta doğrulanır: `LLM_PROVIDER=groq` iken
  `GROQ_API_KEY` boşsa, sağlayıcı bilinmiyorsa ya da `TENANT_API_KEYS` boşsa uygulama eksik değişkenin
  adını söyleyen hatayla durur. Varsayılan değere ya da başka sağlayıcıya sessizce düşülmez.
- Yeniden deneme yalnızca Groq modelinin `max_retries=3` ayarıyla yapılır (SDK yapar); `while` /
  `sleep` ile elle yeniden deneme, LangGraph `RetryPolicy` ya da yeniden deneme middleware'i eklenmez
  (üst üste binmesin). Denemeler tükenince hata `review_rule`'da `no_finding`'e çevrilir.
- Embedding her zaman yerel Ollama'dır; bulut embedding çağrısı eklenmez.
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
- İndeks gibi serileştirilemeyen nesneler graph state'ine konmaz.

## Avukat aşaması
- Bulgu ve taslak kararlarını ve son kararı alan tek uç `POST /reviews/{id}/decision`'dır; bulgu
  başına ayrı uç eklenmez.
- Karar gövdesi `LawyerDecision` pydantic modeliyle doğrulanır: bulgu için `accept | correct`, taslak
  için `accept | edit | reject`, son karar için `approve | request_changes`. Başka değer 422'dir.
- Zorunluluk kontrolü (kuyruktaki her bulgu ve taslak için karar) graph devam ettirilmeden önce
  API'de yapılır.
- Avukatın yazdığı `edited_text`, saklanmadan ve `Command(resume=...)`'a verilmeden önce
  hazırlıktaki aynı maskeleme fonksiyonundan, incelemenin mevcut eşlemesiyle geçer. İkinci bir
  maskeleme yolu yazılmaz.
- Rapor `src/report.py`'de saf fonksiyondur: saklanan son sonuçtan üretilir, DB'ye yazmaz, LLM çağırmaz.
- Rapor yalnızca JSON'dur.

## Async / eşzamanlılık
- Kod senkrondur: senkron FastAPI endpoint'leri + `SqliteSaver`. `async` ile karıştırılmaz.
- Paralellik yalnızca LangGraph `Send` + `max_concurrency` ile yapılır.
- SQLite bağlantıları `check_same_thread=False` ile açılır (endpoint'ler ve dallar thread'de çalışır).

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
- Yalnızca kimlik bilgileri maskelenir (TCKN, IBAN, telefon, e-posta, kişi adı). Tutar, süre, tarih
  ve yüzdeleri yakalayabilecek genel bir "rakam grubu" kalıbı yazılmaz; telefon kalıbı `+90` ya da
  `0` öneki ister.
- Hazırlık asıl sözleşme metnini değiştirmez; asıl metin saklanır. Modelin kopyasından yalnızca
  görünmez karakterler çıkarılır, başka hiçbir metin silinmez.
- Maske açma tek bir fonksiyondadır ve yalnızca yanıt üretilirken, `lawyer` rolü için çağrılır.
  `viewer` yanıtlarında ve audit yanıtlarında çağrılmaz. Endpoint içinde elle maske açılmaz.
- `GROQ_API_KEY` loglanmaz, hata mesajına ve audit log'a yazılmaz.
- Tüm DB erişimi `Store(tenant_id)` üzerinden yapılır.
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
  değiştirilemez" denir.
- `GET` uçları audit log'a yazmaz.

## Git
- Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
- `data/tenants/*/*.db` commit edilmez (`.gitignore`; PII eşlemesi içerir).
- Çalışma zamanı çıktılarından yalnızca `evals/results/` commit edilir.

## Komutlar
- Format: `uv run ruff format .`
- Lint: `uv run ruff check .`
- Typecheck: `uv run mypy src`
- Test: `uv run pytest`
- Çalıştır: `docker compose up`
- Eval: `docker compose run --rm app python -m evals.run`
