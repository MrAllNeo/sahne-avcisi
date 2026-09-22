# Sahne Avcısı

**Sahne Avcısı**, bir ekran görüntüsünün hangi film, dizi, anime veya indekslenmiş videoya ait olduğunu ve sahnenin yaklaşık zaman kodunu bulmayı hedefleyen açık kaynak bir TOYWES projesidir.

Projenin kaynak keşif yaklaşımı **FMHY-first** olarak tasarlanmıştır: FMHY ana katalog olarak izlenir, keşfedilen siteler inceleme kuyruğuna alınır ve yalnızca teknik, hukuki ve güvenlik kontrollerinden geçen kaynaklar sahne indeksleyicisine bağlanır.

> Durum: Erken çalışan MVP. Yerel ve izinli videoları indeksleyip yüklenen ekran görüntülerini dHash + aHash ile arayabilir. İsteğe bağlı trace.moe aramasıyla normal ve 18+ anime sonuçlarını bölüm/zaman koduyla birleştirir. FMHY katalog takibi, güvenli kaynak kuyruğu, HTML5/doğrudan video ve açık HLS VOD adaptörleri çalışır.

## İlk sürümde çalışanlar

- Mobil uyumlu ekran görüntüsü yükleme ve sonuç arayüzü
- Siyah kenarları azaltan görsel normalizasyonu
- dHash + aHash tabanlı sahne parmak izi
- FFmpeg ile belirli aralıklarla video karesi çıkarma
- Başlık, bölüm, kaynak ve zaman kodu saklama
- SQLite üzerinde en yakın kare araması
- FMHY sayfalarından harici kaynak keşfi
- FMHY kaynaklarını bölüm, tür, özellik ve önceliğe göre sınıflandırma
- Yeni, güncellenen, kaybolan ve geri dönen kaynak geçmişi
- Kaynak adaptörü geliştirme kuyruğu
- HTML5 video, Open Graph video, Video.js, JWPlayer, Plyr, HLS ve iframe oynatıcı tespiti
- Yalnızca etkinleştirilmiş kaynaklar için kalıcı indeksleme iş kuyruğu
- Videoyu diske yazmadan doğrudan akıştan indeksleme; boru üzerinden okunamayan kapsayıcılar için boyut sınırlı indirmeye geri düşüş
- Açık, şifresiz ve tamamlanmış HLS VOD manifestlerini güvenli yerel aynaya alma
- HTTPS, alan adı, yönlendirme ve özel IP/SSRF kontrolleri
- Internet Archive kamu malı film arşivinden toplu indeks doldurma
- Archive.org öğelerinde lisans doğrulaması; lisansı belirtilmemiş öğeleri indekslememe
- Açık kullanıcı onayıyla trace.moe canlı anime sahne araması
- Yerel indekste zaten güçlü bir eşleşme (%90 üzeri) varsa trace.moe'ye sorulmaz; kota sadece gerektiğinde harcanır
- `SAHNE_TRACE_MOE=0` ile trace.moe federasyonunu tamamen kapatma
- AniList başlığı, bölüm, zaman kodu, benzerlik ve kısa sahne önizlemesi
- trace.moe sonuçlarında 18+ içeriği sunucu tarafında ayrıca filtreleme
- trace.moe kota (402) ve hız sınırı (429) hataları için ayrı, anlaşılır Türkçe uyarılar
- Servis token'ı ile korunabilen API ve arama ucunda istemci başına hız sınırı
- Yönetici anahtarıyla korunan FMHY eşitleme uç noktası; anahtar karşılaştırması zamanlama saldırılarına karşı `hmac.compare_digest` ile yapılır
- Yetişkin kaynaklarını varsayılan olarak gizleme ve 18+ onayı
- Parmak izleri 64-bit tamsayı olarak saklanır ve bellekte bitişik dizilerde tutulur; sahne araması numpy ile vektörleştirilmiş tek geçişte puanlanır (indeks değiştiğinde otomatik yenilenir)
- Kare yazımı tek işlemde toplu yapılır; bir video yeniden indekslenirse eski kareleri otomatik siler
- Var olmayan bir kaynak kimliğiyle indeksleme denemesi açık bir hata mesajıyla reddedilir
- FFmpeg/FFprobe kurulu değilse net bir hata ile durur
- Takılı kalan indeksleme işleri worker başlangıcında otomatik yeniden kuyruğa alınır
- Docker ile çalıştırma

