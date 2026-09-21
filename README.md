# Sahne Avcısı

**Sahne Avcısı**, bir ekran görüntüsünün hangi film, dizi, anime veya indekslenmiş videoya ait olduğunu ve sahnenin yaklaşık zaman kodunu bulmayı hedefleyen açık kaynak bir TOYWES projesidir.

Projenin kaynak keşif yaklaşımı **FMHY-first** olarak tasarlanmıştır: FMHY ana katalog olarak izlenir, keşfedilen siteler inceleme kuyruğuna alınır ve yalnızca teknik, hukuki ve güvenlik kontrollerinden geçen kaynaklar sahne indeksleyicisine bağlanır.

> Durum: Erken çalışan MVP. Yerel ve izinli videoları indeksleyip yüklenen ekran görüntülerini dHash + aHash ile arayabilir. FMHY katalog değişikliklerini sürüm sürüm takip eder. Güvenli kaynak kuyruğu ve temel HTML5/doğrudan video adaptörleri çalışır; siteye özel geniş ölçekli adaptörler henüz geliştirilmemiştir.

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
- HTTPS, alan adı, yönlendirme ve özel IP/SSRF kontrolleri
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

Keşfedilen kaynaklar otomatik etkinleştirilmez; `review-required` durumunda tutulur.

Sunucuyu açmadan komut satırından çalıştırmak için:

```bash
sahne-sync-fmhy
```

Takipçi FMHY'nin güncel `/video` ve `/non-english` kataloglarını tarar. İndirme, torrent, canlı TV ve yardımcı durum/dokümantasyon bağlantıları sahne adaptörü kuyruğunun dışında tutulur.

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

Worker doğrudan MP4/WebM/MOV/M4V adreslerini ve HTML sayfasındaki standart video metadatasını çözebilir. HLS veya üçüncü taraf iframe tanınır fakat otomatik işlenmez; içerik sahibinin iznine göre kaynağa özel adaptör gerekir. İndirilen video yalnızca geçici dizinde işlenir ve kare parmak izleri çıkarılınca silinir.

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
4. Normal anime için trace.moe adaptörü
5. JustWatch/TMDB metadata zenginleştirme
6. Açık izinli HLS ve ortak iframe oynatıcı adaptörleri
7. Kaynak sağlık kontrolleri ve takılı iş kurtarma
8. Aynı videonun farklı kaynaklardaki kopyalarını birleştirme

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
