# AurumPulse

Altın (XAU/USD) için gerçek zamanlı fiyat, korelasyon ve otomatik sinyal simülasyon platformu.

## Özellikler

- **Gerçek zamanlı fiyat:** TwelveData REST her 120sn + saniyelik tick simülasyonu (Socket.IO).
- **Teknik indikatörler:** RSI, ATR, MACD, VWAP, Bollinger, DXY korelasyonu.
- **Grafik kalıbı tespiti:** double bottom/top, head & shoulders, triangles, flags (bkz. `backtest_v5.py`).
- **Otomatik sinyal motoru:** kompozit kalite skoru + ardışık kayıp devre kesici + günlük kayıp limiti.
- **Trade simülasyonu:** max 3 eş zamanlı pozisyon, SL/TP/trailing, $100 başlangıç bakiye. Açık pozisyonlar restart'ta DB'den restore edilir.
- **Kripto:** BTC, ETH, SOL, XRP için ayrı simülasyon (Binance spot).
- **Telegram bildirimleri:** opsiyonel, sinyal aç/kapa mesajları.

## Mimari

- **Backend:** Flask + Flask-SocketIO (threading async), Pandas, NumPy
- **Frontend:** vanilla JS + ApexCharts (`index.html`, `crypto.html`)
- **DB:** SQLite (`aurumpulse.db`) — `gold_history`, `trade_history`, `active_positions`
- **Harici API:** TwelveData (altın), Binance (PAXG fallback, kripto), gold-api.com (ikincil fallback)

## Kurulum

### Masaüstü Uygulaması (önerilen)

Tek `.exe` dosyası, çift tıklayıp çalıştır.

```bash
git clone https://github.com/<kullanıcı>/AurumPulse.git
cd AurumPulse
python -m venv venv
venv\Scripts\activate          # Windows; Mac/Linux: source venv/bin/activate
pip install -r requirements.txt

# 1) Test (geliştirme modu — pencere açılır)
python launcher.py

# 2) İlk açılışta %APPDATA%/AurumPulse/.env oluşur,
#    o dosyayı editle, API anahtarlarını gir, tekrar başlat.

# 3) Tek-dosya .exe build
python build_desktop.py
# Çıktı: dist/AurumPulse.exe (~80MB)

# 4) Masaüstüne kısayol oluştur, çift tıkla, hazır.
```

**Veri konumu:**
- Windows: `%APPDATA%/AurumPulse/` (`aurumpulse.db` + `.env`)
- Mac: `~/Library/Application Support/AurumPulse/`
- Linux: `~/.local/share/AurumPulse/`

DB ve ayarlar burada kalıcı — uygulama silinince bile kaybolmaz.

### Sunucu Modu (Railway / VPS)

```bash
pip install -r requirements.txt
cp .env.example .env
# .env'i düzenle, API_KEY zorunlu
python backend.py
```

`http://localhost:5000` üzerinden açılır.

## Environment değişkenleri

`.env` dosyasında (ya da OS env olarak) — tümü `.env.example` içinde örneklendi:

| Değişken | Gerekli | Varsayılan | Açıklama |
|---|---|---|---|
| `API_KEY` | **evet** | random (geçici) | Hassas POST endpoint'lerini korur. Frontend `X-API-Key` header'ı ile gönderir. |
| `FLASK_SECRET_KEY` | hayır | random (restart'ta değişir) | Flask session imzası |
| `CORS_ORIGINS` | hayır | `*` | Virgülle ayrılmış origin listesi. Prod'da kısıtlayın. |
| `TELEGRAM_BOT_TOKEN` | hayır | yok | Boşsa Telegram devre dışı kalır |
| `TELEGRAM_CHAT_ID` | hayır | yok | Bildirim gönderilecek chat ID |
| `LOG_LEVEL` | hayır | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `PORT` | hayır | `5000` | Dinleyeceği port (Railway/Heroku otomatik set eder) |

## Güvenlik notları

- **`API_KEY` zorunlu:** `/api/reset_balance`, `/api/telegram/setup`, `/api/telegram/test_signal`, `/api/crypto/reset` → hepsi `X-API-Key` ister.
- **Token'ı commit etmeyin:** `.env` `.gitignore`'da. Telegram token'ı git history'ye sızarsa BotFather'da `/revoke`.
- **CORS:** production'da `CORS_ORIGINS` domain'inize kısıtlayın.

## API endpoint özeti

| Endpoint | Method | Auth | Açıklama |
|---|---|---|---|
| `/` | GET | — | index.html |
| `/crypto` | GET | — | crypto.html |
| `/health` | GET | — | health check |
| `/api/market_data?interval=1min` | GET | — | OHLC + indikatörler |
| `/api/price_source` | GET | — | Tick-sim durumu |
| `/api/positions` | GET | — | Açık pozisyonlar snapshot |
| `/api/trade_history` | GET | — | İşlem geçmişi + equity curve |
| `/api/daily_pnl` | GET | — | Günlük P/L + hedef |
| `/api/closed_today` | GET | — | Bugün kapanan işlemler |
| `/api/geopolitics` | GET | — | Haber + tehdit skoru |
| `/api/event_predictions` | GET | — | Ekonomik takvim tahminleri |
| `/api/telegram/status` | GET | — | Telegram yapılandırma durumu |
| `/api/telegram/debug` | GET | — | Sinyal + pozisyon debug |
| `/api/telegram/setup` | POST | ✅ | Telegram token/chat_id set |
| `/api/telegram/test_signal` | POST | ✅ | Test sinyali gönder |
| `/api/reset_balance` | POST | ✅ | Simülasyonu sıfırla |
| `/api/crypto/reset` | POST | ✅ | Kripto simülasyonu sıfırla |

`✅` olan endpoint'lere request:

```bash
curl -X POST http://localhost:5000/api/reset_balance \
     -H "X-API-Key: <API_KEY>" \
     -H "Content-Type: application/json"
```

## Dosya yapısı

```
backend.py         # Ana Flask + Socket.IO uygulama
database.py        # SQLite wrapper
index.html         # Altın dashboard
crypto.html        # Kripto dashboard
backtest_v5.py     # Mevcut backtest + pattern tanıma
train_ai.py        # RF model eğitimi (manuel çalıştır)
requirements.txt
.env.example
legacy/            # backtest.py, backtest_v4.py (arşiv)
```

## Deployment

- **Railway:** `railway.json` + `Procfile` mevcut. Env variable'larını Railway dashboard'unda set edin.
- **Graceful shutdown:** SIGTERM/SIGINT → açık pozisyonlar DB'ye flush edilir, background thread'ler `_shutdown_event` ile kapatılır.

## Bilinen sınırlar

- TwelveData free plan: 800 kredi/gün. 120sn fetch aralığı → ~720 fetch/gün.
- Pattern confidence değerleri backtest verisi ile tuning edilmedi (hardcoded).
- Frontend reconnect/retry mantığı minimal.

## Geliştirme

```bash
# Syntax check
python -c "import ast; ast.parse(open('backend.py').read())"

# Standalone backtest
python backtest_v5.py

# AI model eğitimi
python train_ai.py
```