## Hızlı başlangıç

Gereksinimler:

- Python 3.11+
- FFmpeg / FFprobe
- Pillow

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
sahne-avcisi
```

Arayüz varsayılan olarak `http://127.0.0.1:8080` adresinde açılır.

trace.moe misafir kotasıyla anahtarsız kullanılabilir. Bir API anahtarınız varsa sunucuya isteğe bağlı olarak verilebilir:

```bash
TRACE_MOE_API_KEY="anahtar" sahne-avcisi
```

trace.moe seçeneği arayüzde varsayılan olarak kapalıdır. Kullanıcı açtığında, kategori `all` ya da `anime` ise ve yerel indekste zaten %90 üzeri bir eşleşme yoksa, ekran görüntüsü anime eşleştirmesi için üçüncü taraf trace.moe API'sine (`POST https://api.trace.moe/search?anilistInfo&cutBorders=2`) gönderilir. Görsel Sahne Avcısı tarafından diske yazılmaz; dönen geçici önizleme adresleri de veritabanında saklanmaz.

Federasyonu tamamen kapatmak için:

```bash
SAHNE_TRACE_MOE=0 sahne-avcisi
```

trace.moe kota sınırına (`402`) veya hız sınırına (`429`) takılırsa arama yine de yerel sonuçlarla döner; yalnızca `external_error` alanında ayrı, anlaşılır bir Türkçe uyarı gösterilir.

Docker ile:

```bash
docker build -t sahne-avcisi .
docker run --rm -p 8080:8080 -v sahne-data:/data sahne-avcisi
```

## İzinli bir videoyu indeksleme

İndeksleme aracı yalnızca yerel bir dosya kabul eder. Dosyayı kullanma ve işleme hakkına sahip olmak kullanıcının sorumluluğundadır.

```bash
sahne-index ./ornek-video.mp4 \
  --source-id trace-moe \
  --source-url "https://example.com/video/123" \
  --title "Örnek Anime" \
  --episode "3" \
  --category anime \
  --interval 2
```

Yetişkinlere yönelik, yasal ve izinli bir içeriği indekslerken `--adult` bayrağı ayrıca verilmelidir.

`--source-id` `config/sources.json` içinde tanımlı olmayan bir kaynağı gösteriyorsa indeksleme açık bir hata mesajıyla reddedilir. FFmpeg veya FFprobe kurulu değilse `FileNotFoundError` fırlatılır. Aynı `--source-url` ile tekrar indekslersen (bir video güncellendiğinde) eski kareler otomatik silinir ve yalnızca yeni kareler tek bir işlemde toplu yazılır.

## Servisi internete açarken

Varsayılanda API açıktır; yerel kullanım ve kendi sunucunda barındırma böyle
basit kalıyor. Servisi kendi genel adresiyle yayına alıyorsan — örneğin FWT'nin
arkasında bir Railway servisi olarak — iki ayarı vermelisin:

```bash
SAHNE_INTERNAL_TOKEN="uzun-rastgele-bir-deger"   # tüm /api uçları için zorunlu
SAHNE_RATE_LIMIT=60                              # istemci başına istek (0 = kapalı)
SAHNE_RATE_WINDOW=60                             # saniye cinsinden pencere
```

`SAHNE_INTERNAL_TOKEN` verildiğinde `/api/health` dışındaki bütün uçlar
`X-Sahne-Internal-Token` başlığını ister; karşılaştırma `hmac.compare_digest`
ile yapılır. `/api/health` açık kalır çünkü platformun sağlık yoklaması oradan
geçer.

**Dikkat:** token verildiğinde servisin kendi web arayüzü de çalışmaz, çünkü
tarayıcı bu başlığı gönderemez. Bu bilinçli bir takas: servis, FWT'nin arkasında
yalnızca arka uç olarak çalışır.

Hız sınırı istemci IP'sine göre uygulanır. FWT gibi bir vekilin arkasındaysan
bütün kullanıcılar tek IP'den göründüğü için sınır toplamda geçerli olur; FWT
zaten kendi tarafında kullanıcı başına ayrıca sınırlıyor. Öntanımlı 60/dakika
bunu göz önüne alarak seçildi.

