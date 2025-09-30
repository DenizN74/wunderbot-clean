# 🤖 WunderBot - Temiz Versiyon

Sıfırdan yazılmış, basit, sağlam trading signal bot.

## 📁 Dosya Yapısı

```
wunderbot/
├── bot.py              # Ana bot (TEK DOSYA!)
├── pairs.json          # Parite ayarları (GitHub'dan düzenle)
├── requirements.txt    # Python bağımlılıkları
├── render.yaml        # Render config
├── .env.example       # Environment örneği
├── .gitignore
└── README.md
```

## 🚀 Kurulum

### 1. GitHub'a Yükle

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin <your-repo-url>
git push -u origin main
```

### 2. Render'a Deploy

1. render.com → **New Web Service**
2. GitHub repo bağla
3. **Environment Variables** ekle:
   ```
   WUNDERTRADING_WEBHOOK_URL = https://your-webhook-url
   CHECK_INTERVAL = 60
   ```
4. Deploy

### 3. WunderTrading'de Bot Oluştur

1. Yeni bot oluştur
2. Alert text'leri tanımla:
   - Enter Long: `LONG_OPEN_SOLUSDT_15M`
   - Exit Long: `LONG_CLOSE_SOLUSDT_15M`
   - Enter Short: `SHORT_OPEN_SOLUSDT_15M`
   - Exit Short: `SHORT_CLOSE_SOLUSDT_15M`
3. Webhook URL'i kopyala → Render environment variable ekle

### 4. pairs.json Düzenle

GitHub'da `pairs.json` aç ve WunderTrading'deki alert text'leri gir:

```json
{
  "pairs": [
    {
      "symbol": "SOLUSDT",
      "timeframe": "15m",
      "enabled": true,
      "alerts": {
        "enter_long": "LONG_OPEN_SOLUSDT_15M",
        "exit_long": "LONG_CLOSE_SOLUSDT_15M",
        "enter_short": "SHORT_OPEN_SOLUSDT_15M",
        "exit_short": "SHORT_CLOSE_SOLUSDT_15M"
      },
      "strategy": {
        "ema_fast": 12,
        "ema_slow": 26,
        "supertrend_period": 10,
        "supertrend_multiplier": 2.0,
        "wt_channel": 9,
        "wt_average": 12,
        "wt_overbought": 60,
        "wt_oversold": -60,
        "confirmation_mode": "Any 2 of 3",
        "use_sl_tp": true,
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 3.0
      }
    }
  ]
}
```

Commit → Push → Render otomatik deploy eder.

## 📊 Monitoring

**Bot durumu:**
```
https://your-bot.onrender.com/status
```

**Aktif pariteler:**
```
https://your-bot.onrender.com/pairs
```

**Health check:**
```
https://your-bot.onrender.com/health
```

## ➕ Yeni Parite Eklemek

1. GitHub'da `pairs.json` aç
2. Yeni parite bloğu ekle
3. Commit → Push
4. Render otomatik güncelleyecek

Örnek:
```json
{
  "symbol": "BTCUSDT",
  "timeframe": "1h",
  "enabled": true,
  "alerts": {
    "enter_long": "LONG_OPEN_BTCUSDT_1H",
    "exit_long": "LONG_CLOSE_BTCUSDT_1H",
    "enter_short": "SHORT_OPEN_BTCUSDT_1H",
    "exit_short": "SHORT_CLOSE_BTCUSDT_1H"
  },
  "strategy": {
    "confirmation_mode": "Any 2 of 3"
  }
}
```

## 🔧 Strategy Ayarları

- `confirmation_mode`: 
  - `"Any 1 of 3"` - Agresif (1 sinyal yeter)
  - `"Any 2 of 3"` - Dengeli (2 sinyal gerekli) ← Önerilen
  - `"All 3 Required"` - Konservatif (3 sinyal gerekli)

- `use_sl_tp`: true/false - Stop Loss & Take Profit kullan
- `sl_atr_mult`: 1.5 - Stop Loss ATR çarpanı
- `tp_atr_mult`: 3.0 - Take Profit ATR çarpanı

## 📝 Notlar

- Bot her 60 saniyede bir kontrol yapar (CHECK_INTERVAL)
- Binance API key opsiyonel (public data yeterli)
- Webhook URL zorunlu (WunderTrading'den alınır)
- pairs.json'dan sadece `enabled: true` olanlar çalışır
- Tüm pariteler paralel kontrol edilir (thread-safe)

## 🐛 Sorun Giderme

**Bot çalışmıyor:**
- Render logs kontrol et
- Environment variables doğru mu?
- pairs.json formatı doğru mu?

**Alert gönderilmiyor:**
- Webhook URL doğru mu?
- WunderTrading'de bot aktif mi?
- `/status` endpoint'ine bak

**Veri çekilmiyor:**
- Binance API erişilebilir mi?
- Symbol isimleri doğru mu? (BTCUSDT, SOLUSDT)

## 💰 Maliyet

- Render Starter: $7/ay
- TradingView: ~~$60/ay~~ → Artık gerek yok!
- **Tasarruf: %88** 🎉

## 📜 Lisans

MIT