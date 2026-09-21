# Güvenlik politikası

## Desteklenen sürüm

Proje erken geliştirme aşamasındadır. Güvenlik düzeltmeleri ana dalda yayımlanır.

## Açık bildirme

Bir güvenlik açığı bulursanız herkese açık issue içinde istismar ayrıntısı paylaşmayın. Depo sahibiyle GitHub'ın özel güvenlik bildirim özelliği üzerinden iletişim kurun.

## Kaynak adaptörü güvenlik kuralları

- Yalnızca açıkça izin verilen alan adlarına istek gönderin.
- Yerel ağ, metadata servisleri ve özel IP aralıklarına erişimi engelleyin.
- Yönlendirmelerin hedefini her adımda yeniden doğrulayın.
- İndirilen yanıtların boyut ve süre limitlerini uygulayın.
- İçerik türünü doğrulayın; kullanıcı tarafından verilen uzantıya güvenmeyin.
- İşleme görevlerini düşük yetkili ve izole worker üzerinde çalıştırın.
- DRM, CAPTCHA, üyelik veya ödeme duvarı aşan kod kabul etmeyin.
- Gerçek kişi yüz tanıma/kimlik belirleme özelliği eklemeyin.
- Reşit olmayanlara ilişkin veya yaşı belirsiz gerçek kişi cinsel içeriklerini reddedin ve raporlama prosedürü uygulayın.

## Gizlilik

Sorgu görselleri varsayılan olarak diske yazılmaz. Üretim ortamında hata logları görsel gövdesi, oturum anahtarı veya kaynak erişim bilgisi içermemelidir.