## FMHY kaynak keşfi

FMHY eşitlemesi yönetici anahtarı olmadan çalışmaz:

```bash
SAHNE_ADMIN_TOKEN="uzun-rastgele-bir-deger" sahne-avcisi

curl -X POST http://127.0.0.1:8080/api/sources/sync-fmhy \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Token: uzun-rastgele-bir-deger' \
  -d '{}'
```

Keşfedilen kaynaklar otomatik etkinleştirilmez; `review-required` durumunda tutulur. Bir kaynağı elle `active` yaptıktan sonra yeniden eşitleme bu kararı ezmez — katalog yalnızca keşfeder, etkinleştirme operatörde kalır.

Sunucuyu açmadan komut satırından çalıştırmak için:

```bash
sahne-sync-fmhy
```

Takipçi FMHY'nin güncel `/video` ve `/non-english` kataloglarını tarar. İndirme, torrent, canlı TV, Smart TV/uygulama listeleri ve yardımcı durum/dokümantasyon bağlantıları sahne adaptörü kuyruğunun dışında tutulur. IMDb/Letterboxd gibi izleme-veritabanı bağlantıları kayıt merkezinde `metadata` olarak saklanır; indeksleme kuyruğu bu türü kabul etmez.

FMHY'nin yıldızla işaretlediği kaynaklar `fmhy-starred` etiketiyle daha yüksek önceliğe alınır, böylece inceleme kuyruğu topluluğun önerdiği kaynaklardan başlar.

## İndeksi kamu malı filmlerle doldurma

trace.moe anime tarafını hazır bir indeksle karşılar; film/dizi tarafında indeksi
sen doldurursun. Internet Archive bunun için hazır bir kaynak olarak gelir:
`feature_films` koleksiyonunda lisansı açıkça kamu malı olarak işaretlenmiş
binlerce tam uzunlukta film var ve Archive programatik erişim için belgelenmiş
bir API sunuyor.

```bash
# Varsayılan sorgu: kamu malı lisansı belirtilmiş uzun metraj filmler
sahne-import-archive --limit 25

# Kendi sorgunla
sahne-import-archive --query 'collection:(prelinger) AND mediatype:(movies)' --limit 50
```

Komut yalnızca kuyruğa ekler; indirme ve kare çıkarma işini `sahne-worker` yapar.

Adaptör her öğeyi indekslemez. `archive.org/metadata` yanıtında lisans alanı
kamu malı veya Creative Commons göstermiyorsa öğe `blocked` olarak işaretlenir ve
indirilmez — eksik lisans izin sayılmaz. Öğe video değilse veya boyut sınırını
aşıyorsa yine aynı şekilde atlanır.

## Kaynak URL'sini indeksleme

Bir kaynak ancak teknik/hukuki incelemeden sonra `config/sources.json` içinde `active` yapılabilir. `catalog`, `metadata` ve `api` türleri video işi kabul etmez. Sayfa adresi kaynağın kendi HTTPS alan adına ait olmalıdır.

```bash
curl -X POST http://127.0.0.1:8080/api/index/jobs \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Token: uzun-rastgele-bir-deger' \
  -d '{
    "source_id": "izinli-kaynak",
    "page_url": "https://video.example.com/watch/42",
    "title": "İsteğe bağlı başlık"
  }'
```

Kuyruğu ayrı bir süreçte çalıştır:

```bash
sahne-worker
# Geliştirme veya zamanlanmış görev için yalnızca tek iş:
sahne-worker --once
# Toplu içe aktarma için eşzamanlı çalıştır:
sahne-worker --concurrency 4 --per-host 4
# Çökme sonrası 'running' durumunda takılı kalan işler için eşik (dakika, varsayılan 60):
sahne-worker --stale-minutes 30
```

`--concurrency` kaç işin aynı anda işleneceğini belirler (öntanımlı 1, yani
sıralı). `--per-host` tek bir kaynak alan adına aynı anda açılacak en fazla
aktarımı sınırlar (öntanımlı 2); toplu içe aktarmada işlerin çoğu aynı siteye
gittiği için hızı pratikte bu sayı belirler. Makinedeki çekirdek sayısını aşmak
işe yaramaz: kare çıkarma FFmpeg'de CPU'ya bağlıdır.

