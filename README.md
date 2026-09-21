# Sahne Avcısı

**Sahne Avcısı**, bir ekran görüntüsünün hangi film, dizi, anime veya indekslenmiş videoya ait olduğunu ve sahnenin yaklaşık zaman kodunu bulmayı hedefleyen açık kaynak bir TOYWES projesidir.

Projenin kaynak keşif yaklaşımı **FMHY-first** olarak tasarlanmıştır: FMHY ana katalog olarak izlenir, keşfedilen siteler inceleme kuyruğuna alınır ve yalnızca teknik, hukuki ve güvenlik kontrollerinden geçen kaynaklar sahne indeksleyicisine bağlanır.

> Durum: Erken çalışan MVP. Yerel ve izinli videoları indeksleyip yüklenen ekran görüntülerini dHash + aHash ile arayabilir. Büyük ölçekli kaynak adaptörleri henüz geliştirilmemiştir.

## İlk sürümde çalışanlar

- Mobil uyumlu ekran görüntüsü yükleme ve sonuç arayüzü
- Siyah kenarları azaltan görsel normalizasyonu
- dHash + aHash tabanlı sahne parmak izi
- FFmpeg ile belirli aralıklarla video karesi çıkarma
- Başlık, bölüm, kaynak ve zaman kodu saklama
- SQLite üzerinde en yakın kare araması
- FMHY sayfalarından harici kaynak keşfi
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

## API

| Yöntem | Yol | Açıklama |
|---|---|---|
| `GET` | `/api/health` | Sağlık kontrolü |
| `GET` | `/api/stats` | İndeks istatistikleri |
| `GET` | `/api/sources?adult=false` | Kaynak kayıtları |
| `POST` | `/api/search` | Base64 görsel ile sahne araması |
| `POST` | `/api/sources/sync-fmhy` | Yönetici korumalı FMHY keşfi |

## Yol haritası

1. Sahne değişimi tabanlı akıllı kare örnekleme
2. OpenCLIP/SigLIP embedding ve pgvector/Qdrant araması
3. Altyazı, filigran ve oynatıcı arayüzü maskeleme
4. Normal anime için trace.moe adaptörü
5. JustWatch/TMDB metadata zenginleştirme
6. İzinli kaynaklar için ortak oynatıcı adaptörleri
7. FMHY değişiklik takibi ve kaynak sağlık kontrolleri
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

