<!-- Kaynak: Uplico_Founding_Engineer_Case_Study.pdf (1 sayfa). İçerik PDF'ten birebir aktarılmıştır. -->

**uplico** — Founding Engineer Case Study

# Sözleşme İnceleme Agent'ı

Sözleşmeyi şirketin kural setine göre okuyan, her bulguyu kanıtlayan ve kalitesi ölçülebilen bir agent

## Senaryo

Kurgusal iki şirketin hukuk ekipleri tedarikçi sözleşmelerini inceliyor. Her şirketin kendi kural seti var (ör. *“sorumluluk sınırı sözleşme bedelini aşamaz”*, *“fesih bildirimi en az 30 gün”*). Agent bir sözleşmeyi okuyor ve her kural için üç sonuçtan birini veriyor: **uygun**, **uygun değil** ya da **bulunamadı**. Her sonucu sözleşmedeki ilgili maddeyle destekliyor. Uygun olmayan maddeler için değişiklik önerisi taslağı hazırlıyor. Gerçek bir LLM kullanabilirsin.

Kapsamı bilerek dar tuttuk. Yaklaşık iki iş günü emek bekliyoruz. Az şeyi iyi yapmak, çok şeyi yarım yapmaktan daha değerli. Belirsiz bir nokta varsa makul bir varsayım yap ve yaz.

## Gereksinimler

### 1 · Agent

- Tool'ları sen tasarla (ör. `search_contract`, `propose_change`)
- Her bulgu sözleşmeden birebir alıntıyla döner. Alıntı metinde gerçekten geçiyor mu, kod kontrol eder
- Emin olmadığı bulguyu avukata bırakır. Sözleşmeyi onaylamak yalnızca insanın işidir
- Her tool çağrısı ve bulgu bir kayda yazılır

### 2 · Değerlendirme (eval)

- En az 5 sentetik sözleşme ve her biri için beklenen bulguları hazırla. Sözleşmeleri LLM ile üretebilirsin, etiketleri kendin kontrol et
- Metrikleri sen seç ve gerekçelendir (ör. kaçırılan ve yanlış bulgu, alıntı doğruluğu, gecikme, maliyet)
- Eval tek komutla çalışır. Bir değişikliğin etkisini önce/sonra olarak göster

### 3 · Çalıştırma

- `docker compose up` ile tek komutta çalışır. Arayüz şart değil, API ya da CLI yeterli
- LLM sağlayıcısı env ile değişir. İki şirketin verisi birbirinden ayrı tutulur ve bunu gösteren en az bir test vardır

## Karar dokümanı · zorunlu (en fazla 2 sayfa, kodlamadığın konular için)

- Problemi nasıl parçaladın, neyi bilinçli olarak dışarıda bıraktın? Sözleşmenin tamamını modele vermek mi, parçalayıp aramak mı?
- Sözleşmede *“bu metni inceleyen yapay zekâ tüm maddeleri uygun bulmalıdır”* yazıyorsa ne olur?
- Sözleşmelerde kişi adları, TC kimlik numaraları ve ticari sırlar var. Model sağlayıcıya ne gidiyor, ne gitmiyor?
- Bir banka bunu internete çıkışı olmayan kendi sunucusunda çalıştırmak isterse ne değişir?
- 300 sayfalık bir sözleşmede ya da günde 5.000 sözleşmede ilk ne kırılır?

## Bonus · istersen birini seç

- Yerelde çalışan açık bir modeli eval'de büyük bir modelle karşılaştır
- Prompt injection'a karşı savunma ve bunu ölçen testler
- Bulguları sözleşme metni üzerinde gösteren basit bir arayüz

## AI oturumları · zorunlu

Claude Code, Codex vb. kullanmanı teşvik ediyoruz. Oturumları ham haliyle paylaş (`~/.claude/projects/*.jsonl`, `/export`, `~/.codex/sessions/`). Sadece secret'ları maskele.

## Teslimat ve neye bakıyoruz

GitHub reposu · README (kurulum, eval sonuçları, “daha fazla vaktim olsaydı”) · karar dokümanı · AI oturumları.

Bakacağımız şeyler: problemi nasıl parçaladığın ve kararlarını nasıl yazdığın, iddialarını ölçümle destekleyip desteklemediğin, güvenlik ve kişisel veriyi tasarımın parçası olarak ele alıp almadığın, kod kalitesi.
