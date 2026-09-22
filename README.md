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
- Boyut sınırlı geçici video indirme ve bağımsız FFmpeg worker'ı
- Açık, şifresiz ve tamamlanmış HLS VOD manifestlerini güvenli yerel aynaya alma
- HTTPS, alan adı, yönlendirme ve özel IP/SSRF kontrolleri
- Açık kullanıcı onayıyla trace.moe canlı anime sahne araması
- AniList başlığı, bölüm, zaman kodu, benzerlik ve kısa sahne önizlemesi
- trace.moe sonuçlarında 18+ içeriği sunucu tarafında ayrıca filtreleme
- Yönetici anahtarıyla korunan FMHY eşitleme uç noktası
- Yetişkin kaynaklarını varsayılan olarak gizleme ve 18+ onayı
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

trace.moe seçeneği arayüzde varsayılan olarak kapalıdır. Kullanıcı açtığında ekran görüntüsü anime eşleştirmesi için üçüncü taraf trace.moe API'sine gönderilir. Görsel Sahne Avcısı tarafından diske yazılmaz; dönen geçici önizleme adresleri de veritabanında saklanmaz.

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
```

Worker doğrudan MP4/WebM/MOV/M4V adreslerini, HTML sayfasındaki standart video metadatasını ve açık HLS VOD manifestlerini çözebilir. HLS akışı önce doğrulanır; yalnızca tamamlanmış, şifresiz, boyut/süre sınırları içindeki ve manifest alan adıyla aynı güven sınırındaki parçalar geçici bir yerel aynaya indirilir. Canlı, DRM/şifreli, düşük gecikmeli veya farklı alan adına parça taşıyan manifestler reddedilir. FFmpeg bu aynayı yalnızca `file,data` protokolleriyle okur. Üçüncü taraf iframe için hâlâ kaynağa özel ve izinli adaptör gerekir. İndirilen medya kare parmak izleri çıkarılınca geçici dizinle birlikte silinir.

HLS sınırları worker seçenekleriyle ayarlanabilir:

```bash
sahne-worker --max-video-mb 1024 --max-hls-hours 4
```

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

1. Sahne değişimi tabanlı akıllı kare örnekleme
2. OpenCLIP/SigLIP embedding ve pgvector/Qdrant araması
3. Altyazı, filigran ve oynatıcı arayüzü maskeleme
4. JustWatch/TMDB metadata zenginleştirme
5. Ortak iframe oynatıcı ve kaynağa özel adaptörler
6. Kaynak sağlık kontrolleri ve takılı iş kurtarma
7. Aynı videonun farklı kaynaklardaki kopyalarını birleştirme

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