Gerçek ölçüm (Archive.org'dan 6 kısa film, 4 çekirdekli makine): sıralı 34,9 sn;
`--concurrency 3 --per-host 2` ile 21,0 sn; `--concurrency 4 --per-host 4` ile
16,2 sn. Üç kurulumda da aynı 786 kare üretildi.

Worker doğrudan MP4/WebM/MOV/M4V adreslerini, HTML sayfasındaki standart video metadatasını ve açık HLS VOD manifestlerini çözebilir. HLS akışı önce doğrulanır; yalnızca tamamlanmış, şifresiz, boyut/süre sınırları içindeki ve manifest alan adıyla aynı güven sınırındaki parçalar geçici bir yerel aynaya indirilir. Canlı, DRM/şifreli, düşük gecikmeli veya farklı alan adına parça taşıyan manifestler reddedilir. FFmpeg bu aynayı yalnızca `file,data` protokolleriyle okur. Üçüncü taraf iframe için hâlâ kaynağa özel ve izinli adaptör gerekir. İndirilen medya kare parmak izleri çıkarılınca geçici dizinle birlikte silinir.

HLS sınırları worker seçenekleriyle ayarlanabilir:

```bash
sahne-worker --max-video-mb 1024 --max-hls-hours 4
```

Worker her başlangıçta `--stale-minutes` süresinden uzun süredir `running` durumunda kalan işleri (ör. worker çökmesi sonrası) otomatik olarak yeniden kuyruğa alır. Beklenmeyen bir hata oluşursa iş sessizce takılı kalmaz; `failed` durumuna alınır ve hata mesajı kaydedilir.

## API

| Yöntem | Yol | Açıklama |
|---|---|---|
| `GET` | `/api/health` | Sağlık kontrolü |
| `GET` | `/api/stats` | İndeks istatistikleri |
| `GET` | `/api/sources?adult=false` | Kaynak kayıtları |
| `POST` | `/api/search` | Base64 görsel ile sahne araması |
| `POST` | `/api/sources/sync-fmhy` | Yönetici korumalı FMHY keşfi |
| `GET` | `/api/catalog/runs` | Yönetici korumalı eşitleme geçmişi |
| `GET` | `/api/catalog/events` | Yönetici korumalı kaynak değişiklikleri |
| `GET` | `/api/adapters/queue` | Yönetici korumalı adaptör geliştirme kuyruğu |
| `POST` | `/api/index/jobs` | Yönetici korumalı URL indeksleme işi oluşturma |
| `GET` | `/api/index/jobs` | Yönetici korumalı indeksleme işleri ve durumları |

## Yol haritası

1. OpenCLIP/SigLIP embedding ve pgvector/Qdrant araması
2. Altyazı, filigran ve oynatıcı arayüzü maskeleme
3. JustWatch/TMDB metadata zenginleştirme
4. Ortak iframe oynatıcı ve kaynağa özel adaptörler
5. Kaynak sağlık kontrolleri ve takılı iş kurtarma
6. Aynı videonun farklı kaynaklardaki kopyalarını birleştirme

Detaylı tasarım için [ARCHITECTURE.md](ARCHITECTURE.md) dosyasına bakın.

## Yasal ve güvenli kullanım

Sahne Avcısı:

- DRM, ödeme duvarı, oturum açma veya erişim kontrolü aşmak için tasarlanmamıştır.
- Telif hakkıyla korunan videoları dağıtmaz veya kendi deposunda barındırmaz.
- Yalnızca herkese açık ve işlenmesine izin verilen kaynakların parmak izlerini tutmayı hedefler.
- Gerçek kişileri yüzlerinden teşhis etmeye çalışmaz; sahne ve kaynak eşleştirir.
- Reşit olmayanlara ilişkin cinsel içerik ile yaşı belirsiz gerçek kişi içeriklerini kesin olarak reddeder.

Kaynak adaptörü eklemeden önce sitenin kullanım koşullarını, robots politikasını, lisansını ve geçerli mevzuatı kontrol edin.

## Lisans

MIT
