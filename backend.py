import requests
import pandas as pd
import numpy as np
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import threading
import time
from datetime import datetime, timezone, timedelta
import feedparser
import traceback
from database import init_db, save_market_data, get_recent_history, save_trade, load_all_trades

import os
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)  # static_folder devre dışı — route'larla çakışmasın
app.config['SECRET_KEY'] = 'aurumpulse-secret'
CORS(app, resources={r"/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading',
                    allow_unsafe_werkzeug=True)


@app.route('/')
def serve_index():
    from flask import send_from_directory
    return send_from_directory(_BASE_DIR, 'index.html')

@app.route('/health')
def health_check():
    return jsonify({"status": "ok"}), 200

@app.route('/api/reset_balance', methods=['POST'])
def reset_balance():
    """Simülasyon bakiyesini sıfırla — $100'e geri dön"""
    global _active_positions, _trade_history
    # Açık pozisyonları kapat
    _active_positions = []
    # Trade geçmişini temizle
    _trade_history = []
    # DB'den de sil
    try:
        import sqlite3
        conn = sqlite3.connect(os.path.join(_BASE_DIR, 'aurumpulse.db'))
        conn.execute('DELETE FROM trade_history')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ DB temizleme hatası: {e}")
    # Bakiyeyi resetle
    ACCOUNT_CONFIG['balance'] = 100.0
    # Günlük istatistikleri sıfırla
    _daily_state['trades_today'] = 0
    _daily_state['pnl_today'] = 0
    _daily_state['consecutive_losses'] = 0
    _daily_state['paused_until'] = 0
    _daily_state['pause_reason'] = ''
    print("🔄 Simülasyon sıfırlandı — Bakiye: $100.00")
    return jsonify({"status": "ok", "balance": 100.0, "message": "Simülasyon sıfırlandı"})

# ─────────────────────────────────────────
# TELEGRAM BOT AYARLARI
# ─────────────────────────────────────────
TELEGRAM_ENABLED = True
TELEGRAM_BOT_TOKEN = "8717946753:AAFVvb25nFheZTUTLxPDbSiTa0MeAFZ1Fuo"
TELEGRAM_CHAT_ID = "6794012842"

_last_telegram_signal = None  # Aynı sinyali tekrar göndermeyi önle

def _send_telegram(text):
    """Telegram mesajı gönder — HTML parse mode + hata kontrolü"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            'chat_id': TELEGRAM_CHAT_ID,
            'text': text,
            'parse_mode': 'HTML'
        }, timeout=10)
        if resp.status_code == 200:
            print(f"📱 Telegram mesaj gönderildi (OK)")
            return True
        else:
            print(f"❌ Telegram API HATA ({resp.status_code}): {resp.text}")
            # HTML da başarısız olursa düz metin dene
            resp2 = requests.post(url, json={
                'chat_id': TELEGRAM_CHAT_ID,
                'text': text.replace('<b>', '').replace('</b>', '').replace('<code>', '').replace('</code>', '').replace('<i>', '').replace('</i>', ''),
            }, timeout=10)
            if resp2.status_code == 200:
                print(f"📱 Telegram düz metin olarak gönderildi (fallback OK)")
                return True
            else:
                print(f"❌ Telegram düz metin de başarısız: {resp2.text}")
                return False
    except Exception as e:
        print(f"⚠️ Telegram bağlantı hatası: {e}")
        return False


def send_telegram_signal(trend, entry, sl, tp1, tp2, confidence, quality_score, quality_reasons, risk_metrics, analysis):
    """Telegram'a sinyal mesajı gönder"""
    if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram devre dışı veya ayarlanmamış")
        return

    global _last_telegram_signal
    sig_key = f"{trend}_{entry}_{sl}"
    if sig_key == _last_telegram_signal:
        print("⚠️ Aynı sinyal, tekrar gönderilmedi")
        return
    _last_telegram_signal = sig_key

    try:
        if trend == "bullish":
            direction = "LONG (AL)"
            emoji = "🟢"
        else:
            direction = "SHORT (SAT)"
            emoji = "🔴"

        lot = risk_metrics.get('lot_size', 0.01)
        risk_usd = risk_metrics.get('risk_usd', 0)
        tp1_profit = risk_metrics.get('tp1_profit', 0)
        tp2_profit = risk_metrics.get('tp2_profit', 0)

        quality_bar = "🟢" * quality_score + "⚫" * (6 - quality_score)
        htf_info = analysis.get('htf', 'N/A')
        now_str = datetime.now().strftime("%H:%M:%S")

        msg = (
            f"{emoji} <b>AURUMPULSE SINYAL</b> {emoji}\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>{emoji} {direction}</b>\n"
            f"Saat: {now_str}\n\n"
            f"Entry: <code>{entry:.2f}</code>\n"
            f"Stop Loss: <code>{sl:.2f}</code>\n"
            f"TP1: <code>{tp1:.2f}</code> (+{tp1_profit:.2f})\n"
            f"TP2: <code>{tp2:.2f}</code> (+{tp2_profit:.2f})\n\n"
            f"Kalite: {quality_bar} ({quality_score}/6)\n"
            f"Guven: {confidence}\n"
            f"Lot: {lot:.2f} | Risk: {risk_usd:.2f}\n\n"
            f"<b>Analiz:</b>\n"
            f"  HTF: {htf_info}\n"
            f"  MACD: {analysis.get('macd', 'N/A')}\n"
            f"  VWAP: {analysis.get('vwap', 'N/A')}\n\n"
            f"{' '.join(quality_reasons)}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )

        print(f"📱 Telegram'a gönderiliyor: {direction} {entry:.2f}")
        _send_telegram(msg)

    except Exception as e:
        print(f"⚠️ Telegram sinyal hazırlama hatası: {e}")
        import traceback
        traceback.print_exc()


def send_telegram_close(result_type, entry, exit_price, pnl):
    """Pozisyon kapandığında Telegram'a bildir"""
    if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:
        if pnl > 0:
            emoji = "✅"
            status = "KAZANC"
        else:
            emoji = "❌"
            status = "KAYIP"

        msg = (
            f"{emoji} <b>POZISYON KAPANDI — {status}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"Giris: <code>{entry:.2f}</code>\n"
            f"Cikis: <code>{exit_price:.2f}</code>\n"
            f"<b>P/L: {pnl:+.2f}</b>\n"
            f"Sebep: {result_type}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )

        _send_telegram(msg)
    except Exception as e:
        print(f"⚠️ Telegram kapanış hatası: {e}")

init_db()

# ─────────────────────────────────────────
# GÜVENLİK (JSON ÇÖKMELERİNİ ÖNLEME ZIRHI)
# ─────────────────────────────────────────
def safe_float(val, default=0.0, decimals=4):
    try:
        if val is None:
            return default
        if isinstance(val, (int, float)) and (np.isnan(val) or np.isinf(val)):
            return default
        if isinstance(val, (pd.Series, np.ndarray)):
            return default
        result = float(val)
        if np.isnan(result) or np.isinf(result):
            return default
        return round(result, decimals)
    except (TypeError, ValueError, OverflowError):
        return default

# ─────────────────────────────────────────
# TWELVE DATA AYARLARI
# ─────────────────────────────────────────
TD_API_KEY  = "8097cbc95b844cf1be80225f069eaefe"
TD_BASE_URL = "https://api.twelvedata.com"

GOLD_SYMBOL    = "XAU/USD"
DXY_CANDIDATES = ["EUR/USD"]

INTERVAL_CONFIG = {
    '1min':  {'outputsize': 120, 'candle_count': 60, 'label': '1 Dakika'},
    '5min':  {'outputsize': 200, 'candle_count': 60, 'label': '5 Dakika'},
    '15min': {'outputsize': 120, 'candle_count': 50, 'label': '15 Dakika'},
    '1h':    {'outputsize': 120, 'candle_count': 48, 'label': '1 Saat'},
}

VALID_INTERVALS = list(INTERVAL_CONFIG.keys())

def get_validated_interval(p):
    if p in VALID_INTERVALS:
        return p
    return '1min'

# ─────────────────────────────────────────
# RATE LIMITER (Dakikada maks. 8 çağrı — TwelveData Free)
# ─────────────────────────────────────────
_call_times = []
_rate_lock  = threading.Lock()

def _safe_get(url, params):
    with _rate_lock:
        now = time.time()
        _call_times[:] = [t for t in _call_times if now - t < 60]
        if len(_call_times) >= 7:
            wait = 61 - (now - _call_times[0])
            if wait > 0:
                print(f"⏳ Rate limit bekleniyor: {wait:.1f}sn")
                time.sleep(wait)
        _call_times.append(time.time())
    return requests.get(url, params=params, timeout=12)

# ─────────────────────────────────────────
# CACHE SİSTEMİ
# ─────────────────────────────────────────
_gold_cache = {}
_dxy_cache  = {'df': pd.DataFrame(), 'sym': '', 'ts': 0}
_htf_cache  = {'df': pd.DataFrame(), 'ts': 0}  # 15dk HTF cache
_cache_lock = threading.Lock()

GOLD_TTL = {'1min': 50, '5min': 280, '15min': 880, '1h': 3580}
DXY_TTL  = 600   # DXY 10dk cache — yavaş değişir
HTF_TTL  = 300   # 15dk cache 5 dakika — yavaş değişir

# ─────────────────────────────────────────
# AKTİF POZİSYON TAKİBİ
# ─────────────────────────────────────────
# Multiple simultaneous positions (max 3)
MAX_SIMULTANEOUS = 3
_active_positions = []  # List of active position dicts

_trade_history = []  # İşlem geçmişi — startup'ta DB'den yüklenir

def _init_trade_history():
    """Backend başlarken DB'deki trade geçmişini belleğe yükler ve bakiyeyi günceller."""
    global _trade_history
    loaded = load_all_trades()
    if loaded:
        _trade_history = loaded
        total_pnl = sum(t.get('pnl', 0) for t in loaded)
        ACCOUNT_CONFIG['balance'] = round(100.0 + total_pnl, 2)
        print(f"💰 Hesap bakiyesi DB'den yüklendi: ${ACCOUNT_CONFIG['balance']}")

# _init_trade_history() → ACCOUNT_CONFIG tanımlandıktan sonra çağrılacak

# ─────────────────────────────────────────
# v3.12: GÜNLÜK GÜVENLİK KONTROLLERI
# ─────────────────────────────────────────
# Profesyonel scalper kuralları (araştırma bulgularına dayalı):
# - Günlük max trade limiti (overtrading koruması)
# - Günlük max kayıp (equity stop — hesap koruma)
# - Ardışık kayıp devre kesici (tilt koruması)
DAILY_SAFETY = {
    'max_trades_per_day': 20,       # v5.7: 5 → 20 (pattern strategy trades more frequently)
    'max_daily_loss_pct': 6.0,      # Günlük max kayıp %6 ($100'da $6)
    'max_consecutive_losses': 999,  # Devre kesici devre dışı — sürekli trade
    'cooldown_after_streak_min': 0,  # Bekleme yok
}

_daily_state = {
    'date': '',                     # Bugünün tarihi (YYYY-MM-DD)
    'trades_today': 0,              # Bugün açılan trade sayısı
    'pnl_today': 0.0,              # Bugünkü toplam P/L
    'consecutive_losses': 0,        # Ardışık kayıp sayacı
    'paused_until': 0,             # Unix timestamp — bu zamana kadar trade açma
    'pause_reason': '',            # Duraklama sebebi
}

def _check_daily_reset():
    """Yeni güne geçildi mi kontrol et, geçildiyse sıfırla"""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if _daily_state['date'] != today:
        _daily_state['date'] = today
        _daily_state['trades_today'] = 0
        _daily_state['pnl_today'] = 0.0
        _daily_state['consecutive_losses'] = 0
        _daily_state['paused_until'] = 0
        _daily_state['pause_reason'] = ''
        print(f"📅 Yeni gün: {today} — günlük sayaçlar sıfırlandı")

def _can_open_trade():
    """Trade açılabilir mi kontrol et — tüm güvenlik katmanları"""
    _check_daily_reset()
    now = int(time.time())

    # 0) Hafta sonu kontrolü — Altın piyasası Cuma 22:00 UTC - Pazar 22:00 UTC arası kapalı
    utc_now = datetime.now(timezone.utc)
    weekday = utc_now.weekday()  # 0=Pazartesi, 4=Cuma, 5=Cumartesi, 6=Pazar
    hour = utc_now.hour
    if weekday == 5 or weekday == 6 or (weekday == 4 and hour >= 22) or (weekday == 0 and hour < 0):
        return False, "📅 PİYASA KAPALI — Hafta sonu (Cuma 22:00 - Pazar 22:00 UTC)"

    # 0b) Bakiye koruması — $0 veya altıysa trade açma
    if ACCOUNT_CONFIG['balance'] <= 0:
        return False, "🚫 BAKİYE SIFIR — Simülasyon durduruldu. Bakiye: $0.00"

    # 1) Duraklama süresi devam ediyor mu?
    if _daily_state['paused_until'] > now:
        remaining = (_daily_state['paused_until'] - now) // 60
        return False, f"⏸️ Duraklama: {_daily_state['pause_reason']} ({remaining}dk kaldı)"

    # 2) Günlük trade limiti
    if _daily_state['trades_today'] >= DAILY_SAFETY['max_trades_per_day']:
        return False, f"🛑 Günlük trade limiti doldu ({_daily_state['trades_today']}/{DAILY_SAFETY['max_trades_per_day']})"

    # 3) Günlük kayıp limiti
    max_loss = ACCOUNT_CONFIG['balance'] * (DAILY_SAFETY['max_daily_loss_pct'] / 100)
    if _daily_state['pnl_today'] <= -max_loss:
        return False, f"🛑 Günlük kayıp limiti aşıldı (${_daily_state['pnl_today']:.2f} / -${max_loss:.2f})"

    return True, "✅"

def _record_trade_result(pnl):
    """Trade sonucunu günlük istatistiklere kaydet"""
    _check_daily_reset()
    _daily_state['trades_today'] += 1
    _daily_state['pnl_today'] += pnl

    if pnl < 0:
        _daily_state['consecutive_losses'] += 1
        # Ardışık kayıp devre kesici
        if _daily_state['consecutive_losses'] >= DAILY_SAFETY['max_consecutive_losses']:
            cooldown_sec = DAILY_SAFETY['cooldown_after_streak_min'] * 60
            _daily_state['paused_until'] = int(time.time()) + cooldown_sec
            _daily_state['pause_reason'] = f"Art arda {_daily_state['consecutive_losses']} kayıp"
            print(f"⚠️ DEVRE KESİCİ: {_daily_state['consecutive_losses']} ardışık kayıp → {DAILY_SAFETY['cooldown_after_streak_min']}dk duraklama")
            if TELEGRAM_ENABLED:
                _send_telegram(f"⚠️ <b>DEVRE KESİCİ AKTİF</b>\n"
                              f"Art arda {_daily_state['consecutive_losses']} kayıp.\n"
                              f"Trading {DAILY_SAFETY['cooldown_after_streak_min']} dakika duraklatıldı.\n"
                              f"Günlük P/L: ${_daily_state['pnl_today']:+.2f}")
    else:
        _daily_state['consecutive_losses'] = 0  # Kazanç = seri sıfırla

    # Günlük kayıp limiti kontrolü
    max_loss = ACCOUNT_CONFIG['balance'] * (DAILY_SAFETY['max_daily_loss_pct'] / 100)
    if _daily_state['pnl_today'] <= -max_loss:
        print(f"🛑 GÜNLÜK KAYIP LİMİTİ: ${_daily_state['pnl_today']:.2f} → Gün sonu kadar trade yok")
        if TELEGRAM_ENABLED:
            _send_telegram(f"🛑 <b>GÜNLÜK KAYIP LİMİTİ</b>\n"
                          f"Bugünkü kayıp: ${_daily_state['pnl_today']:.2f}\n"
                          f"Limit: -${max_loss:.2f} (-%{DAILY_SAFETY['max_daily_loss_pct']})\n"
                          f"Gün sonuna kadar yeni trade açılmayacak.")

def _reset_position():
    return {
        'active': False, 'trend': 'nötr', 'signal': '',
        'entry': 0, 'sl': 0, 'tp1': 0, 'tp2': 0, 'tp1_hit': False,
        'open_time': 0,
        # v5.7 Pattern fields
        'lot': 0.01,
        'remaining_lot': 0.01,
        'partial_done': False,
        'trailing_sl': 0,
        'pattern': '',
        'dynamic_tp_dollars': 0,
    }

def _record_trade(pos, exit_price, result_type, pnl):
    """Kapanan işlemi geçmişe kaydet + DB'ye yaz"""
    trade = {
        'open_time': pos.get('open_time', int(time.time())),
        'close_time': int(time.time()),
        'trend': pos['trend'],
        'entry': pos['entry'],
        'exit_price': exit_price,
        'sl': pos['sl'],
        'tp1': pos['tp1'],
        'tp2': pos['tp2'],
        'result': result_type,
        'pnl': round(pnl, 2),
        'tp1_hit': pos.get('tp1_hit', False),
        'pattern': pos.get('pattern', ''),
        'lot': pos.get('lot', 0.01),
    }
    _trade_history.append(trade)
    # SQLite'a kalıcı kaydet
    try:
        save_trade(trade)
    except Exception as e:
        print(f"⚠️ Trade DB kayıt hatası: {e}")
    # v3.12: Günlük istatistikleri güncelle
    _record_trade_result(pnl)

# ─────────────────────────────────────────
# HESAP & RİSK YÖNETİMİ
# ─────────────────────────────────────────
ACCOUNT_CONFIG = {
    'balance': 100.0,        # Hesap bakiyesi ($)
    'risk_pct': 2.0,         # v3.6 orijinal
    'max_risk_pct': 5.0,     # v3.6 orijinal
    'contract_size': 100,    # 1 lot = 100 ons (XAU/USD standart)
    'min_lot': 0.01,         # Minimum lot büyüklüğü
    'max_lot': 0.10,         # v5.7: 0.05 → 0.10
    'leverage': 100,         # Kaldıraç oranı
}

# DB'den trade geçmişini yükle ve bakiyeyi güncelle
_init_trade_history()

# v5.7 Pattern Strategy Config
PATTERN_CONFIG = {
    'swing_window': 5,
    'pattern_lookback': 40,
    'double_tolerance_pct': 0.25,
    'flag_min_pole_atr': 2.0,
    'flag_max_consolidation': 20,
}

# v5.7 Trade Management
TRADE_MGMT = {
    'tp_dollars': 20.0,
    'sl_dollars': 5.0,
    'trailing_enabled': True,
    'trailing_activate_dollars': 10.0,
    'trailing_step_dollars': 5.0,
    'partial_tp_enabled': True,
    'partial_tp_dollars': 10.0,
    'dynamic_tp_enabled': True,
    'dynamic_tp_min': 10.0,
    'dynamic_tp_max': 40.0,
    'dynamic_tp_multiplier': 1.5,
    'ema_filter_enabled': True,
    'equity_lot_enabled': True,
    'equity_risk_pct': 2.0,
    'equity_high_conf_mult': 2.0,
    'high_conf_threshold': 80,
}

def calculate_position_size(sl_distance, account_balance=None, risk_pct=None):
    """
    SL mesafesine göre lot büyüklüğü hesaplar.

    Formül: Lot = Risk($) / (SL mesafesi × Kontrat büyüklüğü)
    Örnek: $2 risk / ($3 SL × 100 ons) = 0.0067 → 0.01 lot
    """
    balance = account_balance or ACCOUNT_CONFIG['balance']
    risk = risk_pct or ACCOUNT_CONFIG['risk_pct']
    contract = ACCOUNT_CONFIG['contract_size']
    min_lot = ACCOUNT_CONFIG['min_lot']
    max_lot = ACCOUNT_CONFIG['max_lot']

    if sl_distance <= 0:
        return min_lot, balance * (risk / 100), risk

    risk_amount = balance * (risk / 100)  # $100 × 2% = $2
    raw_lot = risk_amount / (sl_distance * contract)

    # Lot'u 0.01 hassasiyetine yuvarla
    lot = round(max(min(raw_lot, max_lot), min_lot), 2)

    # Gerçek risk miktarını hesapla (lot sınırlandığı için değişebilir)
    actual_risk = lot * sl_distance * contract
    actual_risk_pct = (actual_risk / balance) * 100 if balance > 0 else 0

    return lot, round(actual_risk, 2), round(actual_risk_pct, 1)


def calculate_risk_metrics(current_price, sl, tp1, tp2, trend_dir):
    """
    Tam risk metrikleri hesaplar: lot, risk $, risk %, potansiyel kâr, R:R oranı.
    """
    # SL/TP 0 ise sinyal yok demektir — boş metrik döndür
    if sl == 0 or tp1 == 0 or tp2 == 0 or current_price == 0:
        return {
            "lot_size": 0, "risk_usd": 0, "risk_pct": 0,
            "sl_distance": 0, "tp1_profit": 0, "tp2_profit": 0,
            "rr_tp1": 0, "rr_tp2": 0,
            "account_balance": ACCOUNT_CONFIG['balance'], "warning": ""
        }
    sl_distance = abs(current_price - sl)
    lot, risk_usd, risk_pct = calculate_position_size(sl_distance)
    contract = ACCOUNT_CONFIG['contract_size']

    tp1_distance = abs(tp1 - current_price)
    tp2_distance = abs(tp2 - current_price)

    tp1_profit = round(lot * tp1_distance * contract, 2)
    tp2_profit = round(lot * tp2_distance * contract, 2)

    rr_tp1 = round(tp1_distance / sl_distance, 1) if sl_distance > 0 else 0
    rr_tp2 = round(tp2_distance / sl_distance, 1) if sl_distance > 0 else 0

    # Hesap sağlığı uyarısı
    warning = ""
    if risk_pct > ACCOUNT_CONFIG['max_risk_pct']:
        warning = "⚠️ RİSK ÇOK YÜKSEK! SL mesafesi hesabınız için geniş."
    elif risk_pct > 3.0:
        warning = "⚠️ Risk ortalamanın üstünde. Dikkatli olun."
    elif lot == ACCOUNT_CONFIG['min_lot'] and risk_pct < 1.0:
        warning = "✅ Minimum lot ile düşük risk."

    return {
        "lot_size": lot,
        "risk_usd": risk_usd,
        "risk_pct": risk_pct,
        "sl_distance": round(sl_distance, 2),
        "tp1_profit": tp1_profit,
        "tp2_profit": tp2_profit,
        "rr_tp1": rr_tp1,
        "rr_tp2": rr_tp2,
        "account_balance": ACCOUNT_CONFIG['balance'],
        "warning": warning
    }

# ─────────────────────────────────────────
# EKONOMİK TAKVİM VE KILL-SWITCH
# ─────────────────────────────────────────
_calendar_cache = {'events': [], 'ts': 0}

# Ekonomik olayların altın etkisi sözlüğü
# Anahtar = ForexFactory event başlığındaki kelime(ler)
# Değer = (etki_skoru, türkçe_açıklama)
# Pozitif = altın yükselir, negatif = altın düşer
EVENT_GOLD_IMPACT = {
    # ═══ FAİZ & FED ═══
    'Federal Funds Rate':   (-5, 'Faiz kararı — artış altını baskılar, indirim yükseltir'),
    'FOMC Statement':       (3, 'FED açıklaması — güvercin söylem altını destekler'),
    'FOMC Meeting Minutes': (2, 'FED toplantı tutanakları — güvercin ton altını destekler'),
    'FOMC Press Conference':(3, 'Powell basın toplantısı — piyasayı sert hareket ettirir'),
    'Fed Chair Powell':     (3, 'Powell konuşması — sert dalgalanma beklenir'),
    'Interest Rate':        (-3, 'Faiz kararı — artış altını baskılar'),

    # ═══ ENFLASYON ═══
    'CPI':                  (4, 'TÜFE verisi — yüksek enflasyon altını destekler'),
    'Core CPI':             (4, 'Çekirdek TÜFE — FED politikasını doğrudan etkiler'),
    'PPI':                  (3, 'ÜFE verisi — yüksek enflasyon sinyali altını destekler'),
    'Core PPI':             (3, 'Çekirdek ÜFE — enflasyon baskısını gösterir'),
    'PCE Price Index':      (4, 'PCE fiyat endeksi — FED\'in tercih ettiği enflasyon ölçüsü'),
    'Core PCE':             (4, 'Çekirdek PCE — FED kararlarını doğrudan etkiler'),
    'Inflation Rate':       (4, 'Enflasyon oranı — yüksekse altın yükselir'),

    # ═══ İSTİHDAM ═══
    'Non-Farm':             (3, 'Tarım dışı istihdam — zayıfsa altın yükselir, güçlüyse düşer'),
    'Nonfarm Payrolls':     (3, 'NFP — piyasanın en kritik verisi, sert dalgalanma'),
    'Unemployment Rate':    (3, 'İşsizlik oranı — yüksekse FED faiz indirir → altın yükselir'),
    'Average Hourly Earnings': (2, 'Ortalama saatlik kazanç — ücret enflasyonu altını etkiler'),
    'Initial Jobless Claims': (2, 'Haftalık işsizlik başvuruları'),
    'ADP Employment':       (2, 'ADP istihdam — NFP\'nin öncü göstergesi'),
    'JOLTS Job Openings':   (2, 'Açık iş pozisyonları — iş piyasası sağlığı'),

    # ═══ GSYİH & BÜYÜME ═══
    'GDP':                  (-2, 'GSYİH büyümesi — güçlüyse altın düşer'),
    'Advance GDP':          (-2, 'Ön GSYİH tahmini — güçlü büyüme altını baskılar'),
    'Retail Sales':         (-2, 'Perakende satışlar — güçlüyse ekonomi iyi → altın düşer'),
    'ISM Manufacturing':    (-2, 'ISM imalat endeksi — 50 üstü büyüme → altın düşebilir'),
    'ISM Services':         (-2, 'ISM hizmet endeksi — güçlü hizmet sektörü'),
    'Consumer Confidence':  (-1, 'Tüketici güveni — yüksekse risk iştahı artar → altın düşer'),
    'Consumer Sentiment':   (-1, 'Tüketici güven endeksi'),
    'Durable Goods':        (-1, 'Dayanıklı mal siparişleri — üretim gücü'),
    'Industrial Production':(-1, 'Sanayi üretimi'),
    'PMI':                  (-1, 'Satın alma yöneticileri endeksi'),
    'Empire State':         (-1, 'NY bölgesel imalat endeksi'),
    'Philadelphia Fed':     (-1, 'Philadelphia FED endeksi'),

    # ═══ KONUT ═══
    'Existing Home Sales':  (-1, 'Mevcut konut satışları'),
    'New Home Sales':       (-1, 'Yeni konut satışları'),
    'Housing Starts':       (-1, 'Konut başlangıçları'),
    'Building Permits':     (-1, 'İnşaat izinleri'),

    # ═══ DIŞ TİCARET ═══
    'Trade Balance':        (1, 'Dış ticaret dengesi — açık artarsa dolar zayıflar → altın yükselir'),
    'Current Account':      (1, 'Cari denge'),

    # ═══ DİĞER KONUŞMALAR ═══
    'Yellen':               (2, 'Yellen konuşması — maliye politikası sinyali'),
    'Treasury Secretary':   (2, 'Hazine Bakanı açıklaması'),
    'President':            (3, 'Başkan açıklaması — ticaret/jeopolitik etkisi olabilir'),
    'Trump':                (3, 'Trump açıklaması — tarife/ticaret/jeopolitik etkisi'),
    'Biden':                (2, 'Biden açıklaması — politika etkisi'),

    # ═══ TAHVİL İHALELERİ ═══
    'Bond Auction':         (-1, 'Tahvil ihalesi — yüksek getiri altını baskılar'),
    '10-Year':              (-1, '10 yıllık tahvil getirisi — altınla ters korelasyon'),
    '30-Year':              (-1, '30 yıllık tahvil'),
    '2-Year':               (-1, '2 yıllık tahvil — kısa vadeli faiz beklentisi'),
}


def get_event_gold_impact(event_title):
    """Bir ekonomik olay başlığının altın etkisini döndürür."""
    title_lower = event_title.lower()
    best_score = 0
    best_reason = "Altına etkisi dolaylı"

    for key, (score, reason) in EVENT_GOLD_IMPACT.items():
        if key.lower() in title_lower:
            if abs(score) > abs(best_score):
                best_score = score
                best_reason = reason
    return best_score, best_reason


def _parse_ff_events(data):
    """ForexFactory JSON verisini parse et."""
    events = []
    for item in data:
        country = item.get('country', '')
        impact = item.get('impact', '')
        title = item.get('title', '')
        if (country == 'USD' and impact in ('High', 'Medium')) or \
           (impact == 'High' and country in ('USD', 'EUR', 'GBP', 'JPY', 'CNY', 'CHF')):
            try:
                event_time = pd.to_datetime(item.get('date'), utc=True)
            except Exception:
                continue
            gold_score, gold_reason = get_event_gold_impact(title)
            gold_direction = "YÜKSELIR" if gold_score > 0 else ("DÜŞER" if gold_score < 0 else "BELİRSİZ")
            events.append({
                'title': title, 'country': country,
                'time': event_time, 'impact': impact,
                'gold_score': gold_score, 'gold_direction': gold_direction,
                'gold_reason': gold_reason,
                'forecast': item.get('forecast', ''),
                'previous': item.get('previous', ''),
            })
    return events


_ff_last_success = {'events': [], 'ts': 0}  # 429 durumunda eski veriyi tut

def _fetch_from_faireconomy():
    """Kaynak 1: ForexFactory (faireconomy mirror)."""
    global _ff_last_success
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    events = []
    try:
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code == 429:
            # Rate limited — eski başarılı veriyi kullan
            if _ff_last_success['events']:
                age_min = (time.time() - _ff_last_success['ts']) / 60
                print(f"[EconCal] faireconomy 429 — eski cache kullanılıyor ({age_min:.0f}dk)")
                return _ff_last_success['events']
            print("[EconCal] faireconomy 429 — eski cache yok, skip")
            return []
        resp.raise_for_status()
        data = resp.json()
        events = _parse_ff_events(data)
        _ff_last_success = {'events': events, 'ts': time.time()}
        print(f"[EconCal] faireconomy OK — {len(data)} raw, {len(events)} filtered")
    except Exception as e:
        print(f"[EconCal] faireconomy FAIL: {e}")
        if _ff_last_success['events']:
            print(f"[EconCal] Eski cache kullanılıyor")
            return _ff_last_success['events']
    return events


def _fetch_from_finnhub():
    """Kaynak 2: Finnhub free tier — https://finnhub.io/register adresinden ücretsiz key alınabilir."""
    try:
        # Finnhub ücretsiz API key'i buraya yazılabilir
        finnhub_key = os.environ.get('FINNHUB_API_KEY', 'd7cl039r01qv03esog30d7cl039r01qv03esog3g')
        if not finnhub_key:
            print("[EconCal] Finnhub SKIP — API key yok (FINNHUB_API_KEY env var ayarla)")
            return []
        url = "https://finnhub.io/api/v1/calendar/economic"
        params = {'from': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                  'to': (datetime.now(timezone.utc) + timedelta(days=7)).strftime('%Y-%m-%d')}
        headers = {'X-Finnhub-Token': finnhub_key}
        resp = requests.get(url, headers=headers, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        ec = data.get('economicCalendar', [])
        events = []
        for item in ec:
            impact_val = item.get('impact', 0)
            country = item.get('country', '')
            title = item.get('event', '')
            if impact_val >= 2 and country in ('US', 'EU', 'GB', 'JP', 'CN', 'CH'):
                try:
                    event_time = pd.to_datetime(item.get('time', item.get('date', '')), utc=True)
                except Exception:
                    continue
                mapped_country = {'US': 'USD', 'EU': 'EUR', 'GB': 'GBP', 'JP': 'JPY', 'CN': 'CNY', 'CH': 'CHF'}.get(country, country)
                impact = 'High' if impact_val >= 3 else 'Medium'
                gold_score, gold_reason = get_event_gold_impact(title)
                gold_direction = "YÜKSELIR" if gold_score > 0 else ("DÜŞER" if gold_score < 0 else "BELİRSİZ")
                events.append({
                    'title': title, 'country': mapped_country,
                    'time': event_time, 'impact': impact,
                    'gold_score': gold_score, 'gold_direction': gold_direction,
                    'gold_reason': gold_reason,
                    'forecast': str(item.get('estimate', '')),
                    'previous': str(item.get('prev', '')),
                })
        print(f"[EconCal] Finnhub OK — {len(ec)} raw, {len(events)} filtered")
        return events
    except Exception as e:
        print(f"[EconCal] Finnhub FAIL: {e}")
        return []


def _get_recurring_critical_events():
    """
    Kaynak 3 (Fallback): Bilinen tekrarlayan kritik olaylar.
    Her hafta/ay düzenli olarak gerçekleşen büyük olaylar.
    API'ler çalışmadığında en azından bunlar gösterilir.
    """
    now = datetime.now(timezone.utc)
    today = now.date()
    events = []

    # ── Aylık tekrarlayan büyük olaylar ──
    MONTHLY_EVENTS = [
        # (gün aralığı, başlık, etki puanı, açıklama)
        ((1, 7), "Non-Farm Payrolls (NFP)", 8, "İstihdam verisi USD ve altını güçlü etkiler"),
        ((10, 14), "CPI — Consumer Price Index", 7, "Enflasyon verisi FED faiz beklentilerini şekillendirir"),
        ((14, 18), "Retail Sales", 5, "Tüketici harcamaları ekonomik gücü gösterir"),
        ((25, 30), "PCE Price Index", 6, "FED'in tercih ettiği enflasyon ölçüsü"),
        ((1, 5), "ISM Manufacturing PMI", 4, "İmalat sektörü aktivitesi"),
        ((3, 7), "ISM Services PMI", 4, "Hizmet sektörü aktivitesi"),
        ((15, 20), "Industrial Production", 3, "Sanayi üretim verisi"),
    ]

    # Bu ay hangi olaylar yaklaşıyor?
    for (day_start, day_end), title, score, reason in MONTHLY_EVENTS:
        if day_start <= today.day <= day_end + 3:  # Bugüne yakın olan
            est_date = today.replace(day=min(day_start + 2, 28))
            try:
                event_time = datetime.combine(est_date, datetime.min.time()).replace(
                    hour=12, minute=30, tzinfo=timezone.utc)
            except Exception:
                continue
            gold_score, gold_reason = get_event_gold_impact(title)
            if gold_score == 0:
                gold_score = score
                gold_reason = reason
            gold_direction = "YÜKSELIR" if gold_score > 0 else ("DÜŞER" if gold_score < 0 else "BELİRSİZ")
            events.append({
                'title': f"📅 {title} (tahmini)",
                'country': 'USD',
                'time': event_time,
                'impact': 'High' if abs(score) >= 5 else 'Medium',
                'gold_score': gold_score,
                'gold_direction': gold_direction,
                'gold_reason': gold_reason,
                'forecast': '', 'previous': '',
            })

    # ── FOMC toplantıları (2026 takvimi) ──
    FOMC_DATES_2026 = [
        (1, 28, 29), (3, 18, 19), (5, 6, 7), (6, 17, 18),
        (7, 29, 30), (9, 16, 17), (10, 28, 29), (12, 16, 17),
    ]
    for month, day1, day2 in FOMC_DATES_2026:
        try:
            fomc_date = datetime(2026, month, day2, 18, 0, tzinfo=timezone.utc)
            diff = (fomc_date.date() - today).days
            if -1 <= diff <= 14:
                gold_score, gold_reason = get_event_gold_impact("FOMC Interest Rate Decision")
                if gold_score == 0:
                    gold_score = 8
                    gold_reason = "FED faiz kararı altın için en kritik olay"
                gold_direction = "YÜKSELIR" if gold_score > 0 else ("DÜŞER" if gold_score < 0 else "BELİRSİZ")
                events.append({
                    'title': "🏦 FOMC Faiz Kararı",
                    'country': 'USD',
                    'time': fomc_date,
                    'impact': 'High',
                    'gold_score': gold_score,
                    'gold_direction': gold_direction,
                    'gold_reason': gold_reason,
                    'forecast': '', 'previous': '',
                })
        except Exception:
            continue

    # ── ECB toplantıları (2026 takvimi) ──
    ECB_DATES_2026 = [
        (1, 30), (3, 12), (4, 17), (6, 5), (7, 17), (9, 11), (10, 29), (12, 11),
    ]
    for month, day in ECB_DATES_2026:
        try:
            ecb_date = datetime(2026, month, day, 12, 45, tzinfo=timezone.utc)
            diff = (ecb_date.date() - today).days
            if -1 <= diff <= 14:
                events.append({
                    'title': "🏦 ECB Faiz Kararı",
                    'country': 'EUR',
                    'time': ecb_date,
                    'impact': 'High',
                    'gold_score': 5,
                    'gold_direction': 'BELİRSİZ',
                    'gold_reason': 'ECB faiz kararı EUR/USD üzerinden altını etkiler',
                    'forecast': '', 'previous': '',
                })
        except Exception:
            continue

    if events:
        print(f"[EconCal] Recurring fallback — {len(events)} events generated")
    return events


def fetch_economic_calendar():
    """Çoklu kaynaklı ekonomik takvim. Fallback zinciri ile her zaman veri sağlar."""
    global _calendar_cache
    if time.time() - _calendar_cache['ts'] < 3600:  # 1 saat cache — 429 rate limit önleme
        return _calendar_cache['events']

    events = []

    # Kaynak 1: ForexFactory
    try:
        events = _fetch_from_faireconomy()
    except Exception as e:
        print(f"[EconCal] faireconomy exception: {e}")

    # Kaynak 2: Finnhub (eğer FF boş geldiyse)
    if not events:
        try:
            events = _fetch_from_finnhub()
        except Exception as e:
            print(f"[EconCal] finnhub exception: {e}")

    # Kaynak 3: Tekrarlayan kritik olaylar (her zaman ekle, boşsa tek kaynak olur)
    recurring = _get_recurring_critical_events()

    # Eğer API'lerden veri geldiyse, recurring'leri sadece overlap yoksa ekle
    if events:
        existing_titles = {e['title'].lower() for e in events}
        for r in recurring:
            clean_title = r['title'].replace('📅 ', '').replace('🏦 ', '').replace(' (tahmini)', '').lower()
            if not any(clean_title in et for et in existing_titles):
                events.append(r)
    else:
        events = recurring

    # Tarihe göre sırala
    events.sort(key=lambda x: x.get('time', datetime.min.replace(tzinfo=timezone.utc)))

    _calendar_cache = {'events': events, 'ts': time.time()}
    print(f"[EconCal] TOPLAM: {len(events)} olay cached")
    return events


def get_upcoming_events():
    """
    Yaklaşan ekonomik olayları döndürür.
    Geçmiş olaylar hariç, sadece bugün ve ilerisi.
    Son 2 saat içinde gerçekleşenler de gösterilir (sonuçlarıyla).
    """
    try:
        all_events = fetch_economic_calendar()
        now = datetime.now(timezone.utc)
        upcoming = []

        for ev in all_events:
            try:
                ev_time = ev['time']
                # pd.Timestamp veya datetime olabilir — her ikisini de handle et
                if hasattr(ev_time, 'tz_localize'):
                    # pandas Timestamp
                    if ev_time.tzinfo is None:
                        ev_time = ev_time.tz_localize('UTC')
                    else:
                        ev_time = ev_time.tz_convert('UTC')
                else:
                    # stdlib datetime
                    if ev_time.tzinfo is None:
                        ev_time = ev_time.replace(tzinfo=timezone.utc)
                    else:
                        ev_time = ev_time.astimezone(timezone.utc)

                diff_hours = (ev_time - now).total_seconds() / 3600.0

                # Son 2 saat + gelecek tüm olaylar
                if diff_hours >= -2:
                    # Zaman durumu
                    if diff_hours < 0:
                        status = "GEÇTİ"
                        time_label = f"{abs(int(diff_hours * 60))} dk önce"
                    elif diff_hours < 1:
                        mins = int(diff_hours * 60)
                        status = "YAKLAŞIYOR"
                        time_label = f"{mins} dk sonra"
                    elif diff_hours < 24:
                        status = "BUGÜN"
                        time_label = ev_time.strftime('%H:%M UTC')
                    else:
                        days = int(diff_hours / 24)
                        status = f"{days} GÜN"
                        time_label = ev_time.strftime('%a %H:%M UTC')

                    # Kritiklik rengi
                    urgency = "critical" if diff_hours <= 0.25 and diff_hours > -0.25 else \
                              "imminent" if diff_hours <= 1 and diff_hours > 0 else \
                              "upcoming" if diff_hours <= 4 else "scheduled"

                    upcoming.append({
                        'title': ev['title'],
                        'time_label': time_label,
                        'time_utc': ev_time.strftime('%Y-%m-%d %H:%M'),
                        'status': status,
                        'urgency': urgency,
                        'impact': ev['impact'],
                        'gold_score': ev['gold_score'],
                        'gold_direction': ev['gold_direction'],
                        'gold_reason': ev['gold_reason'],
                        'forecast': ev.get('forecast', ''),
                        'previous': ev.get('previous', ''),
                    })
            except Exception:
                continue

        # Zamana göre sırala (en yakın olan en üstte)
        upcoming.sort(key=lambda x: x['time_utc'])
        return upcoming[:20]  # Max 20 olay

    except Exception:
        return []


def check_kill_switch():
    try:
        events = fetch_economic_calendar()
        now = datetime.now(timezone.utc)
        for ev in events:
            try:
                ev_time = ev['time']
                if hasattr(ev_time, 'tz_localize'):
                    if ev_time.tzinfo is None:
                        ev_time = ev_time.tz_localize('UTC')
                    else:
                        ev_time = ev_time.tz_convert('UTC')
                else:
                    if ev_time.tzinfo is None:
                        ev_time = ev_time.replace(tzinfo=timezone.utc)
                    else:
                        ev_time = ev_time.astimezone(timezone.utc)
                diff_minutes = (ev_time - now).total_seconds() / 60.0
                if -15 <= diff_minutes <= 15 and ev.get('impact') == 'High':
                    return {
                        "active": True, "event": ev['title'],
                        "message": f"KİLL-SWITCH AKTİF! KRİTİK VERİ: {ev['title'].upper()}"
                    }
            except Exception:
                continue
    except Exception:
        pass
    return {"active": False, "event": "", "message": ""}

# ─────────────────────────────────────────────────────────────
# ALTIN ODAKLI HABER & ETKİ ANALİZİ
# ─────────────────────────────────────────────────────────────
_news_cache = {'data': None, 'ts': 0}
NEWS_CACHE_TTL = 300

# Altını etkileyen haber kaynakları
NEWS_FEEDS = [
    # Altın direkt
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC=F",
    # Dolar endeksi & forex
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^DXY",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=EURUSD=X",
    # Genel piyasa & makro
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^TNX",
    # Enerji (petrol = enflasyon beklentisi)
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=CL=F",
    # ═══ JEOPOLİTİK & DÜNYA HABERLERİ ═══
    "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx1YlY4U0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en",  # Google News — World
    "https://news.google.com/rss/topics/CAAqIggKIhxDQkFTRHdvSkwyMHZNR2RtY0RFU0FtVnVLQUFQAQ?hl=en-US&gl=US&ceid=US:en",  # Google News — Business
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^VIX",  # VIX (Korku endeksi)
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SI=F",  # Gümüş (altınla birlikte hareket eder)
]

# ── Altın Etki Sözlüğü ──
# Her terim: (puan, kategori, altın_etkisi_açıklaması)
# Pozitif puan = altın yükselir, negatif = altın düşer

GOLD_IMPACT_TERMS = {
    # ═══ JEOPOLİTİK RİSK (Altın güvenli liman → YÜKSELİR) ═══
    'war':          (6, 'Jeopolitik',    'Savaş riski altını güvenli liman olarak yükseltir'),
    'invasion':     (6, 'Jeopolitik',    'İşgal haberleri altın talebini artırır'),
    'missile':      (5, 'Jeopolitik',    'Füze saldırıları küresel riski artırır → altın yükselir'),
    'attack':       (5, 'Jeopolitik',    'Saldırı haberi risk iştahını düşürür → altın yükselir'),
    'nuclear':      (7, 'Jeopolitik',    'Nükleer tehdit en üst düzey risk → altın sert yükselir'),
    'conflict':     (4, 'Jeopolitik',    'Çatışma haberleri altın talebini artırır'),
    'escalation':   (5, 'Jeopolitik',    'Gerilim tırmanması altını destekler'),
    'tension':      (3, 'Jeopolitik',    'Gerginlik artışı altını destekler'),
    'sanctions':    (4, 'Jeopolitik',    'Yaptırımlar küresel belirsizliği artırır → altın yükselir'),
    'israel':       (3, 'Jeopolitik',    'Ortadoğu riski altın fiyatını destekler'),
    'iran':         (3, 'Jeopolitik',    'İran gerilimi petrol + altını yükseltir'),
    'russia':       (3, 'Jeopolitik',    'Rusya gerilimi küresel riski artırır'),
    'ukraine':      (3, 'Jeopolitik',    'Ukrayna krizi altın talebini artırır'),
    'taiwan':       (4, 'Jeopolitik',    'Tayvan gerilimi ABD-Çin riskini artırır → altın yükselir'),
    'terror':       (5, 'Jeopolitik',    'Terör riski güvenli liman talebini artırır'),
    'military':     (3, 'Jeopolitik',    'Askeri harekat haberleri altını destekler'),

    # ═══ PARA POLİTİKASI & FED (Altın faize duyarlı) ═══
    'rate cut':     (5, 'FED',           'Faiz indirimi doları zayıflatır → altın yükselir'),
    'rate cuts':    (5, 'FED',           'Faiz indirim beklentisi altını destekler'),
    'dovish':       (4, 'FED',           'Güvercin FED = düşük faiz beklentisi → altın yükselir'),
    'easing':       (4, 'FED',           'Parasal gevşeme altını destekler'),
    'pause':        (2, 'FED',           'Faiz artışına ara = altın için hafif pozitif'),
    'rate hike':    (-5, 'FED',          'Faiz artışı doları güçlendirir → altın düşer'),
    'rate hikes':   (-5, 'FED',          'Faiz artış döngüsü altını baskılar'),
    'hawkish':      (-4, 'FED',          'Şahin FED = yüksek faiz beklentisi → altın düşer'),
    'tightening':   (-4, 'FED',          'Parasal sıkılaştırma altını baskılar'),
    'tapering':     (-3, 'FED',          'Varlık alım azaltımı altın için negatif'),
    'fed':          (1, 'FED',           'FED haberi — detaya bağlı'),
    'powell':       (1, 'FED',           'Powell açıklaması — detaya bağlı'),

    # ═══ ENFLASYON (Altın enflasyon koruması) ═══
    'inflation':    (4, 'Enflasyon',     'Yüksek enflasyon altını enflasyon koruması olarak yükseltir'),
    'cpi':          (3, 'Enflasyon',     'TÜFE verisi — yüksekse altın yükselir'),
    'pce':          (3, 'Enflasyon',     'PCE verisi FED\'in takip ettiği enflasyon ölçüsü'),
    'consumer price': (3, 'Enflasyon',   'Tüketici fiyatları altın için belirleyici'),
    'stagflation':  (5, 'Enflasyon',     'Stagflasyon = en iyi altın senaryosu (yüksek enflasyon + durgunluk)'),
    'deflation':    (-3, 'Enflasyon',    'Deflasyon altın talebini düşürür'),

    # ═══ DOLAR ENDEKSİ (Ters korelasyon) ═══
    'strong dollar': (-4, 'Dolar',       'Güçlü dolar altını doğrudan baskılar'),
    'dollar surge':  (-4, 'Dolar',       'Dolar yükselişi altını düşürür'),
    'dollar rally':  (-4, 'Dolar',       'Dolar rallisi altını baskılar'),
    'weak dollar':   (4, 'Dolar',        'Zayıf dolar altını destekler'),
    'dollar falls':  (4, 'Dolar',        'Dolar düşüşü altını yükseltir'),
    'dollar drops':  (4, 'Dolar',        'Dolar düşüşü altını yükseltir'),
    'dxy':           (0, 'Dolar',        'Dolar endeksi haberi — yöne bağlı'),

    # ═══ EKONOMİK VERİ ═══
    'recession':     (4, 'Ekonomi',      'Resesyon korkusu güvenli liman talebi → altın yükselir'),
    'slowdown':      (3, 'Ekonomi',      'Ekonomik yavaşlama altını destekler'),
    'crisis':        (5, 'Ekonomi',      'Kriz ortamı altın talebini sert artırır'),
    'debt':          (3, 'Ekonomi',      'Borç krizi altın talebini artırır'),
    'default':       (5, 'Ekonomi',      'Temerrüt riski altını sert yükseltir'),
    'unemployment':  (3, 'Ekonomi',      'İşsizlik artışı = ekonomik zayıflık → altın yükselir'),
    'jobs added':    (-3, 'Ekonomi',     'Güçlü istihdam = FED faiz artırır → altın düşer'),
    'nonfarm':       (2, 'Ekonomi',      'Tarım dışı istihdam — sonuca bağlı'),
    'payrolls':      (2, 'Ekonomi',      'İstihdam verisi — sonuca bağlı'),
    'gdp':           (-1, 'Ekonomi',     'GSYİH verisi — güçlüyse altın düşer'),
    'growth':        (-3, 'Ekonomi',     'Ekonomik büyüme risk iştahını artırır → altın düşer'),
    'strong economy': (-3, 'Ekonomi',    'Güçlü ekonomi altın talebini düşürür'),
    'recovery':      (-3, 'Ekonomi',     'Ekonomik toparlanma altını baskılar'),
    'soft landing':  (-2, 'Ekonomi',     'Yumuşak iniş senaryosu altın için hafif negatif'),
    'bank failure':  (5, 'Ekonomi',      'Banka çöküşü panik → altın sert yükselir'),
    'bank run':      (5, 'Ekonomi',      'Bankaya hücum finansal panik → altın yükselir'),

    # ═══ TİCARET & TARIFELER ═══
    'tariff':        (3, 'Ticaret',      'Gümrük vergisi belirsizliği artırır → altın yükselir'),
    'tariffs':       (3, 'Ticaret',      'Ticaret savaşı riski altını destekler'),
    'trade war':     (4, 'Ticaret',      'Ticaret savaşı küresel riski artırır → altın yükselir'),
    'trade deal':    (-3, 'Ticaret',     'Ticaret anlaşması riski azaltır → altın düşer'),
    'trade agreement': (-3, 'Ticaret',   'Anlaşma haberi altını baskılar'),

    # ═══ RİSK İŞTAHI (Altın ters korelasyon) ═══
    'peace':         (-5, 'Risk',        'Barış haberi güvenli liman talebini düşürür → altın düşer'),
    'ceasefire':     (-5, 'Risk',        'Ateşkes güvenli liman talebini azaltır → altın düşer'),
    'agreement':     (-3, 'Risk',        'Anlaşma haberi riski azaltır → altın düşer'),
    'deal':          (-2, 'Risk',        'Anlaşma haberi belirsizliği azaltır'),
    'truce':         (-4, 'Risk',        'Ateşkes altın talebini düşürür'),
    'stable':        (-2, 'Risk',        'İstikrar haberi altın talebini düşürür'),
    'optimism':      (-2, 'Risk',        'İyimserlik risk iştahını artırır → altın düşer'),
    'rally':         (-1, 'Risk',        'Piyasa rallisi altından çıkışa neden olabilir'),
    'record high':   (-1, 'Risk',        'Borsa rekoru = risk iştahı → altından çıkış'),

    # ═══ ALTIN DİREKT ═══
    'gold surges':   (3, 'Altın',        'Altın yükseliş momentumu devam ediyor'),
    'gold jumps':    (3, 'Altın',        'Altın sert yükselişte'),
    'gold soars':    (3, 'Altın',        'Altın fırlıyor'),
    'gold rises':    (2, 'Altın',        'Altın yükselişte'),
    'gold falls':    (-3, 'Altın',       'Altın düşüşte — satış baskısı'),
    'gold drops':    (-3, 'Altın',       'Altın sert düşüşte'),
    'gold slips':    (-2, 'Altın',       'Altın gevşiyor'),
    'gold tumbles':  (-4, 'Altın',       'Altın sert satılıyor'),
    'safe haven':    (3, 'Altın',        'Güvenli liman talebi altını destekliyor'),
    'haven demand':  (3, 'Altın',        'Güvenli liman talebi artıyor'),

    # ═══ TAHVİL & FAİZ ═══
    'bond yield':    (-2, 'Tahvil',      'Tahvil faizi yükselişi altını baskılar'),
    'yields rise':   (-3, 'Tahvil',      'Artan faizler altın için negatif'),
    'yields surge':  (-4, 'Tahvil',      'Faiz sert yükseliş altını sert baskılar'),
    'yields fall':   (3, 'Tahvil',       'Düşen faizler altını destekler'),
    'yields drop':   (3, 'Tahvil',       'Faiz düşüşü altını yükseltir'),
    'treasury':      (0, 'Tahvil',       'Hazine tahvili haberi — yöne bağlı'),

    # ═══ MERKEZ BANKALARI ALTIN ALIMI ═══
    'central bank':  (2, 'MerkezBankası', 'Merkez bankası haberi altını etkiler'),
    'gold reserves': (3, 'MerkezBankası', 'Altın rezervi artışı talebi destekler'),
    'gold buying':   (3, 'MerkezBankası', 'Merkez bankası altın alımı fiyatı destekler'),
    'china gold':    (3, 'MerkezBankası', 'Çin altın alımı küresel talebi artırır'),
    'brics':         (2, 'MerkezBankası', 'BRICS altın talebi destekliyor'),
    'de-dollarization': (4, 'MerkezBankası', 'Dolardan çıkış altın talebini artırır'),

    # ═══ ORTADOĞU JEOPOLİTİĞİ (2024-2026) ═══
    'ceasefire violation': (5, 'Jeopolitik', 'Ateşkes ihlali gerginliği artırır → altın yükselir'),
    'ceasefire deal':   (-4, 'Risk',        'Ateşkes anlaşması riski azaltır → altın düşer'),
    'ceasefire broken': (5, 'Jeopolitik',   'Bozulan ateşkes tekrar savaş riski → altın yükselir'),
    'hormuz':           (6, 'Jeopolitik',   'Hürmüz Boğazı tehdidi petrol + altını sert yükseltir'),
    'strait':           (4, 'Jeopolitik',   'Boğaz gerilimi deniz ticaretini tehdit eder → altın yükselir'),
    'blockade':         (5, 'Jeopolitik',   'Deniz ablukası küresel ticareti aksatır → altın yükselir'),
    'houthi':           (4, 'Jeopolitik',   'Husi saldırıları Kızıldeniz ticaretini tehdit eder → altın yükselir'),
    'hezbollah':        (4, 'Jeopolitik',   'Hizbullah gerilimi bölgesel savaş riskini artırır → altın yükselir'),
    'hamas':            (3, 'Jeopolitik',   'Hamas haberleri Ortadoğu gerilimini artırır → altın yükselir'),
    'gaza':             (3, 'Jeopolitik',   'Gazze krizi bölgesel istikrarsızlığı artırır → altın yükselir'),
    'lebanon':          (3, 'Jeopolitik',   'Lübnan gerilimi İsrail-İran cephesini genişletir → altın yükselir'),
    'syria':            (3, 'Jeopolitik',   'Suriye gerilimi bölgesel risk → altın yükselir'),
    'yemen':            (3, 'Jeopolitik',   'Yemen gerilimi Kızıldeniz ticaretini etkiler → altın yükselir'),
    'airstrike':        (5, 'Jeopolitik',   'Hava saldırısı askeri tırmanma → altın yükselir'),
    'airstrikes':       (5, 'Jeopolitik',   'Hava saldırıları jeopolitik riski artırır → altın yükselir'),
    'drone':            (4, 'Jeopolitik',   'Drone saldırısı jeopolitik gerilimi artırır → altın yükselir'),
    'drone strike':     (5, 'Jeopolitik',   'Drone saldırısı askeri tırmanma sinyali → altın yükselir'),
    'bombing':          (5, 'Jeopolitik',   'Bombalama haberi savaş riski → altın yükselir'),
    'retaliation':      (5, 'Jeopolitik',   'Misilleme tırmanma riski → altın yükselir'),
    'retaliate':        (5, 'Jeopolitik',   'Misilleme sinyali tırmanma → altın yükselir'),
    'troops':           (3, 'Jeopolitik',   'Askeri yığınak haberleri gerilimi artırır → altın yükselir'),
    'deployment':       (3, 'Jeopolitik',   'Askeri konuşlanma haberi gerginlik → altın yükselir'),
    'navy':             (3, 'Jeopolitik',   'Deniz kuvvetleri hareketi gerilim → altın yükselir'),
    'warship':          (4, 'Jeopolitik',   'Savaş gemisi haberi askeri gerilim → altın yükselir'),
    'aircraft carrier': (4, 'Jeopolitik',   'Uçak gemisi konuşlanması askeri tırmanma → altın yükselir'),
    'red sea':          (4, 'Jeopolitik',   'Kızıldeniz tehdidi deniz ticaretini aksatır → altın yükselir'),
    'suez':             (4, 'Jeopolitik',   'Süveyş Kanalı tehdidi küresel ticareti etkiler → altın yükselir'),

    # ═══ KÜRESEL JEOPOLİTİK ═══
    'china':            (2, 'Jeopolitik',   'Çin haberi — bağlama göre etkisi değişir'),
    'north korea':      (4, 'Jeopolitik',   'Kuzey Kore provokasyonu nükleer risk → altın yükselir'),
    'kim jong':         (4, 'Jeopolitik',   'Kuzey Kore lideri haberi nükleer gerilim → altın yükselir'),
    'coup':             (5, 'Jeopolitik',   'Darbe haberi siyasi istikrarsızlık → altın yükselir'),
    'assassination':    (6, 'Jeopolitik',   'Suikast haberi küresel şok → altın sert yükselir'),
    'martial law':      (5, 'Jeopolitik',   'Sıkıyönetim ilanı istikrarsızlık → altın yükselir'),
    'protest':          (2, 'Jeopolitik',   'Büyük protesto haberi siyasi risk → altın hafif yükselir'),
    'riot':             (3, 'Jeopolitik',   'İsyan/ayaklanma haberi istikrarsızlık → altın yükselir'),
    'revolution':       (5, 'Jeopolitik',   'Devrim haberi küresel belirsizlik → altın yükselir'),
    'regime':           (3, 'Jeopolitik',   'Rejim değişikliği haberi belirsizlik → altın yükselir'),

    # ═══ ENERJİ & EMTİA ═══
    'oil surge':        (3, 'Enerji',       'Petrol sert yükselişi enflasyon beklentisi → altın yükselir'),
    'oil spike':        (3, 'Enerji',       'Petrol fiyat sıçraması enflasyon → altın yükselir'),
    'oil soars':        (3, 'Enerji',       'Petrol fırlıyor enflasyon beklentisi → altın yükselir'),
    'oil crash':        (-2, 'Enerji',      'Petrol çöküşü deflasyon riski → altın düşebilir'),
    'opec':             (2, 'Enerji',       'OPEC haberi petrol fiyatını etkiler → altın dolaylı etki'),
    'opec cut':         (3, 'Enerji',       'OPEC üretim kesintisi petrol yükselir → enflasyon → altın yükselir'),
    'supply disruption': (4, 'Enerji',      'Arz kesintisi fiyatları yükseltir → altın yükselir'),
    'shipping disruption': (4, 'Enerji',    'Deniz taşımacılığı aksaması küresel ticareti etkiler → altın yükselir'),
    'pipeline':         (2, 'Enerji',       'Boru hattı haberi enerji arzını etkiler → altın dolaylı etki'),
    'embargo':          (5, 'Enerji',       'Ambargo küresel arzı kısıtlar → altın yükselir'),

    # ═══ KÜRESEL FİNANSAL RİSK ═══
    'contagion':        (5, 'Ekonomi',      'Finansal bulaşma riski küresel panik → altın yükselir'),
    'collapse':         (5, 'Ekonomi',      'Çöküş haberi panik satışı → altın güvenli liman olarak yükselir'),
    'bailout':          (3, 'Ekonomi',      'Kurtarma paketi finansal zayıflık sinyali → altın yükselir'),
    'liquidity':        (2, 'Ekonomi',      'Likidite krizi haberi → altın güvenli liman talebi artırır'),
    'sell-off':         (2, 'Ekonomi',      'Piyasa satışı risk iştahı düşer → altın yükselir'),
    'crash':            (4, 'Ekonomi',      'Piyasa çöküşü panik → altın güvenli liman olarak yükselir'),
    'bear market':      (3, 'Ekonomi',      'Ayı piyasası haberi risk → altın yükselir'),
    'vix':              (2, 'Ekonomi',      'VIX yükselişi korku endeksi → altın yükselir'),
    'fear':             (2, 'Ekonomi',      'Korku haberi risk iştahını düşürür → altın yükselir'),
    'panic':            (4, 'Ekonomi',      'Panik haberi güvenli liman talebi → altın yükselir'),
    'volatility':       (1, 'Ekonomi',      'Volatilite artışı belirsizlik → altın hafif yükselir'),
    'stimulus':         (3, 'FED',          'Teşvik paketi para arzı artışı → altın yükselir'),
    'quantitative':     (3, 'FED',          'Parasal genişleme altını destekler'),
    'money printing':   (4, 'FED',          'Para basımı enflasyon → altın yükselir'),
    'debt ceiling':     (4, 'Ekonomi',      'Borç tavanı krizi ABD temerrüt riski → altın yükselir'),
    'shutdown':         (3, 'Ekonomi',      'Hükümet kapanması siyasi belirsizlik → altın yükselir'),
    'downgrade':        (4, 'Ekonomi',      'Kredi notu düşürme güven kaybı → altın yükselir'),
}


def analyze_gold_impact(title):
    """
    Bir haber başlığını altın perspektifinden analiz eder.
    Döner: (toplam_skor, kategori, etki_açıklaması, eşleşen_terimler)
    """
    title_lower = title.lower()
    total = 0
    matched = []
    top_category = ""
    top_reason = ""
    max_abs_score = 0

    for term, (score, category, reason) in GOLD_IMPACT_TERMS.items():
        if term in title_lower:
            total += score
            matched.append(term)
            if abs(score) > max_abs_score:
                max_abs_score = abs(score)
                top_category = category
                top_reason = reason

    return total, top_category, top_reason, matched


def fetch_global_market_sentiment():
    """Tüm RSS kaynaklarından haber çeker ve altın etkisini analiz eder."""
    try:
        all_articles = []
        total_score = 0

        for url in NEWS_FEEDS:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:8]:
                    title = entry.title
                    # Duplikat kontrolü
                    if any(a['title'] == title for a in all_articles):
                        continue

                    score, category, reason, matched_terms = analyze_gold_impact(title)
                    pub_time = entry.published[5:22] if hasattr(entry, 'published') else ""
                    link = entry.link if hasattr(entry, 'link') else ""

                    # Sadece altını etkileyen haberleri al (skor 0 değilse)
                    if score != 0 or len(matched_terms) > 0:
                        sentiment = (
                            "bullish" if score >= 3
                            else "bearish" if score <= -3
                            else "slight_bull" if score > 0
                            else "slight_bear" if score < 0
                            else "neutral"
                        )
                        all_articles.append({
                            "title": title,
                            "link": link,
                            "sentiment": sentiment,
                            "score": score,
                            "time": pub_time,
                            "category": category,
                            "reason": reason,
                            "impact": "YÜKSELIR" if score > 0 else "DÜŞER" if score < 0 else "BELİRSİZ"
                        })
                        total_score += score
            except Exception:
                continue

        # Etkiye göre sırala (en yüksek etkili haber en üstte)
        all_articles = sorted(all_articles, key=lambda x: abs(x['score']), reverse=True)[:15]

        # Genel değerlendirme
        if total_score >= 20:
            overall_text = f"ALTIN İÇİN GÜÇLÜ YÜKSELİŞ ORTAMI 🚀 (Skor: {total_score})"
            overall_class = "bullish"
        elif total_score >= 8:
            overall_text = f"Altın İçin Yükseliş Eğilimi 🟢 (Skor: {total_score})"
            overall_class = "bullish"
        elif total_score >= 3:
            overall_text = f"Hafif Yükseliş Sinyali 🟢 (Skor: {total_score})"
            overall_class = "bullish"
        elif total_score <= -20:
            overall_text = f"ALTIN İÇİN GÜÇLÜ DÜŞÜŞ BASKISI 📉 (Skor: {total_score})"
            overall_class = "bearish"
        elif total_score <= -8:
            overall_text = f"Altın İçin Düşüş Eğilimi 🔴 (Skor: {total_score})"
            overall_class = "bearish"
        elif total_score <= -3:
            overall_text = f"Hafif Düşüş Sinyali 🔴 (Skor: {total_score})"
            overall_class = "bearish"
        else:
            overall_text = f"Haberler Nötr — Altın Tekniğe Bakıyor ⚖️ (Skor: {total_score})"
            overall_class = "neutral"

        # Kategori özeti
        cat_scores = {}
        for a in all_articles:
            cat = a.get('category', 'Diğer')
            if cat:
                cat_scores[cat] = cat_scores.get(cat, 0) + a['score']

        return {
            "overall_text": overall_text,
            "overall_class": overall_class,
            "total_score": total_score,
            "articles": all_articles,
            "category_scores": cat_scores,
            "article_count": len(all_articles)
        }
    except Exception:
        return {
            "overall_text": "Haberler Alınamadı", "overall_class": "neutral",
            "articles": [], "total_score": 0, "category_scores": {}, "article_count": 0
        }

def get_cached_news():
    global _news_cache
    with _cache_lock:
        if _news_cache['data'] and (time.time() - _news_cache['ts']) < NEWS_CACHE_TTL:
            return _news_cache['data']
    news_data = fetch_global_market_sentiment()
    with _cache_lock:
        _news_cache = {'data': news_data, 'ts': time.time()}
    return news_data

# ─────────────────────────────────────────
# VERİ ÇEKME
# ─────────────────────────────────────────
def fetch_timeseries(symbol, interval, outputsize=120):
    try:
        params = {
            "symbol": symbol, "interval": interval,
            "outputsize": outputsize, "apikey": TD_API_KEY,
            "format": "JSON", "timezone": "UTC"
        }
        resp = _safe_get(f"{TD_BASE_URL}/time_series", params)
        data = resp.json()
        if data.get("status") == "error" or not data.get("values"):
            return pd.DataFrame()
        df = pd.DataFrame(data.get("values"))
        if 'datetime' in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'])
            df = df.sort_values('datetime').reset_index(drop=True)
        for col in ['open', 'high', 'low', 'close']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        if 'volume' in df.columns:
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
        else:
            df['volume'] = 0
        return df
    except Exception as e:
        print(f"Fetch Hatası ({symbol}): {e}")
        return pd.DataFrame()

def get_gold(interval):
    with _cache_lock:
        cached = _gold_cache.get(interval)
        if cached and (time.time() - cached['ts']) < GOLD_TTL.get(interval, 58):
            return cached['df']
    df = fetch_timeseries(GOLD_SYMBOL, interval, INTERVAL_CONFIG[interval]['outputsize'])
    with _cache_lock:
        _gold_cache[interval] = {'df': df, 'ts': time.time()}
    return df

def get_dxy(interval='1min'):
    global _dxy_cache
    with _cache_lock:
        if not _dxy_cache['df'].empty and (time.time() - _dxy_cache['ts']) < DXY_TTL:
            return _dxy_cache['df'], _dxy_cache['sym']
    for candidate in DXY_CANDIDATES:
        df = fetch_timeseries(candidate, '1min', 60)
        if not df.empty:
            with _cache_lock:
                _dxy_cache = {'df': df, 'sym': candidate, 'ts': time.time()}
            return df, candidate
    return pd.DataFrame(), ""

def get_htf_data():
    """15 dakikalık veriyi çeker — Multi-Timeframe trend filtresi için."""
    global _htf_cache
    with _cache_lock:
        if not _htf_cache['df'].empty and (time.time() - _htf_cache['ts']) < HTF_TTL:
            return _htf_cache['df']
    df = fetch_timeseries(GOLD_SYMBOL, '15min', 80)
    if not df.empty:
        df = calculate_indicators(df.copy())
        with _cache_lock:
            _htf_cache = {'df': df, 'ts': time.time()}
    return df

# 5dk cache — orta vadeli momentum onayı
_mtf_cache = {'df': pd.DataFrame(), 'ts': 0}
MTF_TTL = 240  # 5dk cache 4 dakika — kalite filtresi için yeterli

# 5dk Pattern Detection Cache — v5.7 pattern strategy her zaman 5dk veri kullanır
_pattern_cache = {'df': pd.DataFrame(), 'ts': 0}
PATTERN_CACHE_TTL = 240  # 4 dakika cache

def get_mtf_data():
    """5 dakikalık veriyi çeker — Momentum onayı için."""
    global _mtf_cache
    with _cache_lock:
        if not _mtf_cache['df'].empty and (time.time() - _mtf_cache['ts']) < MTF_TTL:
            return _mtf_cache['df']
    df = fetch_timeseries(GOLD_SYMBOL, '5min', 60)
    if not df.empty:
        df = calculate_indicators(df.copy())
        with _cache_lock:
            _mtf_cache = {'df': df, 'ts': time.time()}
    return df

def get_pattern_data():
    """5dk veriyi çeker — v5.7 Pattern detection için (her zaman 5dk!)"""
    global _pattern_cache
    with _cache_lock:
        if not _pattern_cache['df'].empty and (time.time() - _pattern_cache['ts']) < PATTERN_CACHE_TTL:
            return _pattern_cache['df']
    df = fetch_timeseries(GOLD_SYMBOL, '5min', 200)  # 200 bar = ~16 saat
    if not df.empty:
        df = calculate_indicators(df.copy())
        with _cache_lock:
            _pattern_cache = {'df': df, 'ts': time.time()}
    return df

def fetch_market_data(interval='1min'):
    gold_df = get_gold(interval)
    dxy_df, dxy_sym = get_dxy(interval)
    return gold_df, dxy_df, dxy_sym

# ─────────────────────────────────────────
# İNDİKATÖR HESAPLAMA
# ─────────────────────────────────────────
def calculate_indicators(df):
    close = df['close']
    d = close.diff()

    # RSI (14)
    gain = d.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(com=13, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = (100 - 100 / (1 + rs)).fillna(50)

    # MACD (12, 26, 9)
    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    df['MACD'] = ema_fast - ema_slow
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

    # Hareketli Ortalamalar (SMA)
    df['MA20'] = close.rolling(window=20).mean()
    df['MA50'] = close.rolling(window=50).mean()

    # EMA'lar (v5.7 pattern strategy)
    df['EMA20'] = close.ewm(span=20, adjust=False).mean()
    df['EMA50'] = close.ewm(span=50, adjust=False).mean()

    # VWAP (20 periyot)
    tp = (df['high'] + df['low'] + close) / 3
    vol = df['volume'].copy()
    if vol.sum() == 0:
        vol = (df['high'] - df['low']) * 1000
    cumtp = (tp * vol).rolling(window=20).sum()
    cumvol = vol.rolling(window=20).sum().replace(0, np.nan)
    df['VWAP'] = (cumtp / cumvol).fillna(0)

    # ATR (14)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - close.shift()).abs(),
        (df['low'] - close.shift()).abs()
    ], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()

    # Bollinger Bantları (20, 2)
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    df['BB_Up'] = sma20 + 2 * std20
    df['BB_Mid'] = sma20
    df['BB_Low'] = sma20 - 2 * std20
    df['BB_Width'] = ((df['BB_Up'] - df['BB_Low']) / df['BB_Mid'] * 100).fillna(0)

    # BB Width percentile (son 50 mum içinde ne kadar dar?)
    df['BB_Width_Pct'] = df['BB_Width'].rolling(50).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100 if len(x) >= 10 else 50,
        raw=False
    ).fillna(50)

    return df

# ══════════════════════════════════════════════════════════════
# v5.7 PATTERN DETECTION FUNCTIONS
# ══════════════════════════════════════════════════════════════

def find_swings(df, idx, lookback):
    """
    Son 'lookback' bar içindeki swing high ve swing low noktalarını bul.
    Her swing: (bar_index, price)
    Lowercase column names: close, high, low (v5.7 backend compat)
    """
    start = max(0, idx - lookback)
    chunk_h = df['high'].values
    chunk_l = df['low'].values
    w = PATTERN_CONFIG['swing_window']

    swing_highs = []
    swing_lows = []

    for i in range(start + w, min(idx - w + 1, len(df) - w)):
        # Swing High: ortadaki bar her iki yandaki w bar'dan yüksek
        is_sh = True
        for j in range(1, w + 1):
            if chunk_h[i] <= chunk_h[i - j] or chunk_h[i] <= chunk_h[i + j]:
                is_sh = False
                break
        if is_sh:
            swing_highs.append((i, float(chunk_h[i])))

        # Swing Low
        is_sl = True
        for j in range(1, w + 1):
            if chunk_l[i] >= chunk_l[i - j] or chunk_l[i] >= chunk_l[i + j]:
                is_sl = False
                break
        if is_sl:
            swing_lows.append((i, float(chunk_l[i])))

    return swing_highs, swing_lows


def detect_double_bottom(swing_lows, swing_highs, current_price, atr, idx):
    """
    W kalıbı: İki benzer dip + aradaki tepe (neckline)
    Neckline kırılımında LONG
    """
    if len(swing_lows) < 2 or len(swing_highs) < 1:
        return None

    # Son iki swing low'u al
    for i in range(len(swing_lows) - 1, 0, -1):
        low2_idx, low2 = swing_lows[i]
        low1_idx, low1 = swing_lows[i - 1]

        # İki dip arası mesafe kontrolü (çok yakın veya çok uzak olmasın)
        bar_dist = low2_idx - low1_idx
        if bar_dist < 5 or bar_dist > PATTERN_CONFIG['pattern_lookback']:
            continue

        # Fiyat benzerliği kontrolü
        tolerance = PATTERN_CONFIG['double_tolerance_pct'] * atr
        if abs(low1 - low2) > tolerance:
            continue

        # Aradaki en yüksek nokta = neckline
        mid_highs = [h for h in swing_highs if low1_idx < h[0] < low2_idx]
        if not mid_highs:
            continue
        neckline_idx, neckline = max(mid_highs, key=lambda x: x[1])

        # Fiyat neckline'ı kırmış mı?
        if current_price > neckline:
            pattern_height = neckline - min(low1, low2)
            return {
                'pattern': 'DOUBLE_BOTTOM',
                'direction': 'LONG',
                'neckline': neckline,
                'height': pattern_height,
                'confidence': 82,
                'low1': low1, 'low2': low2,
            }

    return None


def detect_head_shoulders(swing_highs, swing_lows, current_price, atr, idx):
    """
    Sol omuz + kafa + sağ omuz: 3 tepe, ortadaki en yüksek
    Neckline (iki dip arası çizgi) kırılımında SHORT
    """
    if len(swing_highs) < 3 or len(swing_lows) < 2:
        return None

    for i in range(len(swing_highs) - 1, 2, -1):
        rs_idx, rs = swing_highs[i]       # Sağ omuz
        h_idx, h = swing_highs[i - 1]     # Kafa
        ls_idx, ls = swing_highs[i - 2]   # Sol omuz

        # Kafa en yüksek olmalı
        if h <= rs or h <= ls:
            continue

        # Omuzlar benzer yükseklikte
        tolerance = PATTERN_CONFIG['double_tolerance_pct'] * atr * 2
        if abs(ls - rs) > tolerance:
            continue

        # Bar mesafeleri mantıklı mı
        if (h_idx - ls_idx) < 3 or (rs_idx - h_idx) < 3:
            continue
        if (rs_idx - ls_idx) > PATTERN_CONFIG['pattern_lookback']:
            continue

        # Neckline: iki dip (omuzlar arası)
        left_lows = [l for l in swing_lows if ls_idx < l[0] < h_idx]
        right_lows = [l for l in swing_lows if h_idx < l[0] < rs_idx]
        if not left_lows or not right_lows:
            continue

        nl_left = min(left_lows, key=lambda x: x[1])[1]
        nl_right = min(right_lows, key=lambda x: x[1])[1]
        neckline = (nl_left + nl_right) / 2

        if current_price < neckline:
            pattern_height = h - neckline
            return {
                'pattern': 'HEAD_SHOULDERS',
                'direction': 'SHORT',
                'neckline': neckline,
                'height': pattern_height,
                'confidence': 82,
                'head': h, 'left_shoulder': ls, 'right_shoulder': rs,
            }

    return None


def detect_inv_head_shoulders(swing_lows, swing_highs, current_price, atr, idx):
    """
    Ters H&S: 3 dip, ortadaki en düşük → LONG
    """
    if len(swing_lows) < 3 or len(swing_highs) < 2:
        return None

    for i in range(len(swing_lows) - 1, 2, -1):
        rs_idx, rs = swing_lows[i]
        h_idx, h = swing_lows[i - 1]
        ls_idx, ls = swing_lows[i - 2]

        if h >= rs or h >= ls:
            continue

        tolerance = PATTERN_CONFIG['double_tolerance_pct'] * atr * 2
        if abs(ls - rs) > tolerance:
            continue

        if (h_idx - ls_idx) < 3 or (rs_idx - h_idx) < 3:
            continue
        if (rs_idx - ls_idx) > PATTERN_CONFIG['pattern_lookback']:
            continue

        left_highs = [x for x in swing_highs if ls_idx < x[0] < h_idx]
        right_highs = [x for x in swing_highs if h_idx < x[0] < rs_idx]
        if not left_highs or not right_highs:
            continue

        nl_left = max(left_highs, key=lambda x: x[1])[1]
        nl_right = max(right_highs, key=lambda x: x[1])[1]
        neckline = (nl_left + nl_right) / 2

        if current_price > neckline:
            pattern_height = neckline - h
            return {
                'pattern': 'INV_HEAD_SHOULDERS',
                'direction': 'LONG',
                'neckline': neckline,
                'height': pattern_height,
                'confidence': 84,
                'head': h, 'left_shoulder': ls, 'right_shoulder': rs,
            }

    return None


def detect_flag(df, idx, atr):
    """
    Flag kalıbı: Güçlü hareket (pole) + küçük konsolidasyon (flag)
    Bull flag: Yukarı pole + hafif düşüş konsolidasyonu → LONG
    Bear flag: Aşağı pole + hafif yükseliş konsolidasyonu → SHORT
    Lowercase columns: close, high, low
    """
    if idx < 30:
        return None

    close = float(df.iloc[idx]['close'])
    lookback = min(PATTERN_CONFIG['flag_max_consolidation'] + 10, idx)

    # Son N bar'da konsolidasyon var mı? (düşük volatilite bölgesi)
    recent = df.iloc[idx - lookback:idx + 1]
    recent_range = recent['high'].max() - recent['low'].min()
    recent_atr_avg = recent['ATR'].mean()

    # Konsolidasyon = range < 1.5 ATR
    if recent_range > 1.5 * recent_atr_avg:
        return None

    # Pole: konsolidasyondan önceki güçlü hareket
    pole_start = max(0, idx - lookback - 15)
    pole_end = idx - lookback
    if pole_end <= pole_start:
        return None

    pole_chunk = df.iloc[pole_start:pole_end + 1]
    pole_move = float(pole_chunk['close'].iloc[-1]) - float(pole_chunk['close'].iloc[0])
    pole_abs = abs(pole_move)

    if pole_abs < PATTERN_CONFIG['flag_min_pole_atr'] * atr:
        return None

    # Yön belirleme
    if pole_move > 0:
        # Bull flag: yukarı pole + konsolidasyon
        # Konsolidasyon hafif düşüş veya yatay olmalı
        consol_slope = close - float(df.iloc[idx - lookback]['close'])
        if consol_slope > 0.5 * atr:  # Konsolidasyon yukarı gidiyorsa flag değil
            return None
        return {
            'pattern': 'BULL_FLAG',
            'direction': 'LONG',
            'neckline': recent['high'].max(),
            'height': pole_abs,
            'confidence': 65,
            'pole_move': pole_move,
        }
    else:
        # Bear flag
        consol_slope = close - float(df.iloc[idx - lookback]['close'])
        if consol_slope < -0.5 * atr:
            return None
        return {
            'pattern': 'BEAR_FLAG',
            'direction': 'SHORT',
            'neckline': recent['low'].min(),
            'height': pole_abs,
            'confidence': 65,
            'pole_move': pole_move,
        }


def detect_patterns(df, idx, atr):
    """Tüm kalıpları tara, en güvenilir olanı döndür"""
    swing_highs, swing_lows = find_swings(df, idx, PATTERN_CONFIG['pattern_lookback'])
    current_price = float(df.iloc[idx]['close'])

    patterns_found = []

    # Double Bottom
    p = detect_double_bottom(swing_lows, swing_highs, current_price, atr, idx)
    if p:
        patterns_found.append(p)

    # Head & Shoulders
    p = detect_head_shoulders(swing_highs, swing_lows, current_price, atr, idx)
    if p:
        patterns_found.append(p)

    # Inverse Head & Shoulders
    p = detect_inv_head_shoulders(swing_lows, swing_highs, current_price, atr, idx)
    if p:
        patterns_found.append(p)

    # Bull/Bear Flag
    p = detect_flag(df, idx, atr)
    if p:
        patterns_found.append(p)

    if not patterns_found:
        return None

    # En yüksek güvenilirliğe sahip kalıbı seç
    return max(patterns_found, key=lambda x: x['confidence'])


def generate_pattern_signal(current_price, atr_val, balance):
    """
    v5.7 Pattern-based signal generator
    ALWAYS uses 5-min data for pattern detection (tested on 5min backtest)
    - Needs at least 65 bars of data (pattern_lookback + swing_window + 20)
    - Calls detect_patterns on the last bar
    - Checks EMA filter (EMA20 > EMA50 for LONG, vice versa)
    - Calculates equity-based lot sizing
    - Calculates dynamic TP based on pattern height
    - Returns pattern info, direction, lot size, SL/TP values
    - Returns None if no valid pattern found
    """
    # Always fetch 5min data for pattern detection
    gold_df = get_pattern_data()
    if gold_df.empty:
        return None

    min_bars = PATTERN_CONFIG['pattern_lookback'] + PATTERN_CONFIG['swing_window'] + 20
    if len(gold_df) < min_bars:
        return None

    # Missing indicators check
    if 'ATR' not in gold_df.columns or 'EMA20' not in gold_df.columns or 'EMA50' not in gold_df.columns:
        return None

    idx = len(gold_df) - 1
    atr = safe_float(gold_df.iloc[idx]['ATR'], 0)
    ema20 = safe_float(gold_df.iloc[idx]['EMA20'], 0)
    ema50 = safe_float(gold_df.iloc[idx]['EMA50'], 0)

    if atr <= 0:
        return None

    # Detect pattern
    pattern_info = detect_patterns(gold_df, idx, atr)
    if not pattern_info:
        return None

    direction = pattern_info.get('direction', 'LONG')
    pattern_name = pattern_info.get('pattern', 'UNKNOWN')
    confidence = pattern_info.get('confidence', 0)
    pattern_height = pattern_info.get('height', 0)

    # v5.7: EMA Filter
    if TRADE_MGMT['ema_filter_enabled']:
        if direction == 'LONG' and ema20 <= ema50:
            return None  # Bearish EMA → skip LONG
        elif direction == 'SHORT' and ema20 >= ema50:
            return None  # Bullish EMA → skip SHORT

    # Calculate SL (use neckline if available, else ATR-based)
    neckline = pattern_info.get('neckline', 0)
    if direction == 'LONG':
        sl = neckline - (0.3 * atr) if neckline > 0 else current_price - (2.0 * atr)
    else:
        sl = neckline + (0.3 * atr) if neckline > 0 else current_price + (2.0 * atr)

    sl_distance = abs(current_price - sl)

    # Calculate lot size (equity-based)
    if TRADE_MGMT['equity_lot_enabled']:
        risk_amount = balance * (TRADE_MGMT['equity_risk_pct'] / 100)
        # Apply high confidence multiplier
        if confidence >= TRADE_MGMT['high_conf_threshold']:
            risk_amount *= TRADE_MGMT['equity_high_conf_mult']
        raw_lot = risk_amount / (sl_distance * ACCOUNT_CONFIG['contract_size'])
        lot = round(max(min(raw_lot, ACCOUNT_CONFIG['max_lot']), ACCOUNT_CONFIG['min_lot']), 2)
    else:
        lot = ACCOUNT_CONFIG['min_lot']

    # Calculate dynamic TP
    if TRADE_MGMT['dynamic_tp_enabled'] and pattern_height > 0:
        tp_dollars = pattern_height * TRADE_MGMT['dynamic_tp_multiplier'] * lot * ACCOUNT_CONFIG['contract_size']
        tp_dollars = max(TRADE_MGMT['dynamic_tp_min'], min(tp_dollars, TRADE_MGMT['dynamic_tp_max']))
    else:
        tp_dollars = TRADE_MGMT['tp_dollars']

    # Calculate TP1 and TP2
    if direction == 'LONG':
        tp1 = current_price + (tp_dollars / (lot * ACCOUNT_CONFIG['contract_size']))
        tp2 = current_price + (tp_dollars * 1.5 / (lot * ACCOUNT_CONFIG['contract_size']))
    else:
        tp1 = current_price - (tp_dollars / (lot * ACCOUNT_CONFIG['contract_size']))
        tp2 = current_price - (tp_dollars * 1.5 / (lot * ACCOUNT_CONFIG['contract_size']))

    return {
        'pattern': pattern_name,
        'direction': direction,
        'confidence': confidence,
        'lot': lot,
        'entry': current_price,
        'sl': sl,
        'tp1': tp1,
        'tp2': tp2,
        'dynamic_tp_dollars': tp_dollars,
        'pattern_height': pattern_height,
        'pattern_info': pattern_info,
    }


def rsi_signal(v):
    if v >= 70:
        return "Aşırı Alım 🔴"
    elif v <= 30:
        return "Aşırı Satım 🟢"
    return "Nötr ⚪"

def get_market_note(dxy_df, dxy_sym):
    if dxy_df.empty or len(dxy_df) < 2:
        return "Piyasa verileri toplanıyor..."
    chg = ((dxy_df['close'].iloc[-1] - dxy_df['close'].iloc[0]) / dxy_df['close'].iloc[0]) * 100
    if "EUR/USD" in dxy_sym:
        chg = -chg
    if chg > 0.2:
        return "⚠️ DXY güçlü yükselişte. Altın üzerinde belirgin satış baskısı olabilir."
    elif chg > 0:
        return "⚠️ Dolar Endeksi yükselişte. Altın üzerinde satış baskısı olabilir."
    elif chg < -0.2:
        return "✅ DXY güçlü düşüşte. Altın için güçlü destek sinyali."
    elif chg < 0:
        return "✅ Dolar Endeksi düşüşte. Bu durum altın fiyatını destekliyor."
    return "⚖️ Dolar yatay seyrediyor. Altın teknik seviyelerine göre hareket ediyor."

# ══════════════════════════════════════════════════════════════
# AKILLI SL / TP HESAPLAMA
# Swing Noktası + Dinamik ATR + Risk:Reward
# ══════════════════════════════════════════════════════════════

def find_swing_levels(df, lookback=20):
    """
    Son N mumda swing high ve swing low noktalarını bulur.
    3'lü pencere: Bir mum, sağ ve solundaki 2 mumdan yüksek/düşükse swing'dir.
    """
    if len(df) < lookback:
        return [], []

    recent = df.tail(lookback)
    highs = recent['high'].values
    lows = recent['low'].values

    swing_highs = []
    swing_lows = []

    for i in range(2, len(highs) - 2):
        if (highs[i] > highs[i-1] and highs[i] > highs[i-2]
                and highs[i] > highs[i+1] and highs[i] > highs[i+2]):
            swing_highs.append(float(highs[i]))
        if (lows[i] < lows[i-1] and lows[i] < lows[i-2]
                and lows[i] < lows[i+1] and lows[i] < lows[i+2]):
            swing_lows.append(float(lows[i]))

    return swing_highs, swing_lows


def get_dynamic_atr_multiplier(bb_width_pct):
    """
    Bollinger bant genişliğine göre ATR çarpanını dinamik ayarlar.
    Dar piyasa (squeeze) → dar SL (1.2x), geniş piyasa → geniş SL (2.5x).
    """
    # v3.1: Minimum 2.0x ATR (daha geniş SL, daha az stop-out)
    if bb_width_pct <= 15:
        return 2.0   # v3.1: 1.2 → 2.0 (sıkışmada bile yeterli nefes payı)
    elif bb_width_pct <= 30:
        return 2.0   # v3.1: 1.5 → 2.0
    elif bb_width_pct <= 50:
        return 2.2   # Orta — standart
    elif bb_width_pct <= 75:
        return 2.8   # Geniş — volatil piyasa
    else:
        return 3.2   # Çok geniş — aşırı volatilite


def calculate_smart_sl_tp(df, current_price, atr_val, trend_dir, bb_width_pct):
    """
    3 katmanlı akıllı SL/TP hesaplayıcı:
    1. Swing noktasına bak (gerçek destek/direnç)
    2. Dinamik ATR çarpanıyla minimum mesafe belirle
    3. SL mesafesinden R:R bazlı TP hesapla (TP1=1.5R, TP2=3.0R)
    """
    swing_highs, swing_lows = find_swing_levels(df)
    dyn_mult = get_dynamic_atr_multiplier(bb_width_pct)
    min_sl_distance = dyn_mult * atr_val  # Minimum SL mesafesi (ATR bazlı)

    sl = 0
    sl_source = ""

    if trend_dir == 'bullish':
        # LONG SL: Son swing low'un altı
        valid_swing_lows = [s for s in swing_lows if s < current_price]
        if valid_swing_lows:
            nearest_swing_low = max(valid_swing_lows)  # En yakın swing low
            swing_distance = current_price - nearest_swing_low

            if swing_distance >= (0.5 * atr_val):
                # Swing mesafesi mantıklıysa onu kullan
                sl = nearest_swing_low - (0.3 * atr_val)  # Swing'in biraz altı (wick koruması)
                sl_source = "Swing Low"
            else:
                # Swing çok yakınsa ATR'yi kullan
                sl = current_price - min_sl_distance
                sl_source = f"ATR ({dyn_mult:.1f}x)"
        else:
            sl = current_price - min_sl_distance
            sl_source = f"ATR ({dyn_mult:.1f}x)"

    elif trend_dir == 'bearish':
        # SHORT SL: Son swing high'ın üstü
        valid_swing_highs = [s for s in swing_highs if s > current_price]
        if valid_swing_highs:
            nearest_swing_high = min(valid_swing_highs)  # En yakın swing high
            swing_distance = nearest_swing_high - current_price

            if swing_distance >= (0.5 * atr_val):
                sl = nearest_swing_high + (0.3 * atr_val)  # Swing'in biraz üstü
                sl_source = "Swing High"
            else:
                sl = current_price + min_sl_distance
                sl_source = f"ATR ({dyn_mult:.1f}x)"
        else:
            sl = current_price + min_sl_distance
            sl_source = f"ATR ({dyn_mult:.1f}x)"

    else:
        # Nötr — varsayılan ATR bazlı
        sl = current_price - min_sl_distance
        sl_source = f"ATR ({dyn_mult:.1f}x)"

    # ── R:R Bazlı TP Hesaplama ──
    # v3.1: TP1 = 1.5R, TP2 = 2.5R (3.0 → 2.5, daha gerçekçi hedef)
    sl_distance = abs(current_price - sl)

    if trend_dir == 'bullish':
        tp1 = current_price + (1.5 * sl_distance)  # 1:1.5 R:R
        tp2 = current_price + (2.5 * sl_distance)  # 1:2.5 R:R (v3.1)
    elif trend_dir == 'bearish':
        tp1 = current_price - (1.5 * sl_distance)
        tp2 = current_price - (2.5 * sl_distance)
    else:
        tp1 = current_price + (1.5 * sl_distance)
        tp2 = current_price + (2.5 * sl_distance)

    return (
        round(sl, 2), round(tp1, 2), round(tp2, 2),
        sl_source, round(sl_distance, 2), round(dyn_mult, 1)
    )


# ══════════════════════════════════════════════════════════════
# BİRLEŞİK SİNYAL MOTORU
# MTF Trend + Price Action Yapı + Bollinger Squeeze + Momentum
# ══════════════════════════════════════════════════════════════

def detect_htf_trend():
    """
    KATMAN 1 — Multi-Timeframe Trend Filtresi (15dk)
    Ana trendi belirler. 1dk sinyalleri sadece bu yönde alınır.
    Döner: 'bullish', 'bearish', 'nötr'
    """
    htf_df = get_htf_data()
    if htf_df.empty or len(htf_df) < 50:
        return 'nötr', "HTF veri yetersiz"

    last = htf_df.iloc[-1]
    close = safe_float(last.get('close'))
    ma20 = safe_float(last.get('MA20'))
    ma50 = safe_float(last.get('MA50'))
    macd = safe_float(last.get('MACD'))
    macd_s = safe_float(last.get('MACD_Signal'))
    rsi = safe_float(last.get('RSI'), 50.0)

    bull_pts = 0
    bear_pts = 0

    # Fiyat > MA50 (15dk) = güçlü yükseliş trendi
    if ma50 > 0:
        if close > ma50:
            bull_pts += 3
        else:
            bear_pts += 3

    # Fiyat > MA20 (15dk)
    if ma20 > 0:
        if close > ma20:
            bull_pts += 2
        else:
            bear_pts += 2

    # MA20 > MA50 = golden cross (15dk)
    if ma20 > 0 and ma50 > 0:
        if ma20 > ma50:
            bull_pts += 2
        else:
            bear_pts += 2

    # MACD (15dk)
    if macd > macd_s:
        bull_pts += 1
    else:
        bear_pts += 1

    total = bull_pts - bear_pts
    # v3.1: Eşik 7 → 5 (daha fazla trend tespiti, neutral süresini azalt)
    if total >= 5:
        return 'bullish', f"15dk YUKARI (Güç: {total})"
    elif total <= -5:
        return 'bearish', f"15dk AŞAĞI (Güç: {abs(total)})"
    return 'nötr', f"15dk YATAY (Fark: {total})"


def detect_structure(df, lookback=20):
    """
    KATMAN 2 — Price Action Yapı Analizi
    Son N mumda Higher High / Higher Low veya Lower High / Lower Low yapısı.
    Break of Structure (BOS) tespiti.
    """
    if len(df) < lookback + 5:
        return 'nötr', False, "Veri yetersiz"

    recent = df.tail(lookback)
    highs = recent['high'].values
    lows = recent['low'].values

    # Swing noktalarını bul (basit: 3'lü pencere)
    swing_highs = []
    swing_lows = []
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            swing_highs.append(highs[i])
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            swing_lows.append(lows[i])

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return 'nötr', False, "Swing yetersiz"

    # Higher Highs + Higher Lows = Yükseliş yapısı
    hh = swing_highs[-1] > swing_highs[-2]
    hl = swing_lows[-1] > swing_lows[-2]
    # Lower Highs + Lower Lows = Düşüş yapısı
    lh = swing_highs[-1] < swing_highs[-2]
    ll = swing_lows[-1] < swing_lows[-2]

    # Break of Structure (BOS): Son fiyat en son swing'i kırdı mı?
    last_close = df['close'].iloc[-1]
    bos = False

    if hh and hl:
        # Yükseliş yapısı — BOS: fiyat son swing low'u kırdıysa yapı bozuldu
        if last_close < swing_lows[-1]:
            bos = True
            return 'bearish', True, "Yükseliş yapısı KIRILDI (BOS)"
        return 'bullish', False, f"HH+HL Yapısı (Sağlam)"
    elif lh and ll:
        # Düşüş yapısı — BOS: fiyat son swing high'ı kırdıysa yapı bozuldu
        if last_close > swing_highs[-1]:
            bos = True
            return 'bullish', True, "Düşüş yapısı KIRILDI (BOS)"
        return 'bearish', False, f"LH+LL Yapısı (Sağlam)"
    else:
        return 'nötr', False, "Karışık yapı"


def detect_squeeze(df):
    """
    KATMAN 3 — Bollinger Squeeze Tespiti
    BB genişliği son 50 mumun en dar %20'sindeyse = SQUEEZE (sıkışma).
    Squeeze sonrası patlama yönünü MACD histogram ile belirler.
    """
    if len(df) < 20:
        return False, 'nötr', "Veri yetersiz"

    last = df.iloc[-1]
    bb_width_pct = safe_float(last.get('BB_Width_Pct'), 50.0, 1)
    macd_hist = safe_float(last.get('MACD_Hist'), 0.0, 4)
    close = safe_float(last.get('close'))
    bb_mid = safe_float(last.get('BB_Mid'))

    is_squeeze = bb_width_pct <= 20  # En dar %20

    if is_squeeze:
        # Patlama yönü: MACD histogram + fiyat BB orta çizgisine göre
        if macd_hist > 0 and close > bb_mid:
            return True, 'bullish', f"SQUEEZE → YUKARI (BB%: {bb_width_pct:.0f})"
        elif macd_hist < 0 and close < bb_mid:
            return True, 'bearish', f"SQUEEZE → AŞAĞI (BB%: {bb_width_pct:.0f})"
        else:
            return True, 'nötr', f"SQUEEZE AKTİF — Yön bekleniyor (BB%: {bb_width_pct:.0f})"

    return False, 'nötr', ""


# ═══════════════════════════════════════════
# AKILLI SİNYAL KALİTE FİLTRESİ
# ═══════════════════════════════════════════
MIN_QUALITY_GUCLU = 3   # v3.4: 3/9
MIN_QUALITY_ORTA = 4    # v3.6 orijinal (v3.8'de 5 yapıldı ama kâr düştü)
MOMENTUM_LOOKBACK = 5

def calculate_signal_quality(gold_df, current_price, trend_dir, atr_val, rsi_val, vwap_val, bb_mid, bb_width_pct):
    """
    Her sinyal için 0-9 arası kalite skoru hesaplar.
    v3.0 — 9 kriterli gelişmiş filtre (backtest ile senkron + MTF)
    """
    score = 0
    reasons = []

    # 1) RSI Momentum — RSI sinyalle aynı yöne mi gidiyor?
    if len(gold_df) >= 4:
        rsi_now = rsi_val
        rsi_prev = float(gold_df['RSI'].iloc[-4]) if 'RSI' in gold_df.columns else rsi_val
        if trend_dir == 'bullish' and rsi_now > rsi_prev:
            score += 1
            reasons.append("RSI↑")
        elif trend_dir == 'bearish' and rsi_now < rsi_prev:
            score += 1
            reasons.append("RSI↓")

    # 2) Mum Momentum — Son N mumun çoğunluğu sinyalle uyumlu mu?
    if len(gold_df) >= MOMENTUM_LOOKBACK + 1:
        col = 'Close' if 'Close' in gold_df.columns else 'close'
        closes = gold_df[col].iloc[-(MOMENTUM_LOOKBACK + 1):].values
        up_moves = sum(1 for j in range(len(closes) - 1) if closes[j + 1] > closes[j])
        down_moves = MOMENTUM_LOOKBACK - up_moves
        if trend_dir == 'bullish' and up_moves >= 3:
            score += 1
            reasons.append("Momentum↑")
        elif trend_dir == 'bearish' and down_moves >= 3:
            score += 1
            reasons.append("Momentum↓")

    # 3) BB Pozisyon — Fiyat uygun BB tarafında mı?
    if bb_mid > 0:
        if trend_dir == 'bullish' and current_price < bb_mid:
            score += 1
            reasons.append("BB_alt")
        elif trend_dir == 'bearish' and current_price > bb_mid:
            score += 1
            reasons.append("BB_üst")

    # 4) ATR Gücü — Volatilite yeterli mi?
    if atr_val >= 0.80 * 1.5:
        score += 1
        reasons.append("ATR_güçlü")

    # 5) VWAP Onay — Fiyat VWAP'la uyumlu mu?
    if vwap_val > 0:
        if trend_dir == 'bullish' and current_price > vwap_val:
            score += 1
            reasons.append("VWAP↑")
        elif trend_dir == 'bearish' and current_price < vwap_val:
            score += 1
            reasons.append("VWAP↓")

    # 6) MUM GÖVDESİ ANALİZİ — Mum sinyalle uyumlu güçlü mum mu? (+1) [v3.0 YENİ]
    last_row = gold_df.iloc[-1]
    col_close = 'Close' if 'Close' in gold_df.columns else 'close'
    col_open = 'Open' if 'Open' in gold_df.columns else 'open'
    col_high = 'High' if 'High' in gold_df.columns else 'high'
    col_low = 'Low' if 'Low' in gold_df.columns else 'low'
    c_close = safe_float(last_row.get(col_close), 0)
    c_open = safe_float(last_row.get(col_open), 0)
    c_high = safe_float(last_row.get(col_high), 0)
    c_low = safe_float(last_row.get(col_low), 0)
    body = c_close - c_open
    candle_range = c_high - c_low if c_high > c_low else 0.001
    body_ratio = abs(body) / candle_range
    if trend_dir == 'bullish' and body > 0 and body_ratio > 0.5:
        score += 1
        reasons.append("Mum_güçlü↑")
    elif trend_dir == 'bearish' and body < 0 and body_ratio > 0.5:
        score += 1
        reasons.append("Mum_güçlü↓")

    # 7) MACD HİSTOGRAM İVME — Histogram art arda büyüyor mu? (+1) [v3.0 YENİ]
    if len(gold_df) >= 3 and 'MACD_Hist' in gold_df.columns:
        hist_now = safe_float(gold_df['MACD_Hist'].iloc[-1], 0)
        hist_prev = safe_float(gold_df['MACD_Hist'].iloc[-2], 0)
        hist_prev2 = safe_float(gold_df['MACD_Hist'].iloc[-3], 0)
        if trend_dir == 'bullish' and hist_now > hist_prev > hist_prev2:
            score += 1
            reasons.append("MACD_ivme↑")
        elif trend_dir == 'bearish' and hist_now < hist_prev < hist_prev2:
            score += 1
            reasons.append("MACD_ivme↓")

    # 8) FİYAT-MA20 YAKINLIĞI — Fiyat MA20'ye yakın mı? (pullback girişi) (+1) [v3.0 YENİ]
    if 'MA20' in gold_df.columns:
        ma20 = safe_float(gold_df['MA20'].iloc[-1], 0)
        if ma20 > 0 and atr_val > 0:
            dist_to_ma20 = abs(current_price - ma20) / atr_val
            if dist_to_ma20 < 1.0:
                score += 1
                reasons.append("MA20_yakın")

    # 9) 5dk Multi-Timeframe Momentum Onayı (GERÇEK VERİ)
    try:
        mtf_df = get_mtf_data()
        if not mtf_df.empty and len(mtf_df) >= 3:
            mtf_last = mtf_df.iloc[-1]
            mtf_macd = safe_float(mtf_last.get('MACD'), 0)
            mtf_macd_s = safe_float(mtf_last.get('MACD_Signal'), 0)
            mtf_rsi = safe_float(mtf_last.get('RSI'), 50)
            mtf_bullish = mtf_macd > mtf_macd_s and mtf_rsi > 45
            mtf_bearish = mtf_macd < mtf_macd_s and mtf_rsi < 55

            if trend_dir == 'bullish' and mtf_bullish:
                score += 1
                reasons.append("5dk↑")
            elif trend_dir == 'bearish' and mtf_bearish:
                score += 1
                reasons.append("5dk↓")
    except Exception:
        pass  # 5dk verisi yoksa bu kriteri atla

    return score, reasons


def generate_composite_signal(gold_df, mas, current_price, atr_val, macd_v, macd_s,
                               macd_h, rsi_val, vwap_val):
    """
    BİRLEŞİK SİNYAL ÜRETİCİ
    3 Katman Yön Tespiti + Akıllı SL/TP (Swing + Dinamik ATR + R:R)
    """

    # ── Katman 1: HTF Trend ──
    htf_trend, htf_note = detect_htf_trend()

    # ── Katman 2: Yapı Analizi (1dk) ──
    structure_trend, bos_detected, structure_note = detect_structure(gold_df)

    # ── Katman 3: Squeeze Tespiti (1dk) ──
    is_squeeze, squeeze_dir, squeeze_note = detect_squeeze(gold_df)

    # ── Ek Momentum Onayları ──
    macd_bullish = macd_v > macd_s
    vwap_bullish = current_price > vwap_val if vwap_val > 0 else None
    # v3.1: RSI filtre — momentum tükenmeden gir
    rsi_ok_long = rsi_val < 62     # v3.1: 70 → 62
    rsi_ok_short = rsi_val > 38    # v3.1: 30 → 38

    # ── Katmanları Say ──
    bull_layers = 0
    bear_layers = 0

    if htf_trend == 'bullish':
        bull_layers += 1
    elif htf_trend == 'bearish':
        bear_layers += 1

    if structure_trend == 'bullish':
        bull_layers += 1
    elif structure_trend == 'bearish':
        bear_layers += 1

    if is_squeeze and squeeze_dir == 'bullish':
        bull_layers += 1
    elif is_squeeze and squeeze_dir == 'bearish':
        bear_layers += 1
    elif not is_squeeze:
        if macd_bullish and (vwap_bullish is True):
            bull_layers += 1
        elif not macd_bullish and (vwap_bullish is False):
            bear_layers += 1

    # ── BB Width Pct (Dinamik ATR çarpanı için) ──
    bb_width_pct = safe_float(gold_df.iloc[-1].get('BB_Width_Pct'), 50.0, 1) if len(gold_df) > 0 else 50.0

    # ── Nihai Yön Kararı ──
    trend = "nötr"
    sig_type = ""
    confidence = ""
    trend_dir = "nötr"  # calculate_smart_sl_tp için

    if bull_layers >= 3 and rsi_ok_long:
        trend = "bullish"
        trend_dir = "bullish"
        confidence = "GÜÇLÜ"
        sig_type = f"SCALP LONG — {confidence} TEYİT (3/3) 🟢"

    elif bear_layers >= 3 and rsi_ok_short:
        trend = "bearish"
        trend_dir = "bearish"
        confidence = "GÜÇLÜ"
        sig_type = f"SCALP SHORT — {confidence} TEYİT (3/3) 🔴"

    elif bull_layers >= 2 and bear_layers == 0 and rsi_ok_long:
        trend = "bullish"
        trend_dir = "bullish"
        confidence = "ORTA"
        sig_type = f"YÜKSELİŞ EĞİLİMİ — {confidence} TEYİT ({bull_layers}/3) 🟢"

    elif bear_layers >= 2 and bull_layers == 0 and rsi_ok_short:
        trend = "bearish"
        trend_dir = "bearish"
        confidence = "ORTA"
        sig_type = f"DÜŞÜŞ EĞİLİMİ — {confidence} TEYİT ({bear_layers}/3) 🔴"

    elif bull_layers > bear_layers:
        trend = "bullish"
        trend_dir = "bullish"
        confidence = "ZAYIF"
        sig_type = f"HAFİF YÜKSELİŞ — ({bull_layers}/3) 🟡"

    elif bear_layers > bull_layers:
        trend = "bearish"
        trend_dir = "bearish"
        confidence = "ZAYIF"
        sig_type = f"HAFİF DÜŞÜŞ — ({bear_layers}/3) 🟡"

    else:
        trend = "nötr"
        trend_dir = "nötr"
        confidence = "YOK"
        if bos_detected:
            sig_type = "⚡ YAPI KIRILIMI — Yeni yön oluşuyor ⚪"
        elif is_squeeze:
            sig_type = "🔸 SIKIŞMA — Patlama bekleniyor ⚪"
        else:
            sig_type = "YATAY / KARARSIZ ⚪"

    # ═══ AKILLI SL/TP HESAPLA ═══
    sl, tp1, tp2, sl_source, sl_distance, dyn_mult = calculate_smart_sl_tp(
        gold_df, current_price, atr_val, trend_dir, bb_width_pct
    )

    # v3.9: Dinamik TP2 kaldırıldı — v3.6 orijinal TP2 = 2.5R tüm sinyallerde
    # (v3.8'de 3.5R denendi ama GÜÇLÜ sinyallerde TP2'ye asla ulaşamadı)

    # Analiz detayları
    analysis = {
        "htf": htf_note,
        "structure": structure_note,
        "squeeze": squeeze_note if squeeze_note else "Squeeze yok",
        "macd": "YUKARI" if macd_bullish else "AŞAĞI",
        "vwap": "ÜSTÜNDE" if vwap_bullish else "ALTINDA" if vwap_bullish is False else "N/A",
        "confidence": confidence,
        "layers": f"Bull:{bull_layers} Bear:{bear_layers}",
        "sl_source": sl_source,
        "sl_distance": sl_distance,
        "atr_mult": dyn_mult,
        "rr_tp1": "1:2.0",
        "rr_tp2": "1:3.5"
    }

    # 5dk Multi-Timeframe bilgisi ekle
    try:
        mtf_df = get_mtf_data()
        if not mtf_df.empty and len(mtf_df) >= 2:
            mtf_last = mtf_df.iloc[-1]
            mtf_macd = safe_float(mtf_last.get('MACD'), 0)
            mtf_macd_s = safe_float(mtf_last.get('MACD_Signal'), 0)
            mtf_rsi = safe_float(mtf_last.get('RSI'), 50)
            if mtf_macd > mtf_macd_s:
                analysis["mtf_5m"] = f"YUKARI (RSI:{mtf_rsi:.0f})"
            else:
                analysis["mtf_5m"] = f"AŞAĞI (RSI:{mtf_rsi:.0f})"
        else:
            analysis["mtf_5m"] = "Veri yok"
    except Exception:
        analysis["mtf_5m"] = "Hata"

    return trend, sig_type, sl, tp1, tp2, confidence, analysis


# ─────────────────────────────────────────
# PAYLOAD BUILDER
# ─────────────────────────────────────────
def build_response_payload(interval='1min'):
    try:
        config = INTERVAL_CONFIG[interval]
        gold_df_raw, dxy_df, dxy_sym = fetch_market_data(interval)

        if gold_df_raw.empty:
            return None

        gold_df = calculate_indicators(gold_df_raw.copy())
        last = gold_df.iloc[-1]
        n = config['candle_count']
        tail_df = gold_df.tail(n)

        rsi_val  = safe_float(last.get('RSI'), 50.0, 2)
        macd_v   = safe_float(last.get('MACD'), 0.0, 4)
        macd_s   = safe_float(last.get('MACD_Signal'), 0.0, 4)
        macd_h   = safe_float(last.get('MACD_Hist'), 0.0, 4)
        bb_u     = safe_float(last.get('BB_Up'), 0.0, 2)
        bb_m     = safe_float(last.get('BB_Mid'), 0.0, 2)
        bb_l     = safe_float(last.get('BB_Low'), 0.0, 2)
        atr_val  = safe_float(last.get('ATR'), 2.0, 2)
        vwap_val = safe_float(last.get('VWAP'), 0.0, 2)

        dxy_price = safe_float(dxy_df['close'].iloc[-1], 0.0, 4) if not dxy_df.empty else 0.0
        current_price = safe_float(last.get('close'), 0.0, 2)
        current_vol = safe_float(last.get('volume'), 0.0, 2)
        if current_vol == 0:
            current_vol = safe_float(
                (safe_float(last.get('high'), 0.0) - safe_float(last.get('low'), 0.0)) * 1000,
                0.0, 2
            )

        ma20_val = safe_float(last.get('MA20'), 0.0, 2)
        ma50_val = safe_float(last.get('MA50'), 0.0, 2)
        mas = {
            'ma20': ma20_val if ma20_val > 0 else None,
            'ma50': ma50_val if ma50_val > 0 else None
        }

        # ═══════════════════════════════════════
        # SİNYAL MOTORU
        # ═══════════════════════════════════════
        kill_switch_status = check_kill_switch()
        trend = "nötr"
        sig_type = "YÖNSÜZ / BEKLE ⚪"
        sl = tp1 = tp2 = 0
        confidence = ""
        analysis = {}
        quality_score = 0
        quality_reasons = []
        global _active_positions

        if kill_switch_status.get('active', False):
            # Close ALL active positions
            for pos in _active_positions:
                lot_sz = pos.get('lot', ACCOUNT_CONFIG['min_lot'])
                pnl_close = 0
                if pos['trend'] == 'bullish':
                    pnl_close = (current_price - pos['entry']) * lot_sz * ACCOUNT_CONFIG['contract_size']
                else:
                    pnl_close = (pos['entry'] - current_price) * lot_sz * ACCOUNT_CONFIG['contract_size']
                send_telegram_close("KILL SWITCH", pos['entry'], current_price, pnl_close)
                _record_trade(pos, current_price, "KILL_SWITCH", pnl_close)
            _active_positions = []
            trend = "kilitli"
            sig_type = kill_switch_status.get('message', 'KİLİTLİ')

        elif _active_positions:
            # ── HAFTA SONU KONTROLÜ — Piyasa kapalıysa pozisyonları dondur ──
            _utc_now = datetime.now(timezone.utc)
            _wd = _utc_now.weekday()
            _hr = _utc_now.hour
            _market_closed = (_wd == 5 or _wd == 6 or (_wd == 4 and _hr >= 22))
            if _market_closed:
                sig_type = "📅 PİYASA KAPALI — Açık pozisyonlar donduruldu"
                trend = _active_positions[0]['trend'] if _active_positions else 'nötr'
                # Pozisyonlara dokunma, fiyat kontrolü yapma
            else:
              # ── AKTİF POZİSYONLAR KONTROLÜ — iterate in reverse to safely remove ──
              closed_any = False

              for idx in range(len(_active_positions) - 1, -1, -1):
                pos = _active_positions[idx]
                pos_closed = False

                if pos['trend'] == 'bullish':
                    lot_sz = pos.get('lot', ACCOUNT_CONFIG['min_lot'])
                    if current_price <= pos['sl']:
                        sig_type = "🛑 STOP-LOSS VURULDU — Pozisyon Kapatıldı"
                        pnl_close = (current_price - pos['entry']) * lot_sz * ACCOUNT_CONFIG['contract_size']
                        send_telegram_close("STOP-LOSS", pos['entry'], current_price, pnl_close)
                        _record_trade(pos, current_price, "STOP-LOSS", pnl_close)
                        _active_positions.pop(idx)
                        pos_closed = True
                        closed_any = True
                    elif current_price >= pos['tp2']:
                        sig_type = "🏆 TP2 VURULDU — Maksimum Kâr Alındı!"
                        pnl_close = (current_price - pos['entry']) * lot_sz * ACCOUNT_CONFIG['contract_size']
                        send_telegram_close("TP2 VURULDU", pos['entry'], current_price, pnl_close)
                        _record_trade(pos, current_price, "TP2", pnl_close)
                        _active_positions.pop(idx)
                        pos_closed = True
                        closed_any = True
                    elif current_price >= pos['tp1'] and not pos['tp1_hit']:
                        _active_positions[idx]['tp1_hit'] = True
                        # v3.8: Trail lock %30 — TP1 mesafesinin %30'unu kilitle
                        tp1_dist = pos['tp1'] - pos['entry']
                        trail_lock_sl = pos['entry'] + tp1_dist * 0.30
                        _active_positions[idx]['sl'] = round(trail_lock_sl, 2)
                    else:
                        # v3.2 — AKILLI ERKEN ÇIKIŞ (LONG)
                        early_exit = False
                        if not pos.get('tp1_hit', False):
                            time_in_trade = int(time.time()) - pos.get('open_time', int(time.time()))
                            unrealized_loss = pos['entry'] - current_price
                            if time_in_trade >= 600 and unrealized_loss >= 0.5 * atr_val:
                                if len(gold_df) >= 3 and 'MACD_Hist' in gold_df.columns:
                                    h1 = safe_float(gold_df['MACD_Hist'].iloc[-1], 0)
                                    h2 = safe_float(gold_df['MACD_Hist'].iloc[-2], 0)
                                    h3 = safe_float(gold_df['MACD_Hist'].iloc[-3], 0)
                                    if h1 < 0 and h2 < 0 and h3 < 0:
                                        early_exit = True
                        if early_exit:
                            lot_sz = pos.get('lot', ACCOUNT_CONFIG['min_lot'])
                            pnl_close = (current_price - pos['entry']) * lot_sz * ACCOUNT_CONFIG['contract_size']
                            sig_type = "⚡ ERKEN ÇIKIŞ — Momentum tersine döndü"
                            send_telegram_close("ERKEN ÇIKIŞ", pos['entry'], current_price, pnl_close)
                            _record_trade(pos, current_price, "EARLY_EXIT", pnl_close)
                            _active_positions.pop(idx)
                            pos_closed = True
                            closed_any = True
                            print(f"   ⚡ EARLY EXIT LONG: {time_in_trade}sn, loss=${unrealized_loss:.2f}, PnL=${pnl_close:.2f}")

                elif pos['trend'] == 'bearish':
                    lot_sz = pos.get('lot', ACCOUNT_CONFIG['min_lot'])
                    if current_price >= pos['sl']:
                        sig_type = "🛑 STOP-LOSS VURULDU — Pozisyon Kapatıldı"
                        pnl_close = (pos['entry'] - current_price) * lot_sz * ACCOUNT_CONFIG['contract_size']
                        send_telegram_close("STOP-LOSS", pos['entry'], current_price, pnl_close)
                        _record_trade(pos, current_price, "STOP-LOSS", pnl_close)
                        _active_positions.pop(idx)
                        pos_closed = True
                        closed_any = True
                    elif current_price <= pos['tp2']:
                        sig_type = "🏆 TP2 VURULDU — Maksimum Kâr Alındı!"
                        pnl_close = (pos['entry'] - current_price) * lot_sz * ACCOUNT_CONFIG['contract_size']
                        send_telegram_close("TP2 VURULDU", pos['entry'], current_price, pnl_close)
                        _record_trade(pos, current_price, "TP2", pnl_close)
                        _active_positions.pop(idx)
                        pos_closed = True
                        closed_any = True
                    elif current_price <= pos['tp1'] and not pos['tp1_hit']:
                        _active_positions[idx]['tp1_hit'] = True
                        # v3.8: Trail lock %30 — TP1 mesafesinin %30'unu kilitle
                        tp1_dist = pos['entry'] - pos['tp1']
                        trail_lock_sl = pos['entry'] - tp1_dist * 0.30
                        _active_positions[idx]['sl'] = round(trail_lock_sl, 2)
                    else:
                        # v3.2 — AKILLI ERKEN ÇIKIŞ (SHORT)
                        early_exit = False
                        if not pos.get('tp1_hit', False):
                            time_in_trade = int(time.time()) - pos.get('open_time', int(time.time()))
                            unrealized_loss = current_price - pos['entry']
                            if time_in_trade >= 600 and unrealized_loss >= 0.5 * atr_val:
                                if len(gold_df) >= 3 and 'MACD_Hist' in gold_df.columns:
                                    h1 = safe_float(gold_df['MACD_Hist'].iloc[-1], 0)
                                    h2 = safe_float(gold_df['MACD_Hist'].iloc[-2], 0)
                                    h3 = safe_float(gold_df['MACD_Hist'].iloc[-3], 0)
                                    if h1 > 0 and h2 > 0 and h3 > 0:
                                        early_exit = True
                        if early_exit:
                            lot_sz = pos.get('lot', ACCOUNT_CONFIG['min_lot'])
                            pnl_close = (pos['entry'] - current_price) * lot_sz * ACCOUNT_CONFIG['contract_size']
                            sig_type = "⚡ ERKEN ÇIKIŞ — Momentum tersine döndü"
                            send_telegram_close("ERKEN ÇIKIŞ", pos['entry'], current_price, pnl_close)
                            _record_trade(pos, current_price, "EARLY_EXIT", pnl_close)
                            _active_positions.pop(idx)
                            pos_closed = True
                            closed_any = True
                            print(f"   ⚡ EARLY EXIT SHORT: {time_in_trade}sn, loss=${unrealized_loss:.2f}, PnL=${pnl_close:.2f}")

              # If all positions closed in this loop, generate new signal in next block
              if closed_any and not _active_positions:
                  # Kapandıysa yeni sinyal üret
                  trend, sig_type, sl, tp1, tp2, confidence, analysis = generate_composite_signal(
                      gold_df, mas, current_price, atr_val,
                      macd_v, macd_s, macd_h, rsi_val, vwap_val
                  )

        else:
            # ── YENİ SİNYAL ÜRET — v5.7 PATTERN-BASED ──
            # Only generate new signal if we have room for more positions
            if len(_active_positions) < MAX_SIMULTANEOUS:
                # Try pattern detection first (v5.7)
                pattern_signal = generate_pattern_signal(current_price, atr_val, ACCOUNT_CONFIG['balance'])

                # Fallback to composite signal if no pattern found
                if pattern_signal:
                    trend = pattern_signal.get('direction', 'nötr')
                    if trend == 'LONG':
                        trend = 'bullish'
                    elif trend == 'SHORT':
                        trend = 'bearish'

                    # Check: don't open 2 positions with same trend
                    same_trend_exists = any(p['trend'] == trend for p in _active_positions)
                    if same_trend_exists:
                        trend = 'nötr'
                        sig_type = "SINYAL: Aynı yöne zaten açık pozisyon var"
                        pattern_signal = None
                    else:
                        sl = pattern_signal.get('sl', 0)
                        tp1 = pattern_signal.get('tp1', 0)
                        tp2 = pattern_signal.get('tp2', 0)
                        lot = pattern_signal.get('lot', ACCOUNT_CONFIG['min_lot'])
                        pattern_name = pattern_signal.get('pattern', 'UNKNOWN')
                        confidence = pattern_signal.get('confidence', 0)

                        sig_type = f"🎯 {pattern_name} — {trend.upper()} (Conf: {confidence}%)"
                        analysis = {
                            'htf': 'Pattern detected',
                            'structure': f'Pattern: {pattern_name}',
                            'squeeze': 'Pattern analysis',
                            'macd': 'N/A',
                            'vwap': 'N/A',
                            'confidence': f'{confidence}%',
                            'pattern': pattern_name,
                            'pattern_height': pattern_signal.get('pattern_height', 0),
                        }
                        quality_score = min(9, confidence // 10) if confidence > 0 else 0
                        quality_reasons = [pattern_name]

                        should_open = True  # v5.7: Pattern signals open directly

                        # v3.12: Günlük güvenlik kontrolü
                        if should_open:
                            can_trade, safety_msg = _can_open_trade()
                            if not can_trade:
                                should_open = False
                                print(f"   {safety_msg}")
                                analysis['daily_safety'] = safety_msg

                        if should_open:
                            print(f"   ✅ POZİSYON AÇILIYOR — {pattern_name} {trend.upper()} @ ${current_price:.2f}, Lot: {lot}")
                            _active_positions.append({
                                'trend': trend, 'signal': sig_type,
                                'entry': current_price, 'sl': round(sl, 2),
                                'tp1': round(tp1, 2), 'tp2': round(tp2, 2), 'tp1_hit': False,
                                'open_time': int(time.time()),
                                'lot': lot,
                                'remaining_lot': lot,
                                'partial_done': False,
                                'trailing_sl': sl,
                                'pattern': pattern_name,
                                'dynamic_tp_dollars': pattern_signal.get('dynamic_tp_dollars', TRADE_MGMT['tp_dollars']),
                            })
                            # Telegram'a sinyal gönder
                            _temp_risk = calculate_risk_metrics(current_price, sl, tp1, tp2, trend)
                            _temp_risk['lot_size'] = lot  # Use pattern lot
                            send_telegram_signal(
                                trend, current_price, round(sl, 2), round(tp1, 2), round(tp2, 2),
                                f"{confidence}%", quality_score, quality_reasons, _temp_risk, analysis
                            )
            else:
                # Fallback: Use composite signal
                trend, sig_type, sl, tp1, tp2, confidence, analysis = generate_composite_signal(
                    gold_df, mas, current_price, atr_val,
                    macd_v, macd_s, macd_h, rsi_val, vwap_val
                )

                # Check: don't open 2 positions with same trend
                same_trend_exists = any(p['trend'] == trend for p in _active_positions)

                # Kalite filtresi ile pozisyon aç
                should_open = False
                quality_score = 0
                quality_reasons = []

                # Debug loglama
                print(f"📊 Sinyal: trend={trend}, confidence={confidence}, price=${current_price:.2f}")

                if same_trend_exists:
                    print(f"   ⚠️ Aynı yöne ({trend}) zaten açık pozisyon var — sinyal açılmadı")
                elif confidence == "GÜÇLÜ" and trend in ("bullish", "bearish"):
                    quality_score, quality_reasons = calculate_signal_quality(
                        gold_df, current_price, trend, atr_val, rsi_val, vwap_val, bb_m, 50
                    )
                    print(f"   GÜÇLÜ sinyal kalite: {quality_score}/9 (min: {MIN_QUALITY_GUCLU}) -> {'AÇILACAK' if quality_score >= MIN_QUALITY_GUCLU else 'FİLTRELENDİ'}")
                    if quality_score >= MIN_QUALITY_GUCLU:
                        should_open = True

                elif confidence == "ORTA" and trend in ("bullish", "bearish"):
                    quality_score, quality_reasons = calculate_signal_quality(
                        gold_df, current_price, trend, atr_val, rsi_val, vwap_val, bb_m, 50
                    )
                    print(f"   ORTA sinyal kalite: {quality_score}/9 (min: {MIN_QUALITY_ORTA}) -> {'AÇILACAK' if quality_score >= MIN_QUALITY_ORTA else 'FİLTRELENDİ'}")
                    if quality_score >= MIN_QUALITY_ORTA:
                        should_open = True

                elif confidence == "ZAYIF" and trend in ("bullish", "bearish"):
                    # SİMÜLASYON: Zayıf sinyallerde minimum lotla aç (performans testi için)
                    quality_score, quality_reasons = calculate_signal_quality(
                        gold_df, current_price, trend, atr_val, rsi_val, vwap_val, bb_m, 50
                    )
                    print(f"   ZAYIF sinyal kalite: {quality_score}/9 → SİMÜLASYON TRADE (min lot)")
                    should_open = True
                    quality_reasons.append('SİMÜLASYON-ZAYIF')
                elif confidence == "YOK" and trend == "nötr":
                    # ── BASİT SİNYAL FALLBACK — RSI + MACD + VWAP ile trade aç ──
                    simple_bull = 0
                    simple_bear = 0
                    # MACD histogram yönü
                    if macd_h > 0: simple_bull += 1
                    elif macd_h < 0: simple_bear += 1
                    # RSI momentum
                    if rsi_val > 55: simple_bull += 1
                    elif rsi_val < 45: simple_bear += 1
                    # VWAP pozisyonu
                    if vwap_val > 0:
                        if current_price > vwap_val: simple_bull += 1
                        elif current_price < vwap_val: simple_bear += 1
                    # MA20 yönü
                    if ma20_val > 0:
                        if current_price > ma20_val: simple_bull += 1
                        elif current_price < ma20_val: simple_bear += 1

                    if simple_bull >= 3 and simple_bear == 0 and rsi_val < 65:
                        trend = "bullish"
                        confidence = "BASİT"
                        sig_type = f"📈 BASİT LONG — RSI+MACD+VWAP ({simple_bull}/4) 🟢"
                        sl = current_price - (1.5 * atr_val)
                        tp1 = current_price + (2.0 * atr_val)
                        tp2 = current_price + (3.0 * atr_val)
                        should_open = True
                        quality_score = simple_bull
                        quality_reasons = ['BASİT-LONG', f'RSI:{rsi_val:.0f}', f'MACD_H:{macd_h:.4f}']
                        print(f"   📈 BASİT LONG sinyal: bull={simple_bull}, bear={simple_bear}")
                    elif simple_bear >= 3 and simple_bull == 0 and rsi_val > 35:
                        trend = "bearish"
                        confidence = "BASİT"
                        sig_type = f"📉 BASİT SHORT — RSI+MACD+VWAP ({simple_bear}/4) 🔴"
                        sl = current_price + (1.5 * atr_val)
                        tp1 = current_price - (2.0 * atr_val)
                        tp2 = current_price - (3.0 * atr_val)
                        should_open = True
                        quality_score = simple_bear
                        quality_reasons = ['BASİT-SHORT', f'RSI:{rsi_val:.0f}', f'MACD_H:{macd_h:.4f}']
                        print(f"   📉 BASİT SHORT sinyal: bull={simple_bull}, bear={simple_bear}")
                    else:
                        print(f"   Sinyal açılmadı: BASİT filtre geçemedi (bull={simple_bull}, bear={simple_bear})")
                else:
                    print(f"   Sinyal açılmadı: confidence={confidence}, trend={trend}")

                # MACD Histogram yön onayı — GÜÇLÜ/ORTA sinyallerde zorunlu, ZAYIF/BASİT'te skip
                if should_open and confidence not in ("ZAYIF", "BASİT"):
                    if trend == 'bullish' and macd_h <= 0:
                        should_open = False
                        print(f"   ❌ MACD onay başarısız: LONG sinyal ama MACD_H={macd_h:.4f} (negatif)")
                    elif trend == 'bearish' and macd_h >= 0:
                        should_open = False
                        print(f"   ❌ MACD onay başarısız: SHORT sinyal ama MACD_H={macd_h:.4f} (pozitif)")

                # v3.7 — MUM ONAY KALDIRILDI (v3.4'te 47→10 işleme düşürdü, zararlı)
                # Candle confirmation devre dışı bırakıldı

                # v3.12: Günlük güvenlik kontrolü
                if should_open:
                    can_trade, safety_msg = _can_open_trade()
                    if not can_trade:
                        should_open = False
                        print(f"   {safety_msg}")
                        # Güvenlik durumunu frontend'e bildir
                        analysis['daily_safety'] = safety_msg

                if should_open:
                    _pattern_label = 'BASİT' if confidence == 'BASİT' else 'COMPOSITE'
                    print(f"   ✅ POZİSYON AÇILIYOR — {_pattern_label} {trend} @ ${current_price:.2f}")
                    _active_positions.append({
                        'trend': trend, 'signal': sig_type,
                        'entry': current_price, 'sl': round(sl, 2),
                        'tp1': round(tp1, 2), 'tp2': round(tp2, 2), 'tp1_hit': False,
                        'open_time': int(time.time()),
                        'lot': ACCOUNT_CONFIG['min_lot'],
                        'remaining_lot': ACCOUNT_CONFIG['min_lot'],
                        'partial_done': False,
                        'trailing_sl': sl,
                        'pattern': _pattern_label,
                        'dynamic_tp_dollars': TRADE_MGMT['tp_dollars'],
                    })
                    # Telegram'a sinyal gönder
                    _temp_risk = calculate_risk_metrics(current_price, sl, tp1, tp2, trend)
                    send_telegram_signal(
                        trend, current_price, round(sl, 2), round(tp1, 2), round(tp2, 2),
                        confidence, quality_score, quality_reasons, _temp_risk, analysis
                    )

        # ═══ RİSK METRİKLERİ HESAPLA ═══
        risk_metrics = calculate_risk_metrics(current_price, sl, tp1, tp2, trend)

        # Kalite skoru hesapla (gösterim için)
        if trend in ("bullish", "bearish") and not quality_score:
            quality_score, quality_reasons = calculate_signal_quality(
                gold_df, current_price, trend, atr_val, rsi_val, vwap_val, bb_m, 50
            )

        trading_signals = {
            "trend": trend, "signal": sig_type,
            "entry": current_price,
            "sl": round(sl, 2), "tp1": round(tp1, 2), "tp2": round(tp2, 2),
            "atr": atr_val, "analysis": analysis,
            "risk": risk_metrics,
            "quality_score": quality_score,
            "quality_reasons": quality_reasons,
            # v5.7 Pattern fields - reference first position if exists
            "pattern": _active_positions[0].get('pattern', '') if _active_positions else '',
            "pattern_confidence": analysis.get('confidence', 0),
            "lot_size": _active_positions[0].get('lot', ACCOUNT_CONFIG['min_lot']) if _active_positions else ACCOUNT_CONFIG['min_lot'],
            "trailing_active": any(p.get('trailing_sl', 0) != 0 for p in _active_positions),
            "partial_done": any(p.get('partial_done', False) for p in _active_positions),
        }

        # Grafik verilerini hazırla
        candles, rsi_chart, bb_upper, bb_middle, bb_lower = [], [], [], [], []

        for _, r in tail_df.iterrows():
            try:
                ts = int(r['datetime'].timestamp() * 1000)
                candles.append({
                    'x': ts,
                    'y': [
                        safe_float(r['open']), safe_float(r['high']),
                        safe_float(r['low']), safe_float(r['close'])
                    ]
                })
                rsi_val_loop = safe_float(r.get('RSI'))
                if rsi_val_loop != 0.0:
                    rsi_chart.append({'x': ts, 'y': rsi_val_loop})
                bb_upper.append({'x': ts, 'y': safe_float(r.get('BB_Up'))})
                bb_middle.append({'x': ts, 'y': safe_float(r.get('BB_Mid'))})
                bb_lower.append({'x': ts, 'y': safe_float(r.get('BB_Low'))})
            except Exception:
                continue

        bb_chart = {"upper": bb_upper, "middle": bb_middle, "lower": bb_lower}

        try:
            history_len = get_recent_history(count_only=True)
            if not isinstance(history_len, int):
                history_len = len(history_len)
        except Exception:
            history_len = 0

        return {
            "interval": interval, "interval_label": config['label'],
            "gold_price": current_price,
            "gold_open": safe_float(last.get('open')),
            "gold_high": safe_float(last.get('high')),
            "gold_low": safe_float(last.get('low')),
            "dxy_price": dxy_price, "gold_rsi": rsi_val,
            "rsi_signal": rsi_signal(rsi_val),
            "vwap": vwap_val, "volume": current_vol,
            "kill_switch": kill_switch_status,
            "macd": {
                "macd": macd_v, "signal": macd_s, "histogram": macd_h,
                "trend": "bullish" if macd_v > macd_s else "bearish"
            },
            "bollinger": {
                "upper": bb_u, "middle": bb_m, "lower": bb_l,
                "width": safe_float(bb_u - bb_l)
            },
            "moving_averages": mas,
            "market_note": get_market_note(dxy_df, dxy_sym),
            "trading_signals": trading_signals,
            "candles": candles, "rsi_series": rsi_chart, "bb_chart": bb_chart,
            "news_data": get_cached_news(),
            "upcoming_events": get_upcoming_events(),
            "geopolitics": get_cached_geopolitics(),
            "history_count": history_len,
            "last_updated": int(time.time() * 1000),
            "active_positions": [
                {"trend": p['trend'], "entry": p['entry'], "sl": p['sl'],
                 "tp1": p['tp1'], "tp2": p['tp2'],
                 "pattern": p.get('pattern', ''), "lot": p.get('lot', 0.01),
                 "open_time": p.get('open_time', 0)}
                for p in _active_positions
            ],
            "daily_safety": {
                "trades_today": _daily_state['trades_today'],
                "max_trades": DAILY_SAFETY['max_trades_per_day'],
                "pnl_today": round(_daily_state['pnl_today'], 2),
                "consecutive_losses": _daily_state['consecutive_losses'],
                "can_trade": _can_open_trade()[0],
                "paused": _daily_state['paused_until'] > time.time()
            }
        }
    except Exception as e:
        print(f"Payload Build Hatası: {e}")
        traceback.print_exc()
        return None

# ─────────────────────────────────────────
# ARKA PLAN TARAYICI (60sn döngü)
# ─────────────────────────────────────────
def background_scanner():
    time.sleep(5)
    while True:
        t0 = time.time()
        try:
            with _cache_lock:
                _gold_cache.pop('1min', None)
            gold_df_raw, dxy_df, _ = fetch_market_data('1min')
            if not gold_df_raw.empty:
                gold_df = calculate_indicators(gold_df_raw.copy())
                price = safe_float(gold_df['close'].iloc[-1])
                rsi = safe_float(gold_df['RSI'].iloc[-1], 50.0)
                dxy = safe_float(dxy_df['close'].iloc[-1], 0.0, 4) if not dxy_df.empty else 0.0
                save_market_data(price, rsi, dxy)
                payload = build_response_payload('1min')
                if payload:
                    socketio.emit('market_update', payload)
                    # Sinyal durumu log
                    ts = payload.get('trading_signals', {})
                    ap = len(_active_positions)
                    print(f"   📊 Sinyal: {ts.get('trend','?')} | Conf: {ts.get('pattern','?')} | Aktif Poz: {ap}/3 | RSI: {rsi:.1f}")
            else:
                print(f"   ⚠️ Altın verisi boş geldi — API hatası olabilir")
            elapsed = time.time() - t0
            print(f"⚡ Scan tamamlandı: {elapsed:.1f}sn")
        except Exception as e:
            print(f"Scanner Hatası: {e}")
        time.sleep(30)  # 60sn → 30sn daha hızlı güncelleme

threading.Thread(target=background_scanner, daemon=True).start()

# ─────────────────────────────────────────
# OLAY ÖNCESİ TELEGRAM UYARI SİSTEMİ
# ─────────────────────────────────────────
_event_alerts_sent = set()  # Gönderilmiş uyarı ID'leri (tekrar göndermeyi önle)

# Ekonomik olay → altın yön tahmini veritabanı (geçmiş verilere dayalı)
EVENT_GOLD_PREDICTIONS = {
    # FED & Faiz
    'Federal Funds Rate': {'direction': 'down', 'confidence': 85, 'reason': 'Faiz artışı doları güçlendirir, altın düşer. Faiz sabit kalırsa altın yükselir.'},
    'FOMC': {'direction': 'volatile', 'confidence': 90, 'reason': 'FOMC kararları altında sert hareketlere neden olur. Şahin → düşüş, güvercin → yükseliş.'},
    'Fed Chair': {'direction': 'volatile', 'confidence': 85, 'reason': 'Fed başkanı konuşmaları piyasada sert hareketlere yol açar.'},
    'Powell': {'direction': 'volatile', 'confidence': 85, 'reason': 'Powell konuşmaları faiz beklentilerini değiştirir, altın sert hareket eder.'},

    # Enflasyon
    'CPI': {'direction': 'mixed', 'confidence': 80, 'reason': 'Yüksek CPI → faiz artış beklentisi → altın düşer. Düşük CPI → altın yükselir.'},
    'Core CPI': {'direction': 'mixed', 'confidence': 80, 'reason': 'Çekirdek CPI beklenenden yüksekse altın düşer, düşükse yükselir.'},
    'PPI': {'direction': 'mixed', 'confidence': 70, 'reason': 'Üretici fiyatları enflasyon habercisidir. Yüksek PPI altını baskılar.'},
    'PCE': {'direction': 'mixed', 'confidence': 80, 'reason': 'Fed\'in tercih ettiği enflasyon göstergesi. Yüksek → altın düşer.'},

    # İstihdam
    'Non-Farm': {'direction': 'down', 'confidence': 85, 'reason': 'Güçlü istihdam → dolar güçlenir → altın düşer. Zayıf istihdam → altın yükselir.'},
    'Nonfarm': {'direction': 'down', 'confidence': 85, 'reason': 'NFP güçlü gelirse altın düşer, zayıf gelirse yükselir.'},
    'Unemployment': {'direction': 'up', 'confidence': 75, 'reason': 'Yüksek işsizlik → ekonomi zayıf → güvenli liman altın yükselir.'},
    'Initial Jobless': {'direction': 'up', 'confidence': 65, 'reason': 'Yüksek başvuru sayısı ekonomik zayıflık sinyali → altın yükselir.'},
    'ADP': {'direction': 'down', 'confidence': 70, 'reason': 'Güçlü ADP istihdam → NFP güçlü gelir beklentisi → altın baskılanır.'},

    # Büyüme
    'GDP': {'direction': 'down', 'confidence': 75, 'reason': 'Güçlü GDP → dolar güçlenir → altın düşer.'},
    'Retail Sales': {'direction': 'down', 'confidence': 70, 'reason': 'Güçlü perakende satış → ekonomi güçlü → altın düşer.'},
    'ISM Manufacturing': {'direction': 'down', 'confidence': 70, 'reason': 'Güçlü ISM → ekonomi güçlü → faiz yükselir → altın düşer.'},
    'ISM Services': {'direction': 'down', 'confidence': 65, 'reason': 'Güçlü hizmet sektörü → dolar güçlenir → altın baskılanır.'},

    # Siyasi
    'Trump': {'direction': 'volatile', 'confidence': 75, 'reason': 'Trump konuşmaları ticaret savaşı/tarife endişesi yaratır → altın genelde yükselir.'},
    'President': {'direction': 'volatile', 'confidence': 70, 'reason': 'Başkanlık açıklamaları politik belirsizlik yaratır → altın hareketlenir.'},
    'Treasury': {'direction': 'down', 'confidence': 65, 'reason': 'Hazine açıklamaları tahvil faizlerini etkiler → altın ters yönde hareket eder.'},
}

def get_event_prediction(title):
    """Ekonomik olay başlığına göre altın yön tahmini döndür"""
    title_upper = title.upper()
    for keyword, pred in EVENT_GOLD_PREDICTIONS.items():
        if keyword.upper() in title_upper:
            return pred
    return None

def check_and_send_event_alerts():
    """Yaklaşan olayları kontrol et, 30dk/15dk/5dk önce Telegram uyarısı gönder"""
    if not TELEGRAM_ENABLED:
        return

    try:
        events = get_upcoming_events()
        if not events:
            return

        now = datetime.now(timezone.utc)

        for ev in events:
            if ev.get('status') == 'GEÇTİ':
                continue

            # Time label'dan kalan süreyi tahmin et
            time_label = ev.get('time_label', '')
            title = ev.get('title', '')
            impact = ev.get('impact', '')
            event_id = f"{title}_{ev.get('time_label', '')}"

            # Tahmin al
            prediction = get_event_prediction(title)
            if not prediction:
                continue

            # Alert zamanlamaları: 30dk, 15dk, 5dk önce
            alert_windows = []
            if '30 dk' in time_label or '25 dk' in time_label:
                alert_windows.append('30dk')
            elif '15 dk' in time_label or '14 dk' in time_label or '13 dk' in time_label:
                alert_windows.append('15dk')
            elif '5 dk' in time_label or '4 dk' in time_label or '3 dk' in time_label:
                alert_windows.append('5dk')
            elif '1 dk' in time_label or '2 dk' in time_label:
                alert_windows.append('SON')

            for window in alert_windows:
                alert_key = f"{title}_{window}"
                if alert_key in _event_alerts_sent:
                    continue

                _event_alerts_sent.add(alert_key)

                # Yön emoji ve metin
                if prediction['direction'] == 'up':
                    dir_emoji = "📈"
                    dir_text = "YUKARI (Altın YÜKSELEBILIR)"
                elif prediction['direction'] == 'down':
                    dir_emoji = "📉"
                    dir_text = "AŞAĞI (Altın DÜŞEBİLİR)"
                elif prediction['direction'] == 'volatile':
                    dir_emoji = "⚡"
                    dir_text = "SERT HAREKET BEKLENİYOR"
                else:
                    dir_emoji = "⚖️"
                    dir_text = "VERİYE BAĞLI (Yüksek/düşük gelirse ters yön)"

                urgency = "🔴 ACIL" if window in ('5dk', 'SON') else "🟡 YAKLAŞIYOR" if window == '15dk' else "🔵 BİLGİ"

                etki_text = "KRITIK" if impact == 'High' else "ORTA"
                action_text = "POZISYON ALMA — Veri aciklanmasini bekle!" if window in ('5dk', 'SON') else "Dikkatli ol — sinyal kalitesini kontrol et."

                msg = (
                    f"{urgency} <b>OLAY UYARISI</b> {urgency}\n"
                    f"━━━━━━━━━━━━━━━━━━━\n\n"
                    f"<b>{title}</b>\n"
                    f"Kalan: ~{time_label}\n"
                    f"Etki: {etki_text}\n\n"
                    f"{dir_emoji} <b>Tahmin:</b> {dir_text}\n"
                    f"Guven: %{prediction['confidence']}\n\n"
                    f"<i>{prediction['reason']}</i>\n\n"
                    f"{action_text}\n"
                    f"━━━━━━━━━━━━━━━━━━━"
                )

                _send_telegram(msg)
                print(f"📱 Olay uyarısı gönderildi: {title} ({window})")

    except Exception as e:
        print(f"Event alert hatası: {e}")

# Olay uyarıcısı arka plan thread'i (her 60sn kontrol)
def event_alert_scanner():
    time.sleep(30)
    while True:
        try:
            check_and_send_event_alerts()
        except Exception as e:
            print(f"Event alert scanner hatası: {e}")
        time.sleep(60)

threading.Thread(target=event_alert_scanner, daemon=True).start()

# ─────────────────────────────────────────
# JEOPOLİTİK & KRİTİK OLAY TAKİP SİSTEMİ
# ─────────────────────────────────────────
_geopolitics_cache = {'data': None, 'ts': 0}
GEOPOLITICS_CACHE_TTL = 180  # 3 dakika cache

# Jeopolitik tehdit seviyeleri
THREAT_LEVELS = {
    'CRITICAL': {'min_score': 15, 'color': 'red', 'emoji': '🔴', 'label': 'KRİTİK — SAVAŞ/KRİZ'},
    'HIGH': {'min_score': 8, 'color': 'orange', 'emoji': '🟠', 'label': 'YÜKSEK — CİDDİ GERGİNLİK'},
    'MODERATE': {'min_score': 3, 'color': 'yellow', 'emoji': '🟡', 'label': 'ORTA — İZLE'},
    'LOW': {'min_score': 0, 'color': 'green', 'emoji': '🟢', 'label': 'DÜŞÜK — SAKİN PİYASA'},
}

def calculate_geopolitical_threat():
    """
    Tüm haber kaynaklarından jeopolitik tehdit seviyesi hesaplar.
    Haberleri kategorize eder ve frontend'e gönderir.
    """
    news_data = get_cached_news()
    events = get_upcoming_events()

    geo_articles = []
    total_geo_score = 0

    # Haberlerden jeopolitik olanları filtrele
    if news_data and news_data.get('articles'):
        for article in news_data['articles']:
            cat = article.get('category', '')
            if cat in ('Jeopolitik', 'Enerji', 'Ticaret'):
                geo_articles.append({
                    'title': article['title'],
                    'score': article['score'],
                    'category': cat,
                    'reason': article.get('reason', ''),
                    'impact': article.get('impact', 'BELİRSİZ'),
                    'time': article.get('time', ''),
                    'link': article.get('link', ''),
                    'severity': 'high' if abs(article['score']) >= 5 else 'medium' if abs(article['score']) >= 3 else 'low',
                })
                total_geo_score += abs(article['score'])

    # Tehdit seviyesi belirle
    threat_level = 'LOW'
    for level, info in THREAT_LEVELS.items():
        if total_geo_score >= info['min_score']:
            threat_level = level
            break

    threat_info = THREAT_LEVELS[threat_level]

    # Yaklaşan kritik olaylar (High impact economic)
    critical_events = []
    for ev in events:
        if ev.get('impact') == 'High':
            critical_events.append({
                'title': ev['title'],
                'time_label': ev['time_label'],
                'status': ev['status'],
                'urgency': ev['urgency'],
                'gold_direction': ev.get('gold_direction', 'BELİRSİZ'),
                'gold_reason': ev.get('gold_reason', ''),
            })

    # Bölge bazlı risk haritası
    region_risks = {}
    region_keywords = {
        'Ortadoğu': ['israel', 'iran', 'gaza', 'lebanon', 'syria', 'yemen', 'houthi', 'hezbollah', 'hamas', 'hormuz', 'red sea'],
        'Doğu Avrupa': ['russia', 'ukraine', 'nato'],
        'Doğu Asya': ['china', 'taiwan', 'north korea', 'kim jong'],
        'Küresel': ['nuclear', 'war', 'conflict', 'sanctions', 'tariff', 'trade war'],
    }

    for region, keywords in region_keywords.items():
        region_score = 0
        region_articles = []
        for article in geo_articles:
            title_lower = article['title'].lower()
            for kw in keywords:
                if kw in title_lower:
                    region_score += abs(article['score'])
                    region_articles.append(article['title'])
                    break
        if region_score > 0:
            region_risks[region] = {
                'score': region_score,
                'level': 'critical' if region_score >= 10 else 'high' if region_score >= 5 else 'moderate',
                'articles': region_articles[:3],
            }

    return {
        'threat_level': threat_level,
        'threat_label': threat_info['label'],
        'threat_emoji': threat_info['emoji'],
        'threat_color': threat_info['color'],
        'total_score': total_geo_score,
        'geo_articles': sorted(geo_articles, key=lambda x: abs(x['score']), reverse=True)[:10],
        'critical_events': critical_events[:5],
        'region_risks': region_risks,
        'article_count': len(geo_articles),
        'last_updated': int(time.time() * 1000),
    }

def get_cached_geopolitics():
    global _geopolitics_cache
    with _cache_lock:
        if _geopolitics_cache['data'] and (time.time() - _geopolitics_cache['ts']) < GEOPOLITICS_CACHE_TTL:
            return _geopolitics_cache['data']
    data = calculate_geopolitical_threat()
    with _cache_lock:
        _geopolitics_cache = {'data': data, 'ts': time.time()}
    return data

# ─────────────────────────────────────────
# API ENDPOINT'LERİ
# ─────────────────────────────────────────
@app.route('/api/telegram/setup', methods=['POST'])
def setup_telegram():
    """Telegram bot token ve chat ID ayarla"""
    global TELEGRAM_ENABLED, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    try:
        data = request.get_json()
        token = data.get('token', '').strip()
        chat_id = data.get('chat_id', '').strip()

        if not token or not chat_id:
            return jsonify({"error": "Token ve Chat ID gerekli"}), 400

        TELEGRAM_BOT_TOKEN = token
        TELEGRAM_CHAT_ID = chat_id
        TELEGRAM_ENABLED = True

        # Test mesajı gönder
        test_msg = "✅ <b>AurumPulse Telegram Baglantisi Basarili!</b>\n\nSinyaller bu sohbete gelecek."
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={
            'chat_id': chat_id,
            'text': test_msg,
            'parse_mode': 'HTML'
        }, timeout=10)

        if resp.status_code == 200:
            return jsonify({"success": True, "message": "Telegram bağlandı! Test mesajı gönderildi."})
        else:
            TELEGRAM_ENABLED = False
            return jsonify({"error": f"Telegram API hatası: {resp.text}"}), 400

    except Exception as e:
        TELEGRAM_ENABLED = False
        return jsonify({"error": str(e)}), 500


@app.route('/api/telegram/status')
def telegram_status():
    return jsonify({
        "enabled": TELEGRAM_ENABLED,
        "configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
    })


@app.route('/api/telegram/test_signal', methods=['POST'])
def test_telegram_signal():
    """Test amaçlı Telegram'a örnek sinyal gönder"""
    if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return jsonify({"error": "Telegram yapılandırılmamış"}), 400

    try:
        now_str = datetime.now().strftime("%H:%M:%S")
        test_msg = (
            f"🟢 <b>AURUMPULSE TEST SINYAL</b> 🟢\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>🟢 LONG (AL) — TEST</b>\n"
            f"Saat: {now_str}\n\n"
            f"Entry: <code>3050.00</code>\n"
            f"Stop Loss: <code>3047.50</code>\n"
            f"TP1: <code>3055.00</code> (+0.75)\n"
            f"TP2: <code>3058.75</code> (+1.31)\n\n"
            f"Kalite: 🟢🟢🟢🟢⚫⚫ (4/6)\n"
            f"Guven: GUCLU\n\n"
            f"Bu bir TEST sinyalidir.\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )

        ok = _send_telegram(test_msg)
        if ok:
            return jsonify({"success": True, "message": "Test sinyali Telegram'a gönderildi!"})
        else:
            return jsonify({"error": "Telegram mesaj gönderilemedi — konsol loglarını kontrol et"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/telegram/debug')
def telegram_debug():
    """Telegram ve sinyal durumu debug bilgisi"""
    return jsonify({
        "telegram_enabled": TELEGRAM_ENABLED,
        "token_set": bool(TELEGRAM_BOT_TOKEN),
        "chat_id_set": bool(TELEGRAM_CHAT_ID),
        "active_positions": _active_positions,
        "active_positions_count": len(_active_positions),
        "trade_history_count": len(_trade_history),
        "last_telegram_signal": _last_telegram_signal
    })


@app.route('/api/event_predictions')
def get_event_predictions():
    """Yaklaşan olayların altın yön tahminlerini döndür"""
    try:
        events = get_upcoming_events()
        predictions = []
        for ev in events:
            if ev.get('status') == 'GEÇTİ':
                continue
            pred = get_event_prediction(ev['title'])
            if pred:
                predictions.append({
                    'title': ev['title'],
                    'time_label': ev.get('time_label', ''),
                    'impact': ev['impact'],
                    'direction': pred['direction'],
                    'confidence': pred['confidence'],
                    'reason': pred['reason'],
                    'gold_direction': ev.get('gold_direction', 'BELİRSİZ')
                })
        return jsonify({"predictions": predictions})
    except Exception as e:
        return jsonify({"predictions": [], "error": str(e)})


@app.route('/api/geopolitics')
def api_geopolitics():
    """Jeopolitik tehdit seviyesi ve haberleri"""
    try:
        data = get_cached_geopolitics()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/trade_history')
def get_trade_history():
    """İşlem geçmişi ve performans istatistikleri"""
    trades = list(_trade_history)
    total = len(trades)
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    total_pnl = sum(t['pnl'] for t in trades)
    win_rate = (len(wins) / total * 100) if total > 0 else 0
    avg_win = (sum(t['pnl'] for t in wins) / len(wins)) if wins else 0
    avg_loss = (sum(t['pnl'] for t in losses) / len(losses)) if losses else 0

    # Equity curve
    equity = []
    running = 100.0  # $100 başlangıç
    for t in trades:
        running += t['pnl']
        equity.append({'x': t['close_time'] * 1000, 'y': round(running, 2)})

    # Günlük güvenlik durumu
    _check_daily_reset()
    can_trade, safety_msg = _can_open_trade()
    max_loss = ACCOUNT_CONFIG['balance'] * (DAILY_SAFETY['max_daily_loss_pct'] / 100)

    current_balance = round(100.0 + total_pnl, 2)

    # Frontend uyumluluğu: exit_price → exit alias
    out_trades = []
    for t in trades[-50:]:
        td = dict(t)
        if 'exit_price' in td and 'exit' not in td:
            td['exit'] = td['exit_price']
        out_trades.append(td)

    return jsonify({
        "trades": out_trades,  # Son 50 işlem
        "stats": {
            "total": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "best_trade": round(max((t['pnl'] for t in trades), default=0), 2),
            "worst_trade": round(min((t['pnl'] for t in trades), default=0), 2),
            "balance": current_balance,
            "starting_balance": 100.0,
        },
        "equity_curve": equity,
        "active_positions": [
            {
                "trend": p['trend'],
                "entry": p['entry'],
                "sl": p['sl'],
                "tp1": p['tp1'],
                "tp2": p['tp2'],
                "pattern": p.get('pattern', '')
            } for p in _active_positions
        ] if _active_positions else [],
        "daily_safety": {
            "trades_today": _daily_state['trades_today'],
            "max_trades": DAILY_SAFETY['max_trades_per_day'],
            "pnl_today": round(_daily_state['pnl_today'], 2),
            "max_loss": round(-max_loss, 2),
            "consecutive_losses": _daily_state['consecutive_losses'],
            "max_consecutive": DAILY_SAFETY['max_consecutive_losses'],
            "can_trade": can_trade,
            "status_msg": safety_msg if not can_trade else "✅ Trading aktif",
            "paused": _daily_state['paused_until'] > time.time()
        }
    })


@app.route('/api/market_data')
def get_market_data():
    try:
        interval = request.args.get('interval', '1min')
        valid_interval = get_validated_interval(interval)
        payload = build_response_payload(valid_interval)
        if payload is not None:
            return jsonify(payload)
        else:
            return jsonify({"error": "API limiti aşıldı, lütfen 1 dakika bekleyin."})
    except Exception as e:
        return jsonify({"error": f"Sunucu Hatası: {str(e)}"})

@app.route('/api/history')
def get_history():
    try:
        limit = min(int(request.args.get('limit', 100)), 1000)
        records = get_recent_history(limit=limit)
        safe_records = []
        for r in records:
            try:
                safe_records.append({
                    "id": r[0], "timestamp": r[1],
                    "price": safe_float(r[2]),
                    "rsi": safe_float(r[3]),
                    "dxy": safe_float(r[4], 0.0, 4)
                })
            except Exception:
                continue
        return jsonify({"records": safe_records, "count": len(safe_records)})
    except Exception as e:
        return jsonify({"error": f"Sunucu Hatası: {str(e)}"})

@socketio.on('connect')
def on_connect():
    pass

@socketio.on('disconnect')
def on_disconnect():
    pass

@socketio.on('subscribe_interval')
def on_subscribe(data):
    pass

if __name__ == '__main__':
    print("🚀 AurumPulse Backend başlatılıyor...")
    print("📊 Birleşik Sinyal Motoru: MTF + Price Action + Bollinger Squeeze")
    print("💡 WebSocket 500 alıyorsan: pip install simple-websocket")
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Port: {port}")
    socketio.run(app, debug=False, host='0.0.0.0', port=port,
                 use_reloader=False, log_output=True,
                 allow_unsafe_werkzeug=True)
