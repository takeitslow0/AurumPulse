# AurumPulse — Claude Code ortak hafıza notları

Bu dosya Claude Code'un repoyu hızlıca anlaması için. İnsanlar için README.md'ye bakın.

## Proje özeti

XAU/USD gerçek zamanlı fiyat takibi + otomatik ticaret sinyali simülasyonu.

- **Dil/Stack:** Python 3, Flask + Flask-SocketIO (threading mode), SQLite, vanilla JS + ApexCharts.
- **Çalıştırma:** `python backend.py` (env için `.env` oluştur, `.env.example`'dan kopyala).
- **Syntax check:** `python -c "import ast; ast.parse(open('backend.py').read())"`
- **Test suite:** yok (henüz).

## Ana dosyalar

- `backend.py` (~5400 satır): tek dosyada tüm logic — Flask routes, Socket.IO, scanner thread'leri, sinyal motoru, tick simülasyonu, Telegram.
- `database.py`: SQLite wrapper — `gold_history`, `trade_history`, `active_positions` tabloları.
- `index.html`: Altın dashboard.
- `crypto.html`: Kripto dashboard.
- `backtest_v5.py`: Standalone backtest + pattern detection kütüphanesi. Backend'den import edilmiyor.
- `train_ai.py`: Manuel RF model eğitimi, runtime'da kullanılmıyor.
- `legacy/`: Eski backtest versiyonları (arşiv, silinebilir).

## Kritik mimarî noktalar

- **Background thread'ler:** `_gold_realtime_updater` (1sn tick), `background_scanner` (~3dk sinyal), `event_alert_scanner` (60sn), `crypto_scanner` (30sn). Hepsi `_shutdown_event` kontrol ediyor.
- **State:** `_active_positions`, `_trade_history`, `_daily_state`, `ACCOUNT_CONFIG` → `_state_lock` (RLock) koruması altında. `_tick_state` → `_tick_lock`.
- **Persistence:** Açık pozisyonlar her mutasyonda `_persist_positions()` ile DB'ye flush edilir. Startup'ta `_init_active_positions()` restore.
- **Güvenlik:** Hassas POST endpoint'leri `@require_api_key` — `X-API-Key` header'ı gerekli. `API_KEY` env'de.
- **Telegram:** `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` env'de yoksa otomatik devre dışı.

## Rate limit & API

- TwelveData free: 800 kredi/gün. Fetch interval 120sn → ~720/gün.
- Fiyat kaynakları fallback: TwelveData → Binance PAXG (+$12 offset) → gold-api.com.
- `_safe_get` + `_rate_lock` ile dakikada max 7 çağrı.

## Sinyal motoru

- Kompozit kalite skoru (`quality_score`): MTF trend + RSI bölgesi + MACD histogram + VWAP + DXY.
- Pattern-first strategy: pattern yoksa composite fallback, o da yoksa basit fallback.
- Max 3 eş zamanlı pozisyon (`MAX_SIMULTANEOUS`).
- Günlük güvenlik: `DAILY_SAFETY` config — max 20 trade/gün, max %6 kayıp.
- Early exit: 10dk+ pozisyonda ATR-based loss + MACD histogram 3-bar divergence.

## Değişiklik yaparken dikkat

- `_active_positions` veya `_daily_state` değiştiriyorsan `_state_lock` altında yap ve sonunda `_persist_positions()` çağır.
- Lock altında Telegram/DB I/O yapma — snapshot al, lock dışında I/O.
- Yeni endpoint mutasyon içeriyorsa `@require_api_key` ekle.
- Yeni background thread açıyorsan `while not _shutdown_event.is_set()` pattern'i kullan.

## Henüz yapılmamış

- Unit/integration test suite (pytest yok).
- Pattern confidence tuning (hardcoded değerler).
- Frontend reconnect/retry mantığı.
- Smart fallback — 3 fiyat kaynağı arasında otomatik seçim (şu an sıralı).
