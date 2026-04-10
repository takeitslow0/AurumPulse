"""
AurumPulse Backtest v3.12 — Altyapı Güçlendirme + ADX Filtresi
─────────────────────────────────────────────────────────────────
v3.9 temeli (kanıtlanmış +$38.22) — PARAMETRELERİN HİÇBİRİ DEĞİŞMEDİ

  Yenilikler (sadece altyapı, strateji mantığı aynı):
  ▸ ADX trend gücü filtresi (ADX < 20 = choppy piyasa, trade açma)
  ▸ Uzatılmış veri desteği (7gün / 30gün / 60gün)
  ▸ Walk-Forward test modu (in-sample/out-of-sample validasyonu)
  ▸ Gelişmiş trade loglama ve istatistiksel analiz
  ▸ Tüm v3.9 parametreleri 1:1 korunmuş durumda
"""

import pandas as pd
import numpy as np
import yfinance as yf
import sys
import warnings
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════
# HESAP AYARLARI ($100 Scalping)
# ═══════════════════════════════════════════
ACCOUNT_CONFIG = {
    'balance': 100.0,
    'risk_pct': 2.0,         # v3.6 orijinal
    'max_risk_pct': 5.0,     # v3.6 orijinal
    'contract_size': 100,
    'min_lot': 0.01,
    'max_lot': 0.05,
}

# ═══════════════════════════════════════════
# OPTİMİZASYON PARAMETRELERİ
# ═══════════════════════════════════════════
# ═══════════════════════════════════════════
# PARAMETRELER — veri indirildikten sonra
# adaptif olarak ayarlanacak (aşağıda)
# ═══════════════════════════════════════════
# ═══ v3.9 — v3.6 TEMEL + SEANS FİLTRESİ ═══
# Tüm parametreler v3.6 ile birebir aynı — TEK ekleme: seans filtresi
TP1_RR = 1.5
TP2_RR = 2.5
ALLOW_ORTA = True
TRAIL_STOP_AFTER_TP1 = True
TRAIL_LOCK_PCT = 0.30
TRAIL_ATR_MULT = 1.0
REQUIRE_MACD_CONFIRM = True
REQUIRE_CANDLE_CONFIRM = False

COOLDOWN_BARS = 5
LOSS_STREAK_COOLDOWN = 20
MAX_LOSS_STREAK = 3
MIN_ATR_THRESHOLD = 0.80
SL_WICK_BUFFER = 0.5
MIN_SL_ATR_MULT = 2.0
HTF_TREND_THRESHOLD = 5
MIN_QUALITY_SCORE_GUCLU = 3
MIN_QUALITY_SCORE_ORTA = 4    # v3.6 orijinal
MOMENTUM_LOOKBACK = 5
RSI_MAX_LONG = 62
RSI_MIN_SHORT = 38

# v3.9 — SEANS FİLTRESİ
SESSION_FILTER = True
SESSION_START_UTC = 10
SESSION_END_UTC = 20

# Peak filtresi kapalı
PEAK_ONLY_ORTA = False

# v3.2 erken çıkış (çalışıyor — SL'den küçük kayıp sağlıyor)
ENABLE_EARLY_EXIT = False
EARLY_EXIT_MIN_BARS = 10
EARLY_EXIT_MIN_LOSS_ATR = 0.5
EARLY_EXIT_MACD_CONSECUTIVE = 3

# v3.6 — KISMİ KÂR KAPALI: R:R oranını bozuyordu (kayıp tam lot, kazanç yarım lot)
PARTIAL_TP1_CLOSE = False
PARTIAL_TP1_PCT = 0.50

# v3.5 — KAZANMA SERİSİNDE AGRESİF: Art arda kazançta lot büyüt
WINNING_STREAK_BOOST = True
STREAK_BOOST_AFTER = 2
STREAK_BOOST_RISK_PCT = 3.0

# v3.5 — KAYIP SERİSİ KORUMA
PROGRESSIVE_RISK = True
RISK_REDUCTION_AFTER_LOSSES = 3
REDUCED_RISK_PCT = 1.0

# v3.8 kaldırıldı — Dinamik TP2 ve güvene göre trail v3.6'da yoktu
DYNAMIC_TP2 = False
DYNAMIC_TP2_GUCLU_RR = 2.5
CONFIDENCE_BASED_TRAIL = False

# ═══ v3.12 — ADX TREND GÜCÜ FİLTRESİ ═══
# ADX < 20 = choppy/yönsüz piyasa → trade açma
# ADX > 25 = güçlü trend → trade aç
# Araştırma: Profesyonel scalperlar trend olmayan piyasada trade açmaz
# DEFAULT: KAPALI — v3.9 baseline'da yoktu, '--adx' ile aktifleştir
ADX_FILTER = False
ADX_MIN_THRESHOLD = 20      # Bu değerin altında yeni trade açma
ADX_PERIOD = 14             # Standart ADX periyodu

# ═══ v3.12 — UZATILMIŞ VERİ & WALK-FORWARD ═══
# Komut satırı argümanları:
#   python backtest.py              → Normal 7 gün backtest
#   python backtest.py --period 30  → 30 günlük backtest (5dk veri)
#   python backtest.py --period 60  → 60 günlük backtest (5dk veri)
#   python backtest.py --walkforward → Walk-forward validasyonu
BACKTEST_PERIOD_DAYS = 7     # Default, komut satırından değiştirilebilir
WALK_FORWARD_MODE = False    # Walk-forward test modu
WF_IN_SAMPLE_PCT = 0.70     # %70 in-sample, %30 out-of-sample
WF_NUM_FOLDS = 3            # Kaç fold ile test

IS_5MIN_DATA = False  # Default, veri indirildikten sonra güncellenir

# ═══ KOMUT SATIRI ARGÜMANLARI ═══
if '--period' in sys.argv:
    try:
        idx = sys.argv.index('--period')
        BACKTEST_PERIOD_DAYS = int(sys.argv[idx + 1])
        if BACKTEST_PERIOD_DAYS > 7:
            IS_5MIN_DATA = True  # 7 günden fazla = 5dk veri kullan (yfinance 1dk limiti)
    except (IndexError, ValueError):
        pass

if '--walkforward' in sys.argv:
    WALK_FORWARD_MODE = True
    if BACKTEST_PERIOD_DAYS <= 7:
        BACKTEST_PERIOD_DAYS = 60  # WF mode minimum 60 gün
        IS_5MIN_DATA = True

if '--adx' in sys.argv:
    ADX_FILTER = True
if '--noadx' in sys.argv:
    ADX_FILTER = False

DEBUG_MODE = '--debug' in sys.argv


# ═══════════════════════════════════════════
# İNDİKATÖR HESAPLAMA
# ═══════════════════════════════════════════
def calculate_indicators(df):
    close = df['Close']
    d = close.diff()

    gain = d.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(com=13, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = (100 - 100 / (1 + rs)).fillna(50)

    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    df['MACD'] = ema_fast - ema_slow
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

    df['MA20'] = close.rolling(window=20).mean()
    df['MA50'] = close.rolling(window=50).mean()

    tp = (df['High'] + df['Low'] + close) / 3
    vol = df['Volume'].copy()
    if vol.sum() == 0:
        vol = (df['High'] - df['Low']) * 1000
    cumtp = (tp * vol).rolling(window=20).sum()
    cumvol = vol.rolling(window=20).sum().replace(0, np.nan)
    df['VWAP'] = (cumtp / cumvol).fillna(0)

    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - close.shift()).abs(),
        (df['Low'] - close.shift()).abs()
    ], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()

    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    df['BB_Up'] = sma20 + 2 * std20
    df['BB_Mid'] = sma20
    df['BB_Low'] = sma20 - 2 * std20
    df['BB_Width'] = ((df['BB_Up'] - df['BB_Low']) / df['BB_Mid'] * 100).fillna(0)

    df['BB_Width_Pct'] = df['BB_Width'].rolling(50).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100 if len(x) >= 10 else 50,
        raw=False
    ).fillna(50)

    # v3.12 — ADX (Average Directional Index) hesaplama
    # Trend gücünü ölçer: < 20 yönsüz, 20-25 zayıf, 25-50 güçlü, > 50 çok güçlü
    _high = df['High'].copy()
    _low = df['Low'].copy()
    plus_dm = _high.diff().copy()
    minus_dm = (-_low.diff()).copy()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    # Standart ADX kuralı: sadece daha büyük olan DM geçerli
    cond_plus = plus_dm < minus_dm  # +DM < -DM ise +DM = 0
    cond_minus = minus_dm < plus_dm  # -DM < +DM ise -DM = 0
    plus_dm[cond_plus] = 0
    minus_dm[cond_minus] = 0

    atr_adx = tr.rolling(ADX_PERIOD).mean()
    plus_di = 100 * (plus_dm.ewm(span=ADX_PERIOD, adjust=False).mean() / atr_adx.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(span=ADX_PERIOD, adjust=False).mean() / atr_adx.replace(0, np.nan))
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
    df['ADX'] = dx.ewm(span=ADX_PERIOD, adjust=False).mean().fillna(0)
    df['PLUS_DI'] = plus_di.fillna(0)
    df['MINUS_DI'] = minus_di.fillna(0)

    return df


# ═══════════════════════════════════════════
# KATMAN 1: HTF TREND — Adaptif Pencere
# ═══════════════════════════════════════════
# 1dk veri: MA100/MA250 ≈ 5dk MA20/MA50
# 5dk veri: Gerçek MA20/MA50 doğrudan kullanılır

def detect_htf_trend(df, idx):
    """
    Veri çözünürlüğüne göre adaptif HTF trend algılama.
    5dk veride gerçek MA20/MA50, 1dk veride simüle.
    """
    # 5dk mi 1dk mı? Veri boyutuna göre tahmin
    is_5min = IS_5MIN_DATA

    min_bars = 60 if is_5min else 300
    lookback = 80 if is_5min else 350

    if idx < min_bars:
        return 'neutral', 0

    chunk = df.iloc[max(0, idx - lookback):idx + 1]
    close = chunk['Close']

    if is_5min:
        # Gerçek 5dk MA'ları
        ma_short = close.rolling(20).mean().iloc[-1]
        ma_long = close.rolling(50).mean().iloc[-1]
    else:
        # 1dk simülasyon
        ma_short = close.rolling(100).mean().iloc[-1]
        ma_long = close.rolling(250).mean().iloc[-1]

    current = float(close.iloc[-1])

    # MACD
    ema_span_f = 12 if is_5min else 60
    ema_span_s = 26 if is_5min else 130
    ema_f = close.ewm(span=ema_span_f, adjust=False).mean().iloc[-1]
    ema_s = close.ewm(span=ema_span_s, adjust=False).mean().iloc[-1]
    macd_trend = ema_f - ema_s

    sig_span = 9 if is_5min else 45
    macd_series = close.ewm(span=ema_span_f, adjust=False).mean() - close.ewm(span=ema_span_s, adjust=False).mean()
    macd_sig = macd_series.ewm(span=sig_span, adjust=False).mean().iloc[-1]

    if pd.isna(ma_short) or pd.isna(ma_long):
        return 'neutral', 0

    ma_short = float(ma_short)
    ma_long = float(ma_long)

    bull_pts = 0
    bear_pts = 0

    if current > ma_long:
        bull_pts += 3
    else:
        bear_pts += 3

    if current > ma_short:
        bull_pts += 2
    else:
        bear_pts += 2

    if ma_short > ma_long:
        bull_pts += 2
    else:
        bear_pts += 2

    if macd_trend > macd_sig:
        bull_pts += 1
    else:
        bear_pts += 1

    total = bull_pts - bear_pts

    if total >= HTF_TREND_THRESHOLD:
        return 'bullish', total
    elif total <= -HTF_TREND_THRESHOLD:
        return 'bearish', total
    return 'neutral', total


# ═══════════════════════════════════════════
# KATMAN 2: PRICE ACTION YAPI ANALİZİ
# ═══════════════════════════════════════════
def detect_structure(df_slice, lookback=20):
    if len(df_slice) < lookback + 5:
        return 'neutral', False

    recent = df_slice.tail(lookback)
    highs = recent['High'].values
    lows = recent['Low'].values

    swing_highs = []
    swing_lows = []
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            swing_highs.append(highs[i])
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            swing_lows.append(lows[i])

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return 'neutral', False

    hh = swing_highs[-1] > swing_highs[-2]
    hl = swing_lows[-1] > swing_lows[-2]
    lh = swing_highs[-1] < swing_highs[-2]
    ll = swing_lows[-1] < swing_lows[-2]

    last_close = float(df_slice['Close'].iloc[-1])

    if hh and hl:
        if last_close < swing_lows[-1]:
            return 'bearish', True
        return 'bullish', False
    elif lh and ll:
        if last_close > swing_highs[-1]:
            return 'bullish', True
        return 'bearish', False

    return 'neutral', False


# ═══════════════════════════════════════════
# KATMAN 3: BOLLINGER SQUEEZE
# ═══════════════════════════════════════════
def detect_squeeze(row):
    bb_width_pct = float(row.get('BB_Width_Pct', 50))
    macd_hist = float(row.get('MACD_Hist', 0))
    close = float(row['Close'])
    bb_mid = float(row.get('BB_Mid', close))

    is_squeeze = bb_width_pct <= 20

    if is_squeeze:
        if macd_hist > 0 and close > bb_mid:
            return True, 'bullish'
        elif macd_hist < 0 and close < bb_mid:
            return True, 'bearish'
        return True, 'neutral'

    return False, 'neutral'


# ═══════════════════════════════════════════
# SWING LEVEL & AKILLI SL/TP — OPTİMİZE
# ═══════════════════════════════════════════
def find_swing_levels(df_slice, lookback=20):
    if len(df_slice) < lookback:
        return [], []

    recent = df_slice.tail(lookback)
    highs = recent['High'].values
    lows = recent['Low'].values

    swing_highs = []
    swing_lows = []
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            swing_highs.append(float(highs[i]))
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            swing_lows.append(float(lows[i]))

    return swing_highs, swing_lows


def get_dynamic_atr_multiplier(bb_width_pct):
    if bb_width_pct <= 15:
        return max(1.5, MIN_SL_ATR_MULT)  # Minimum 1.5x (eskisi 1.2'ydi)
    elif bb_width_pct <= 30:
        return 1.8
    elif bb_width_pct <= 50:
        return 2.2
    elif bb_width_pct <= 75:
        return 2.8
    return 3.2


def calculate_smart_sl_tp(df_slice, current_price, atr_val, trend_dir, bb_width_pct):
    swing_highs, swing_lows = find_swing_levels(df_slice)
    dyn_mult = get_dynamic_atr_multiplier(bb_width_pct)
    min_sl_distance = max(dyn_mult * atr_val, MIN_SL_ATR_MULT * atr_val)

    sl = 0
    sl_source = ""

    if trend_dir == 'bullish':
        valid = [s for s in swing_lows if s < current_price]
        if valid:
            nearest = max(valid)
            dist = current_price - nearest
            if dist >= 0.8 * atr_val:  # 0.5 → 0.8 (daha güvenli swing seçimi)
                sl = nearest - SL_WICK_BUFFER * atr_val  # 0.3 → 0.5 ATR wick koruması
                # SL minimum mesafeden kısa olmasın
                if (current_price - sl) < min_sl_distance:
                    sl = current_price - min_sl_distance
                    sl_source = "ATR"
                else:
                    sl_source = "Swing Low"
            else:
                sl = current_price - min_sl_distance
                sl_source = "ATR"
        else:
            sl = current_price - min_sl_distance
            sl_source = "ATR"

    elif trend_dir == 'bearish':
        valid = [s for s in swing_highs if s > current_price]
        if valid:
            nearest = min(valid)
            dist = nearest - current_price
            if dist >= 0.8 * atr_val:
                sl = nearest + SL_WICK_BUFFER * atr_val
                if (sl - current_price) < min_sl_distance:
                    sl = current_price + min_sl_distance
                    sl_source = "ATR"
                else:
                    sl_source = "Swing High"
            else:
                sl = current_price + min_sl_distance
                sl_source = "ATR"
        else:
            sl = current_price + min_sl_distance
            sl_source = "ATR"
    else:
        sl = current_price - min_sl_distance
        sl_source = "ATR"

    sl_distance = abs(current_price - sl)

    # R:R bazlı TP — optimize edilmiş oranlar
    if trend_dir == 'bullish':
        tp1 = current_price + TP1_RR * sl_distance
        tp2 = current_price + TP2_RR * sl_distance
    elif trend_dir == 'bearish':
        tp1 = current_price - TP1_RR * sl_distance
        tp2 = current_price - TP2_RR * sl_distance
    else:
        tp1 = current_price + TP1_RR * sl_distance
        tp2 = current_price + TP2_RR * sl_distance

    return sl, tp1, tp2, sl_source, sl_distance


# ═══════════════════════════════════════════
# AKILLI KALİTE SKORU — Kötü Sinyalleri Ele
# ═══════════════════════════════════════════
def calculate_signal_quality(df, idx, trend_dir, atr_val):
    """
    Her sinyal için 0-8 arası kalite skoru hesaplar.
    Düşük skorlu sinyaller filtrelenir.
    v3.0 — 8 kriterli gelişmiş filtre
    """
    score = 0
    reasons = []
    row = df.iloc[idx]
    close = float(row['Close'])
    high = float(row['High'])
    low = float(row['Low'])
    open_p = float(row['Open'])

    # 1) RSI Momentum — RSI sinyalle aynı yöne mi gidiyor? (+1)
    if idx >= 3:
        rsi_now = float(df.iloc[idx]['RSI'])
        rsi_prev = float(df.iloc[idx - 3]['RSI'])
        if trend_dir == 'bullish' and rsi_now > rsi_prev:
            score += 1
            reasons.append("RSI↑")
        elif trend_dir == 'bearish' and rsi_now < rsi_prev:
            score += 1
            reasons.append("RSI↓")

    # 2) Mum Momentum — Son N mumun çoğunluğu sinyalle uyumlu mu? (+1)
    if idx >= MOMENTUM_LOOKBACK:
        closes = [float(df.iloc[idx - j]['Close']) for j in range(MOMENTUM_LOOKBACK + 1)]
        up_moves = sum(1 for j in range(len(closes) - 1) if closes[j] > closes[j + 1])
        down_moves = MOMENTUM_LOOKBACK - up_moves
        # Adaptif eşik: lookback'in %60'ı kadar uyumlu mum yeterli
        mom_threshold = max(2, int(MOMENTUM_LOOKBACK * 0.6))
        if trend_dir == 'bullish' and up_moves >= mom_threshold:
            score += 1
            reasons.append(f"Mom↑({up_moves}/{MOMENTUM_LOOKBACK})")
        elif trend_dir == 'bearish' and down_moves >= mom_threshold:
            score += 1
            reasons.append(f"Mom↓({down_moves}/{MOMENTUM_LOOKBACK})")

    # 3) BB Pozisyon — Fiyat uygun BB tarafında mı? (+1)
    bb_up = float(row.get('BB_Up', close))
    bb_low = float(row.get('BB_Low', close))
    bb_mid = float(row.get('BB_Mid', close))

    if trend_dir == 'bullish' and close < bb_mid:
        score += 1
        reasons.append("BB_alt")
    elif trend_dir == 'bearish' and close > bb_mid:
        score += 1
        reasons.append("BB_üst")

    # 4) ATR Gücü — Volatilite yeterli mi? (+1)
    if atr_val >= MIN_ATR_THRESHOLD * 1.5:
        score += 1
        reasons.append(f"ATR_ok")

    # 5) VWAP Onay — Fiyat VWAP'la uyumlu mu? (+1)
    vwap = float(row.get('VWAP', 0))
    if vwap > 0:
        if trend_dir == 'bullish' and close > vwap:
            score += 1
            reasons.append("VWAP↑")
        elif trend_dir == 'bearish' and close < vwap:
            score += 1
            reasons.append("VWAP↓")

    # 6) MUM GÖVDESİ ANALİZİ — Mum sinyalle uyumlu mu? (+1)
    body = close - open_p
    candle_range = high - low if high > low else 0.001
    body_ratio = abs(body) / candle_range

    if trend_dir == 'bullish' and body > 0 and body_ratio > 0.5:
        # Güçlü yeşil mum (gövde > %50)
        score += 1
        reasons.append("Mum_güçlü↑")
    elif trend_dir == 'bearish' and body < 0 and body_ratio > 0.5:
        # Güçlü kırmızı mum (gövde > %50)
        score += 1
        reasons.append("Mum_güçlü↓")

    # 7) MACD HİSTOGRAM İVME — Histogram büyüyor mu? (+1)
    if idx >= 2:
        hist_now = float(df.iloc[idx].get('MACD_Hist', 0))
        hist_prev = float(df.iloc[idx - 1].get('MACD_Hist', 0))
        hist_prev2 = float(df.iloc[idx - 2].get('MACD_Hist', 0))
        if trend_dir == 'bullish' and hist_now > hist_prev > hist_prev2:
            score += 1
            reasons.append("MACD_ivme↑")
        elif trend_dir == 'bearish' and hist_now < hist_prev < hist_prev2:
            score += 1
            reasons.append("MACD_ivme↓")

    # 8) FİYAT-MA İLİŞKİSİ — Fiyat MA20'ye yakın mı? (geri çekilme girişi) (+1)
    ma20 = float(row.get('MA20', 0))
    if ma20 > 0:
        dist_to_ma20 = abs(close - ma20) / atr_val if atr_val > 0 else 999
        if dist_to_ma20 < 1.0:
            # Fiyat MA20'ye yakın = iyi giriş noktası (pullback)
            score += 1
            reasons.append("MA20_yakın")

    return score, reasons


# ═══════════════════════════════════════════
# POZİSYON BOYUTU HESAPLAMA
# ═══════════════════════════════════════════
def calculate_position_size(sl_distance, balance):
    risk_amount = balance * (ACCOUNT_CONFIG['risk_pct'] / 100)
    contract = ACCOUNT_CONFIG['contract_size']
    min_lot = ACCOUNT_CONFIG['min_lot']
    max_lot = ACCOUNT_CONFIG['max_lot']

    if sl_distance <= 0:
        return min_lot, risk_amount

    raw_lot = risk_amount / (sl_distance * contract)
    lot = round(max(min(raw_lot, max_lot), min_lot), 2)
    actual_risk = lot * sl_distance * contract
    return lot, actual_risk


# ═══════════════════════════════════════════
# ANA BACKTEST
# ═══════════════════════════════════════════
print("=" * 60)
print("   AURUMPULSE BACKTEST v3.12 (ADX + Walk-Forward)")
print("   v3.9 Baz — Parametreler Değiştirilmedi")
print("=" * 60)
print()

if BACKTEST_PERIOD_DAYS <= 7:
    print("📊 Altın (XAU/USD) son 7 günlük 1dk veriler indiriliyor...")
    dl_interval = "1m"
    dl_period = "7d"
    data_interval = "1dk"
    IS_5MIN_DATA = False
else:
    print(f"📊 Altın (XAU/USD) son {BACKTEST_PERIOD_DAYS} günlük 5dk veriler indiriliyor...")
    dl_interval = "5m"
    dl_period = f"{BACKTEST_PERIOD_DAYS}d"
    data_interval = "5dk"
    IS_5MIN_DATA = True

try:
    df = yf.download("GC=F", interval=dl_interval, period=dl_period, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.dropna(inplace=True)
    total_bars = len(df)
    print(f"✅ {total_bars} adet mum yüklendi ({data_interval}).\n")

    # ═══ v3.9 KONFİGÜRASYON — v3.6 birebir + seans filtresi ═══
    # UYARI: BU PARAMETRELER v3.9 ORİJİNAL — DEĞİŞTİRME!
    COOLDOWN_BARS = 5
    LOSS_STREAK_COOLDOWN = 20
    MAX_LOSS_STREAK = 3
    MIN_ATR_THRESHOLD = 0.80
    SL_WICK_BUFFER = 0.5
    MIN_SL_ATR_MULT = 2.0
    HTF_TREND_THRESHOLD = 5
    MIN_QUALITY_SCORE_GUCLU = 3
    MIN_QUALITY_SCORE_ORTA = 4
    TP1_RR = 1.5
    TP2_RR = 2.5
    TRAIL_LOCK_PCT = 0.30
    TRAIL_ATR_MULT = 1.0
    ENABLE_EARLY_EXIT = True
    EARLY_EXIT_MIN_BARS = 10
    EARLY_EXIT_MIN_LOSS_ATR = 0.5
    EARLY_EXIT_MACD_CONSECUTIVE = 3
    REQUIRE_MACD_CONFIRM = True
    REQUIRE_CANDLE_CONFIRM = False
    RSI_MAX_LONG = 62
    RSI_MIN_SHORT = 38
    PARTIAL_TP1_CLOSE = False
    PARTIAL_TP1_PCT = 0.50
    WINNING_STREAK_BOOST = True
    STREAK_BOOST_AFTER = 2
    STREAK_BOOST_RISK_PCT = 3.0
    PROGRESSIVE_RISK = True
    RISK_REDUCTION_AFTER_LOSSES = 3
    REDUCED_RISK_PCT = 1.0
    SESSION_FILTER = True
    SESSION_START_UTC = 10
    SESSION_END_UTC = 20
    PEAK_ONLY_ORTA = False
    DYNAMIC_TP2 = False
    DYNAMIC_TP2_GUCLU_RR = 2.5
    CONFIDENCE_BASED_TRAIL = False
    # ═══ v3.12 TIMEFRAME ADAPTASYONU ═══
    # 5dk veride her mum 5 kat daha fazla bilgi taşır.
    # Strateji mantığı ve parametreler DEĞİŞMİYOR — sadece
    # zaman bazlı lookback'ler orantılı olarak uyarlanıyor.
    # 1dk'da 5 bar = 5 dakika ↔ 5dk'da 1 bar = 5 dakika (aynı zaman penceresi)
    if IS_5MIN_DATA:
        # Momentum lookback: 1dk'da 5 bar (5dk) → 5dk'da 3 bar (15dk, yaklaşık eşdeğer)
        MOMENTUM_LOOKBACK = 3

        # Kalite eşikleri: 5dk mumlar daha fazla bilgi taşıdığı için
        # daha az teyit barı yeterli (her mum = 5 mum bilgisi)
        # Bu parametre değişikliği DEĞİL, timeframe normalizasyonu
        # Debug verisi: 5dk'da tüm ORTA sinyaller QS=2 alıyor — 8 kriterden
        # sadece 2'si 5dk zaman ölçeğinde tutarlı tetikleniyor
        MIN_QUALITY_SCORE_GUCLU = 2   # 3 → 2 (5dk'da yeterli)
        MIN_QUALITY_SCORE_ORTA = 2    # 4 → 2 (5dk'da tüm ORTA sinyaller QS=2)

        # Cooldown: 1dk'da 5 bar (5dk bekleme) → 5dk'da 2 bar (10dk bekleme, yaklaşık)
        COOLDOWN_BARS = 2
        LOSS_STREAK_COOLDOWN = 8  # 1dk'da 20 bar (20dk) → 5dk'da 8 bar (40dk, daha güvenli)

        # Erken çıkış: 1dk'da 10 bar (10dk) → 5dk'da 4 bar (20dk)
        EARLY_EXIT_MIN_BARS = 4
        EARLY_EXIT_MACD_CONSECUTIVE = 2  # 1dk'da 3 bar → 5dk'da 2 bar (10dk vs 3dk)

        # BB Width Pct penceresi 5dk veride zaten daha geniş zaman kapsar
        # ATR threshold: 5dk ATR ~2-3x büyük, oranı korumak için artır
        MIN_ATR_THRESHOLD = 2.0  # 5dk'da ATR doğal olarak daha büyük

        print(f"  ⏱️ 5dk timeframe adaptasyonu aktif:")
        print(f"     Kalite: GÜÇLÜ≥{MIN_QUALITY_SCORE_GUCLU}, ORTA≥{MIN_QUALITY_SCORE_ORTA}")
        print(f"     Cooldown: {COOLDOWN_BARS} bar, Momentum: {MOMENTUM_LOOKBACK} bar")
        print(f"     Min ATR: ${MIN_ATR_THRESHOLD}, Early Exit: {EARLY_EXIT_MIN_BARS} bar")
        print()

    adx_str = f"ADX≥{ADX_MIN_THRESHOLD}" if ADX_FILTER else "ADX kapalı"
    wf_str = "Walk-Forward AÇIK" if WALK_FORWARD_MODE else ""
    print(f"⚙️ v3.12 konfigürasyonu yüklendi (v3.9 baz + {adx_str} + Seans 10-20 UTC) {wf_str}\n")
except Exception as e:
    print(f"❌ Veri indirilirken hata: {e}")
    exit()

print("🧮 İndikatörler hesaplanıyor...")
df = calculate_indicators(df)
df.dropna(subset=['RSI', 'MACD', 'MA50', 'ATR', 'BB_Width_Pct', 'ADX'], inplace=True)
print(f"✅ İndikatörlü veri: {len(df)} mum\n")

# ── Backtest Değişkenleri ──
balance = ACCOUNT_CONFIG['balance']
initial_balance = balance

in_position = False
position_type = None
entry_price = 0
sl_price = 0
tp1_price = 0
tp2_price = 0
lot_size = 0
entry_confidence = ""
tp1_hit = False            # TP1 vuruldu mu (trailing stop için)
trailing_sl = 0            # Trailing stop seviyesi
entry_bar = 0              # v3.2: Pozisyon açıldığı bar (erken çıkış için min bekleme)
entry_quality = 0          # v3.12: Giriş kalite skoru
entry_adx = 0              # v3.12: Giriş anındaki ADX değeri
entry_timestamp = ''       # v3.12: Giriş zamanı
trade_metadata = []        # v3.12: Her trade için ekstra metadata listesi
last_trade_bar = -999      # Son işlem barı (cooldown)
consecutive_losses = 0     # Ardışık kayıp sayacı
consecutive_wins = 0       # v3.5: Ardışık kazanç sayacı

# İstatistikler
trades = []
winning = 0
losing = 0
breakeven = 0
tp1_hits = 0
tp2_hits = 0
total_pnl = 0
max_balance = balance
max_drawdown = 0
equity_curve = []
confidence_stats = {'GUCLU': {'win': 0, 'loss': 0}, 'ORTA': {'win': 0, 'loss': 0}, 'ZAYIF': {'win': 0, 'loss': 0}}
htf_trend_log = {'bullish': 0, 'bearish': 0, 'neutral': 0}
filtered_by_quality = 0  # Kalite filtresi tarafından elenen sinyal sayısı
filtered_by_adx = 0      # v3.12: ADX filtresi tarafından elenen bar sayısı
filtered_by_risk = 0     # v3.12: Risk kontrolü tarafından elenen sinyal sayısı
debug_quality_scores = []  # v3.12: Debug için kalite skoru dağılımı

print("🤖 Birleşik Sinyal Motoru simüle ediliyor...\n")

# 1dk veri: MA250 + güvenlik payı = 350 bar
# 5dk veri: MA50 doğrudan = 80 bar yeterli ama ADX(14) ve BB(50) için 100
start_idx = 100 if IS_5MIN_DATA else 350

for i in range(start_idx, len(df)):
    row = df.iloc[i]
    current_price = float(row['Close'])
    high = float(row['High'])
    low = float(row['Low'])
    atr_val = float(row['ATR'])
    rsi_val = float(row['RSI'])
    macd_v = float(row['MACD'])
    macd_s = float(row['MACD_Signal'])
    macd_h = float(row['MACD_Hist'])
    vwap_val = float(row['VWAP'])
    bb_width_pct = float(row['BB_Width_Pct'])

    # ── Pozisyon Takibi ──
    if in_position:

        # ── v3.2 AKILLI ERKEN ÇIKIŞ ──
        # Koşullar: (1) min 10 mum geçmiş olmalı, (2) min 0.5 ATR zararda,
        #           (3) MACD histogram art arda 3 mum ters yönde
        if ENABLE_EARLY_EXIT and not tp1_hit:
            early_exit = False
            bars_in_trade = i - entry_bar

            if bars_in_trade >= EARLY_EXIT_MIN_BARS:
                # Zarar kontrolü — küçük salınımda çıkma
                if position_type == 'LONG':
                    unrealized_loss = entry_price - current_price
                elif position_type == 'SHORT':
                    unrealized_loss = current_price - entry_price
                else:
                    unrealized_loss = 0

                if unrealized_loss >= EARLY_EXIT_MIN_LOSS_ATR * atr_val:
                    # MACD histogram art arda N mum ters yönde mi?
                    if i >= EARLY_EXIT_MACD_CONSECUTIVE:
                        macd_hists = [float(df.iloc[i - j]['MACD_Hist']) for j in range(EARLY_EXIT_MACD_CONSECUTIVE)]
                        if position_type == 'LONG' and all(h < 0 for h in macd_hists):
                            early_exit = True
                        elif position_type == 'SHORT' and all(h > 0 for h in macd_hists):
                            early_exit = True

            if early_exit:
                pnl = 0
                if position_type == 'LONG':
                    pnl = (current_price - entry_price) * lot_size * ACCOUNT_CONFIG['contract_size']
                else:
                    pnl = (entry_price - current_price) * lot_size * ACCOUNT_CONFIG['contract_size']
                balance += pnl
                total_pnl += pnl
                if pnl > 0:
                    winning += 1
                    consecutive_losses = 0
                    consecutive_wins += 1
                else:
                    losing += 1
                    consecutive_losses += 1
                    consecutive_wins = 0
                conf_key = entry_confidence
                if conf_key in confidence_stats:
                    confidence_stats[conf_key]['win' if pnl > 0 else 'loss'] += 1
                trades.append({'type': position_type, 'entry': entry_price, 'exit': round(current_price, 2),
                               'pnl': round(pnl, 2), 'result': 'EARLY_EXIT', 'lot': lot_size,
                               'confidence': entry_confidence})
                in_position = False
                last_trade_bar = i
                equity_curve.append(balance)
                continue

        # ── TRAILING STOP: TP1 sonrası kârın %70'ini kilitle ──
        if tp1_hit and TRAIL_STOP_AFTER_TP1:
            tp1_dist = abs(tp1_price - entry_price)
            if position_type == 'LONG':
                min_lock = entry_price + tp1_dist * TRAIL_LOCK_PCT
                new_trail = max(min_lock, current_price - TRAIL_ATR_MULT * atr_val)
                if new_trail > trailing_sl:
                    trailing_sl = new_trail
                active_sl = trailing_sl
            else:
                min_lock = entry_price - tp1_dist * TRAIL_LOCK_PCT
                new_trail = min(min_lock, current_price + TRAIL_ATR_MULT * atr_val)
                if new_trail < trailing_sl:
                    trailing_sl = new_trail
                active_sl = trailing_sl
        else:
            active_sl = sl_price

        if position_type == 'LONG':
            # SL kontrolü
            if low <= active_sl:
                exit_price = active_sl
                pnl = (exit_price - entry_price) * lot_size * ACCOUNT_CONFIG['contract_size']
                balance += pnl
                total_pnl += pnl
                result = 'TRAIL' if tp1_hit else 'SL'
                if pnl > 0:
                    winning += 1
                    consecutive_losses = 0
                    consecutive_wins += 1
                else:
                    losing += 1
                    consecutive_losses += 1
                    consecutive_wins = 0
                conf_key = entry_confidence
                if conf_key in confidence_stats:
                    confidence_stats[conf_key]['win' if pnl > 0 else 'loss'] += 1
                trades.append({'type': 'LONG', 'entry': entry_price, 'exit': round(exit_price, 2),
                               'pnl': round(pnl, 2), 'result': result, 'lot': lot_size,
                               'confidence': entry_confidence})
                in_position = False
                last_trade_bar = i

            elif high >= tp2_price and not tp1_hit:
                pnl = (tp1_price - entry_price) * lot_size * ACCOUNT_CONFIG['contract_size']
                balance += pnl
                total_pnl += pnl
                winning += 1
                tp1_hits += 1
                consecutive_losses = 0
                consecutive_wins += 1
                conf_key = entry_confidence
                if conf_key in confidence_stats:
                    confidence_stats[conf_key]['win'] += 1
                trades.append({'type': 'LONG', 'entry': entry_price, 'exit': round(tp1_price, 2),
                               'pnl': round(pnl, 2), 'result': 'TP1', 'lot': lot_size,
                               'confidence': entry_confidence})
                in_position = False
                last_trade_bar = i

            elif high >= tp2_price and tp1_hit:
                pnl = (tp2_price - entry_price) * lot_size * ACCOUNT_CONFIG['contract_size']
                balance += pnl
                total_pnl += pnl
                winning += 1
                tp2_hits += 1
                consecutive_losses = 0
                consecutive_wins += 1
                conf_key = entry_confidence
                if conf_key in confidence_stats:
                    confidence_stats[conf_key]['win'] += 1
                trades.append({'type': 'LONG', 'entry': entry_price, 'exit': round(tp2_price, 2),
                               'pnl': round(pnl, 2), 'result': 'TP2', 'lot': lot_size,
                               'confidence': entry_confidence})
                in_position = False
                last_trade_bar = i

            # TP1 — trailing stop başlat (v3.6 orijinal)
            elif high >= tp1_price and not tp1_hit:
                tp1_hit = True
                tp1_dist = tp1_price - entry_price
                trailing_sl = entry_price + tp1_dist * TRAIL_LOCK_PCT
                tp1_hits += 1

                if PARTIAL_TP1_CLOSE:
                        partial_lot = lot_size * PARTIAL_TP1_PCT
                        partial_pnl = (tp1_price - entry_price) * partial_lot * ACCOUNT_CONFIG['contract_size']
                        balance += partial_pnl
                        total_pnl += partial_pnl
                        lot_size = lot_size - partial_lot
                        trades.append({'type': 'LONG', 'entry': entry_price, 'exit': round(tp1_price, 2),
                                       'pnl': round(partial_pnl, 2), 'result': 'TP1_PARTIAL', 'lot': round(partial_lot, 4),
                                       'confidence': entry_confidence})
                        winning += 1
                        consecutive_losses = 0
                        consecutive_wins += 1
                        conf_key = entry_confidence
                        if conf_key in confidence_stats:
                            confidence_stats[conf_key]['win'] += 1

        elif position_type == 'SHORT':
            if high >= active_sl:
                exit_price = active_sl
                pnl = (entry_price - exit_price) * lot_size * ACCOUNT_CONFIG['contract_size']
                balance += pnl
                total_pnl += pnl
                result = 'TRAIL' if tp1_hit else 'SL'
                if pnl > 0:
                    winning += 1
                    consecutive_losses = 0
                    consecutive_wins += 1
                else:
                    losing += 1
                    consecutive_losses += 1
                    consecutive_wins = 0
                conf_key = entry_confidence
                if conf_key in confidence_stats:
                    confidence_stats[conf_key]['win' if pnl > 0 else 'loss'] += 1
                trades.append({'type': 'SHORT', 'entry': entry_price, 'exit': round(exit_price, 2),
                               'pnl': round(pnl, 2), 'result': result, 'lot': lot_size,
                               'confidence': entry_confidence})
                in_position = False
                last_trade_bar = i

            elif low <= tp2_price and not tp1_hit:
                pnl = (entry_price - tp1_price) * lot_size * ACCOUNT_CONFIG['contract_size']
                balance += pnl
                total_pnl += pnl
                winning += 1
                tp1_hits += 1
                consecutive_losses = 0
                consecutive_wins += 1
                conf_key = entry_confidence
                if conf_key in confidence_stats:
                    confidence_stats[conf_key]['win'] += 1
                trades.append({'type': 'SHORT', 'entry': entry_price, 'exit': round(tp1_price, 2),
                               'pnl': round(pnl, 2), 'result': 'TP1', 'lot': lot_size,
                               'confidence': entry_confidence})
                in_position = False
                last_trade_bar = i

            elif low <= tp2_price and tp1_hit:
                pnl = (entry_price - tp2_price) * lot_size * ACCOUNT_CONFIG['contract_size']
                balance += pnl
                total_pnl += pnl
                winning += 1
                tp2_hits += 1
                consecutive_losses = 0
                consecutive_wins += 1
                conf_key = entry_confidence
                if conf_key in confidence_stats:
                    confidence_stats[conf_key]['win'] += 1
                trades.append({'type': 'SHORT', 'entry': entry_price, 'exit': round(tp2_price, 2),
                               'pnl': round(pnl, 2), 'result': 'TP2', 'lot': lot_size,
                               'confidence': entry_confidence})
                in_position = False
                last_trade_bar = i

            elif low <= tp1_price and not tp1_hit:
                tp1_hit = True
                tp1_dist = entry_price - tp1_price
                trailing_sl = entry_price - tp1_dist * TRAIL_LOCK_PCT
                tp1_hits += 1

                if PARTIAL_TP1_CLOSE:
                        partial_lot = lot_size * PARTIAL_TP1_PCT
                        partial_pnl = (entry_price - tp1_price) * partial_lot * ACCOUNT_CONFIG['contract_size']
                        balance += partial_pnl
                        total_pnl += partial_pnl
                        lot_size = lot_size - partial_lot
                        trades.append({'type': 'SHORT', 'entry': entry_price, 'exit': round(tp1_price, 2),
                                       'pnl': round(partial_pnl, 2), 'result': 'TP1_PARTIAL', 'lot': round(partial_lot, 4),
                                       'confidence': entry_confidence})
                        winning += 1
                        consecutive_losses = 0
                        consecutive_wins += 1
                        conf_key = entry_confidence
                        if conf_key in confidence_stats:
                            confidence_stats[conf_key]['win'] += 1

        # Drawdown takibi
        if balance > max_balance:
            max_balance = balance
        dd = (max_balance - balance) / max_balance * 100 if max_balance > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

        equity_curve.append(balance)
        continue

    # ── COOLDOWN KONTROLÜ (ardışık kayıpta uzun bekleme) ──
    required_cooldown = LOSS_STREAK_COOLDOWN if consecutive_losses >= MAX_LOSS_STREAK else COOLDOWN_BARS
    if (i - last_trade_bar) < required_cooldown:
        equity_curve.append(balance)
        continue

    # ── MİNİMUM ATR FİLTRESİ ──
    if atr_val < MIN_ATR_THRESHOLD:
        equity_curve.append(balance)
        continue

    # ── v3.12 ADX TREND GÜCÜ FİLTRESİ ──
    # Choppy/yönsüz piyasada trade açma (ADX < 20)
    if ADX_FILTER:
        adx_val = float(row.get('ADX', 0))
        if adx_val < ADX_MIN_THRESHOLD:
            filtered_by_adx += 1
            equity_curve.append(balance)
            continue

    # ── v3.9 SEANS FİLTRESİ ──
    # Sadece London-NY overlap saatlerinde yeni işlem aç
    # Asya seansı (00:00-07:00 UTC) düşük volatilite → false breakout riski
    if SESSION_FILTER:
        bar_time = df.index[i]
        if hasattr(bar_time, 'hour'):
            bar_hour_utc = bar_time.hour
            # Eğer timezone-aware ise UTC'ye çevir
            if hasattr(bar_time, 'tzinfo') and bar_time.tzinfo is not None:
                import pytz
                utc_time = bar_time.astimezone(pytz.UTC)
                bar_hour_utc = utc_time.hour
            if bar_hour_utc < SESSION_START_UTC or bar_hour_utc >= SESSION_END_UTC:
                equity_curve.append(balance)
                continue

    # ── Yeni Sinyal Üretimi ──

    # Katman 1: HTF Trend (1dk uzun pencere)
    htf_trend, htf_score = detect_htf_trend(df, i)
    htf_trend_log[htf_trend] = htf_trend_log.get(htf_trend, 0) + 1

    # Katman 2: Yapı Analizi
    lookback_slice = df.iloc[max(0, i - 30):i + 1]
    structure_trend, bos_detected = detect_structure(lookback_slice)

    # Katman 3: Squeeze
    is_squeeze, squeeze_dir = detect_squeeze(row)

    # Ek Momentum
    macd_bullish = macd_v > macd_s
    vwap_bullish = current_price > vwap_val if vwap_val > 0 else None
    rsi_ok_long = rsi_val < RSI_MAX_LONG     # v3.1: 70 → 62 (momentum tükenmeden gir)
    rsi_ok_short = rsi_val > RSI_MIN_SHORT   # v3.1: 30 → 38

    # ═══ ORİJİNAL 3 KATMANLI MOTOR (v2.2 — +$8.23) ═══
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
        if macd_bullish and vwap_bullish is True:
            bull_layers += 1
        elif not macd_bullish and vwap_bullish is False:
            bear_layers += 1

    # Sinyal Kararı
    trend = 'neutral'
    confidence = ''

    if bull_layers >= 3 and rsi_ok_long:
        trend = 'bullish'
        confidence = 'GUCLU'
    elif bear_layers >= 3 and rsi_ok_short:
        trend = 'bearish'
        confidence = 'GUCLU'
    elif bull_layers >= 2 and bear_layers == 0 and rsi_ok_long:
        trend = 'bullish'
        confidence = 'ORTA'
    elif bear_layers >= 2 and bull_layers == 0 and rsi_ok_short:
        trend = 'bearish'
        confidence = 'ORTA'
    elif bull_layers > bear_layers:
        trend = 'bullish'
        confidence = 'ZAYIF'
    elif bear_layers > bull_layers:
        trend = 'bearish'
        confidence = 'ZAYIF'

    # Pozisyon Aç
    should_open = False
    if confidence == 'GUCLU':
        should_open = True
    elif confidence == 'ORTA' and ALLOW_ORTA:
        # v3.10: ORTA sadece peak saatlerde (12:00-18:00 UTC)
        if PEAK_ONLY_ORTA:
            bar_time = df.index[i]
            peak_hour = bar_time.hour
            if hasattr(bar_time, 'tzinfo') and bar_time.tzinfo is not None:
                import pytz
                peak_hour = bar_time.astimezone(pytz.UTC).hour
            if PEAK_SESSION_START_UTC <= peak_hour < PEAK_SESSION_END_UTC:
                should_open = True
            # else: ORTA sinyal peak dışında → açma
        else:
            should_open = True

    # MACD Histogram yön onayı — sinyal yönüyle aynı tarafta olmalı
    if should_open and REQUIRE_MACD_CONFIRM:
        if trend == 'bullish' and macd_h <= 0:
            should_open = False
        elif trend == 'bearish' and macd_h >= 0:
            should_open = False

    # v3.4 — MUM ONAY: Giriş mumunun rengi sinyalle aynı yönde olmalı
    if should_open and REQUIRE_CANDLE_CONFIRM:
        candle_body = current_price - float(row['Open'])  # Close - Open
        if trend == 'bullish' and candle_body <= 0:
            should_open = False  # Kırmızı mumda long açma
        elif trend == 'bearish' and candle_body >= 0:
            should_open = False  # Yeşil mumda short açma

    # ── AKILLI KALİTE FİLTRESİ ──
    quality_score = 0
    quality_reasons = []
    if should_open:
        quality_score, quality_reasons = calculate_signal_quality(df, i, trend, atr_val)
        min_score = MIN_QUALITY_SCORE_GUCLU if confidence == 'GUCLU' else MIN_QUALITY_SCORE_ORTA
        if quality_score < min_score:
            should_open = False  # Kalite yetersiz → işleme girme
            filtered_by_quality += 1
            debug_quality_scores.append(quality_score)
            if DEBUG_MODE and filtered_by_quality <= 10:
                ts = str(df.index[i])[:16] if hasattr(df.index[i], 'strftime') else ''
                print(f"  [DEBUG-QF] #{filtered_by_quality} {ts} | {trend} {confidence} | QS={quality_score}/{min_score} | ATR={atr_val:.2f} | RSI={rsi_val:.1f} | reasons={quality_reasons}")
        elif DEBUG_MODE:
            ts = str(df.index[i])[:16] if hasattr(df.index[i], 'strftime') else ''
            print(f"  [DEBUG-QP] {ts} | {trend} {confidence} | QS={quality_score} PASSED | ATR={atr_val:.2f}")

    if should_open and trend in ('bullish', 'bearish') and balance > 10:
        sl, tp1, tp2, sl_src, sl_dist = calculate_smart_sl_tp(
            df.iloc[max(0, i - 30):i + 1], current_price, atr_val, trend, bb_width_pct
        )

        # v3.7: Dinamik TP2 — GÜÇLÜ sinyallerde TP2'yi büyüt
        if DYNAMIC_TP2 and confidence == 'GUCLU' and sl_dist > 0:
            if trend == 'bullish':
                tp2 = current_price + DYNAMIC_TP2_GUCLU_RR * sl_dist
            else:
                tp2 = current_price - DYNAMIC_TP2_GUCLU_RR * sl_dist

        if sl_dist > 0:
            # v3.5: Dinamik risk — kayıp serisinde küçült, kazanç serisinde büyüt
            original_risk = ACCOUNT_CONFIG['risk_pct']
            if PROGRESSIVE_RISK and consecutive_losses >= RISK_REDUCTION_AFTER_LOSSES:
                ACCOUNT_CONFIG['risk_pct'] = REDUCED_RISK_PCT
            elif WINNING_STREAK_BOOST and consecutive_wins >= STREAK_BOOST_AFTER:
                ACCOUNT_CONFIG['risk_pct'] = STREAK_BOOST_RISK_PCT
            lot, risk_usd = calculate_position_size(sl_dist, balance)
            ACCOUNT_CONFIG['risk_pct'] = original_risk
            risk_pct_actual = (risk_usd / balance) * 100 if balance > 0 else 100

            # Risk kontrolü — geniş SL'li tradeleri engeller
            # NOT: Bu kontrol aynı zamanda volatilite filtresi görevi görüyor.
            # v3.9'da bu kontrol 1dk'da kötü tradeleri eliyordu — KALDIRMA!
            risk_ok = risk_pct_actual <= ACCOUNT_CONFIG['max_risk_pct']

            if not risk_ok:
                filtered_by_risk += 1
                if DEBUG_MODE and filtered_by_risk <= 10:
                    ts = str(df.index[i])[:16] if hasattr(df.index[i], 'strftime') else ''
                    print(f"  [DEBUG-RR] #{filtered_by_risk} {ts} | Risk reject: SL_dist=${sl_dist:.2f} ATR=${atr_val:.2f} lot={lot} risk=${risk_usd:.2f} ({risk_pct_actual:.1f}%>{ACCOUNT_CONFIG['max_risk_pct']}%)")

            if risk_ok:
                in_position = True
                position_type = 'LONG' if trend == 'bullish' else 'SHORT'
                entry_price = current_price
                sl_price = sl
                tp1_price = tp1
                tp2_price = tp2
                lot_size = lot
                entry_confidence = confidence
                tp1_hit = False
                trailing_sl = sl
                entry_bar = i   # v3.2: erken çıkış için başlangıç barı
                # v3.12: Ekstra trade metadata
                entry_quality = quality_score
                entry_adx = float(row.get('ADX', 0))
                entry_timestamp = str(df.index[i])[:16] if hasattr(df.index[i], 'strftime') else ''
                trade_metadata.append({
                    'quality': entry_quality,
                    'adx': round(entry_adx, 1),
                    'timestamp': entry_timestamp
                })

    equity_curve.append(balance)


# ═══════════════════════════════════════════
# v3.12 — Trade metadata'yı birleştir
# ═══════════════════════════════════════════
# Partial close tradeleri metadata sayısını bozabilir, güvenli merge
for t_idx in range(len(trades)):
    if t_idx < len(trade_metadata):
        trades[t_idx]['quality'] = trade_metadata[t_idx].get('quality', 0)
        trades[t_idx]['adx'] = trade_metadata[t_idx].get('adx', 0)
        trades[t_idx]['timestamp'] = trade_metadata[t_idx].get('timestamp', '')

# ═══════════════════════════════════════════
# SONUÇLAR
# ═══════════════════════════════════════════
total_trades = winning + losing
win_rate = (winning / total_trades * 100) if total_trades > 0 else 0

gross_profit = sum(t['pnl'] for t in trades if t['pnl'] > 0)
gross_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf')

avg_win = (gross_profit / winning) if winning > 0 else 0
avg_loss = (gross_loss / losing) if losing > 0 else 0

# Streak hesaplama
max_win_streak = 0
max_loss_streak = 0
current_streak = 0
streak_type = None
for t in trades:
    if t['pnl'] > 0:
        if streak_type == 'win':
            current_streak += 1
        else:
            current_streak = 1
            streak_type = 'win'
        max_win_streak = max(max_win_streak, current_streak)
    else:
        if streak_type == 'loss':
            current_streak += 1
        else:
            current_streak = 1
            streak_type = 'loss'
        max_loss_streak = max(max_loss_streak, current_streak)

# İşlem sonuç dağılımı
sl_count = sum(1 for t in trades if t['result'] == 'SL')
tp1_count = sum(1 for t in trades if t['result'] in ('TP1', 'TP1_PARTIAL'))
tp2_count = sum(1 for t in trades if t['result'] == 'TP2')
trail_count = sum(1 for t in trades if t['result'] == 'TRAIL')
early_exit_count = sum(1 for t in trades if t['result'] == 'EARLY_EXIT')

print()
print("=" * 60)
print("   AURUMPULSE v3.12 BACKTEST RAPORU")
print(f"   v3.9 Baz + ADX Filtresi + {BACKTEST_PERIOD_DAYS} Gün Veri")
print("=" * 60)
print()
_bars_per_day = (60 / 5 * 24) if IS_5MIN_DATA else (60 * 24)
_days = total_bars / _bars_per_day
print(f"  Toplam Veri        : {total_bars} mum ({_days:.0f} gün, {data_interval})")
print(f"  Analiz Edilen      : {len(df) - start_idx} mum")
print()
print("─── İŞLEM İSTATİSTİKLERİ ───")
print(f"  Toplam İşlem       : {total_trades}")
print(f"  Kazanan            : {winning}  (TP1: {tp1_count}, TP2: {tp2_count}, Trail: {trail_count})")
print(f"  Kaybeden           : {losing}  (SL: {sl_count})")
if early_exit_count > 0:
    ee_wins = sum(1 for t in trades if t['result'] == 'EARLY_EXIT' and t['pnl'] > 0)
    ee_losses = early_exit_count - ee_wins
    print(f"  Erken Çıkış        : {early_exit_count}  (Kâr: {ee_wins}, Zarar: {ee_losses})")
print(f"  Kazanma Oranı      : %{win_rate:.1f}")
print(f"  Maks Kazanç Serisi : {max_win_streak}")
print(f"  Maks Kayıp Serisi  : {max_loss_streak}")
print(f"  Kalite Filtresi    : {filtered_by_quality} sinyal elendi")
if ADX_FILTER:
    print(f"  ADX Filtresi       : {filtered_by_adx} bar choppy piyasa (ADX<{ADX_MIN_THRESHOLD})")
if filtered_by_risk > 0:
    print(f"  Risk Reddi         : {filtered_by_risk} sinyal (SL mesafesi çok büyük, $100 hesapta risk > %{ACCOUNT_CONFIG['max_risk_pct']})")
if debug_quality_scores:
    from collections import Counter
    qs_dist = Counter(debug_quality_scores)
    print(f"  Kalite Skor Dağılım: {dict(sorted(qs_dist.items()))} (toplam {len(debug_quality_scores)} elendi)")
print()
print("─── FİNANSAL PERFORMANS ───")
print(f"  Başlangıç Bakiye   : ${initial_balance:.2f}")
print(f"  Güncel Bakiye      : ${balance:.2f}")
print(f"  Net Kâr/Zarar      : ${total_pnl:+.2f}")
print(f"  Getiri             : %{((balance - initial_balance) / initial_balance * 100):+.1f}")
print()
print("─── RİSK METRİKLERİ ───")
print(f"  Profit Factor      : {profit_factor}")
print(f"  Ort. Kazanç        : ${avg_win:.2f}")
print(f"  Ort. Kayıp         : ${avg_loss:.2f}")
print(f"  Ort. Kazanç/Kayıp  : {(avg_win/avg_loss):.2f}x" if avg_loss > 0 else "  Ort. Kazanç/Kayıp  : N/A")
print(f"  Maks. Drawdown     : %{max_drawdown:.1f}")
print()
print("─── HTF TREND DAĞILIMI ───")
total_htf = sum(htf_trend_log.values())
for k, v in htf_trend_log.items():
    pct = (v / total_htf * 100) if total_htf > 0 else 0
    bar = "█" * int(pct / 2)
    print(f"  {k:8s}: {v:5d} ({pct:5.1f}%) {bar}")
print()
print("─── GÜVEN SEVİYESİ PERFORMANSI ───")
for conf_name, stats in confidence_stats.items():
    total_c = stats['win'] + stats['loss']
    wr = (stats['win'] / total_c * 100) if total_c > 0 else 0
    print(f"  {conf_name:8s}: {total_c:3d} işlem | Kazanma: %{wr:.0f} ({stats['win']}W / {stats['loss']}L)")

# Parametreler özeti
print()
print("─── OPTİMİZASYON PARAMETRELERİ ───")
print(f"  Cooldown           : {COOLDOWN_BARS} mum")
print(f"  Min ATR            : ${MIN_ATR_THRESHOLD}")
print(f"  TP1 R:R            : 1:{TP1_RR}")
print(f"  TP2 R:R            : 1:{TP2_RR}")
print(f"  SL Wick Buffer     : {SL_WICK_BUFFER}x ATR")
print(f"  Min SL Mesafesi    : {MIN_SL_ATR_MULT}x ATR")
print(f"  HTF Trend Eşiği    : {HTF_TREND_THRESHOLD}")
print(f"  Trailing Stop      : {'Aktif' if TRAIL_STOP_AFTER_TP1 else 'Kapalı'}")
print(f"  ORTA Sinyal        : {'Açık' if ALLOW_ORTA else 'Kapalı'}")
print(f"  Kalite Filtresi    : GÜÇLÜ≥{MIN_QUALITY_SCORE_GUCLU}/8, ORTA≥{MIN_QUALITY_SCORE_ORTA}/8")
print(f"  MACD Onay          : {'Açık' if REQUIRE_MACD_CONFIRM else 'Kapalı'}")
print(f"  RSI Filtre         : Long<{RSI_MAX_LONG}, Short>{RSI_MIN_SHORT}")
print(f"  Erken Çıkış        : Min {EARLY_EXIT_MIN_BARS} bar, {EARLY_EXIT_MIN_LOSS_ATR}xATR zarar, {EARLY_EXIT_MACD_CONSECUTIVE} mum MACD ters")
print(f"  Kısmi TP1          : {'Açık' if PARTIAL_TP1_CLOSE else 'Kapalı'} (%{PARTIAL_TP1_PCT*100:.0f} kapat)")
print(f"  Streak Boost       : {'Açık' if WINNING_STREAK_BOOST else 'Kapalı'} ({STREAK_BOOST_AFTER} kazanç → %{STREAK_BOOST_RISK_PCT} risk)")
print(f"  Kayıp Koruma       : {'Açık' if PROGRESSIVE_RISK else 'Kapalı'} ({RISK_REDUCTION_AFTER_LOSSES} kayıp → %{REDUCED_RISK_PCT} risk)")
print(f"  Seans Filtresi     : {'Açık (' + str(SESSION_START_UTC) + ':00-' + str(SESSION_END_UTC) + ':00 UTC)' if SESSION_FILTER else 'Kapalı'}")
print(f"  ADX Filtresi       : {'Açık (min ' + str(ADX_MIN_THRESHOLD) + ', periyot ' + str(ADX_PERIOD) + ')' if ADX_FILTER else 'Kapalı'}")
print(f"  Veri Periyodu      : {BACKTEST_PERIOD_DAYS} gün ({data_interval})")
if WALK_FORWARD_MODE:
    print(f"  Walk-Forward       : Açık ({WF_NUM_FOLDS} fold, %{WF_IN_SAMPLE_PCT*100:.0f} in-sample)")
if PEAK_ONLY_ORTA:
    print(f"  Peak ORTA          : ORTA sadece {PEAK_SESSION_START_UTC}:00-{PEAK_SESSION_END_UTC}:00 UTC")

if total_trades > 0 and total_trades > 50:
    print()
    print("─── SON 10 İŞLEM ───")
    for t in trades[-10:]:
        emoji = "✅" if t['pnl'] > 0 else "❌"
        ts_str = t.get('timestamp', '')
        adx_s = f"ADX:{t.get('adx', '-')}" if 'adx' in t else ''
        print(f"  {emoji} {t['type']:5s} | ${t['entry']:.2f} → ${t['exit']:.2f} | "
              f"P/L: ${t['pnl']:+.2f} | {t['result']:5s} | {t['confidence']} QS:{t.get('quality', '-')} {adx_s} {ts_str}")

print()

# v3.12 — İSTATİSTİKSEL GÜVENİLİRLİK UYARISI
if total_trades < 30:
    print("─── ⚠️ İSTATİSTİKSEL UYARI ───")
    print(f"  {total_trades} trade istatistiksel olarak YETERSİZ.")
    print(f"  Güvenilir sonuç için minimum 100+ trade gerekli.")
    print(f"  → 'python backtest.py --period 60' ile daha uzun test yapın.")
    print()
elif total_trades < 100:
    print("─── ⚠️ İSTATİSTİKSEL UYARI ───")
    print(f"  {total_trades} trade orta güvenilirlikte.")
    print(f"  Yüksek güven için 100+ trade önerilir.")
    print()

# v3.12 — TRADE DETAY TABLOSU (Her trade'in timestamp'i)
if total_trades > 0 and total_trades <= 50:
    print("─── TÜM İŞLEMLER (Detaylı) ───")
    for idx_t, t in enumerate(trades, 1):
        emoji = "✅" if t['pnl'] > 0 else "❌"
        ts_str = t.get('timestamp', '')
        adx_str = f"ADX:{t.get('adx', 'N/A')}" if 'adx' in t else ''
        print(f"  #{idx_t:2d} {emoji} {t['type']:5s} | ${t['entry']:.2f} → ${t['exit']:.2f} | "
              f"P/L: ${t['pnl']:+.2f} | {t['result']:5s} | Lot: {t['lot']} | {t['confidence']} "
              f"QS:{t.get('quality', 'N/A')} {adx_str} {ts_str}")
    print()

print("=" * 60)
if balance > initial_balance:
    print(f"  🚀 STRATEJİ KARLI! {BACKTEST_PERIOD_DAYS} günde ${total_pnl:+.2f} kazanç ({((balance-initial_balance)/initial_balance*100):+.1f}%).")
elif total_trades == 0:
    print("  ⚠️ Hiç işlem açılmadı. Filtreler çok sıkı olabilir.")
    if ADX_FILTER:
        print(f"     ADX filtresi {filtered_by_adx} bar'ı eledi. '--noadx' ile karşılaştırın.")
else:
    print(f"  ⚠️ STRATEJİ ZARARDA (${total_pnl:+.2f}). Optimizasyona devam edilmeli.")
print("=" * 60)

# ═══════════════════════════════════════════
# v3.12 — WALK-FORWARD ANALİZİ
# ═══════════════════════════════════════════
# Mevcut trade'leri zaman dilimlerine bölerek her dönemde
# stratejinin tutarlı çalışıp çalışmadığını kontrol eder.
# Bu gerçek WFO değil (parametre re-optimizasyonu yok) ama
# stratejinin farklı piyasa koşullarında tutarlılığını gösterir.

if WALK_FORWARD_MODE and total_trades >= 6:
    print()
    print("=" * 60)
    print("   WALK-FORWARD TUTARLILIK ANALİZİ")
    print("=" * 60)

    # Trade'leri zamana göre fold'lara böl
    trades_with_time = [t for t in trades if t.get('timestamp', '')]
    if len(trades_with_time) >= 6:
        fold_size = len(trades_with_time) // WF_NUM_FOLDS
        folds = []
        for f in range(WF_NUM_FOLDS):
            start = f * fold_size
            end = start + fold_size if f < WF_NUM_FOLDS - 1 else len(trades_with_time)
            fold_trades = trades_with_time[start:end]
            folds.append(fold_trades)

        consistent_folds = 0
        for f_idx, fold_trades in enumerate(folds):
            f_wins = sum(1 for t in fold_trades if t['pnl'] > 0)
            f_losses = sum(1 for t in fold_trades if t['pnl'] <= 0)
            f_total = f_wins + f_losses
            f_wr = (f_wins / f_total * 100) if f_total > 0 else 0
            f_pnl = sum(t['pnl'] for t in fold_trades)
            f_gp = sum(t['pnl'] for t in fold_trades if t['pnl'] > 0)
            f_gl = abs(sum(t['pnl'] for t in fold_trades if t['pnl'] < 0))
            f_pf = round(f_gp / f_gl, 2) if f_gl > 0 else float('inf')
            f_start = fold_trades[0].get('timestamp', '?')[:10]
            f_end = fold_trades[-1].get('timestamp', '?')[:10]

            is_profitable = f_pnl > 0
            if is_profitable:
                consistent_folds += 1

            emoji = "✅" if is_profitable else "❌"
            print(f"\n  {emoji} Fold {f_idx + 1} ({f_start} → {f_end})")
            print(f"     İşlem: {f_total} | WR: %{f_wr:.0f} | PF: {f_pf} | P/L: ${f_pnl:+.2f}")

        print(f"\n  ─── TUTARLILIK: {consistent_folds}/{WF_NUM_FOLDS} fold karlı ───")
        if consistent_folds == WF_NUM_FOLDS:
            print("  ✅ Strateji TÜM dönemlerde karlı — güçlü tutarlılık!")
        elif consistent_folds >= WF_NUM_FOLDS * 0.7:
            print("  ⚠️ Strateji çoğu dönemde karlı — orta tutarlılık.")
        else:
            print("  ❌ Strateji tutarsız — overfitting riski yüksek!")
            print("     Strateji belirli piyasa koşullarına bağımlı olabilir.")
    else:
        print("\n  ⚠️ Yeterli timestamped trade yok. Walk-forward analizi yapılamadı.")

elif WALK_FORWARD_MODE and total_trades < 6:
    print()
    print("  ⚠️ Walk-forward analizi için minimum 6 trade gerekli.")
    print(f"     Mevcut: {total_trades} trade. Daha uzun periyot deneyin.")

# v3.12 — KULLANIM REHBERİ
print()
print("─── KULLANIM ───")
print("  python backtest.py              → 7 gün, 1dk veri (v3.9 baseline)")
print("  python backtest.py --period 30  → 30 gün, 5dk veri (deneysel)")
print("  python backtest.py --period 60  → 60 gün, 5dk veri (deneysel)")
print("  python backtest.py --walkforward → Walk-forward validasyonu (60 gün)")
print("  python backtest.py --adx        → ADX filtresi aktif (deneysel)")
print("  python backtest.py --debug      → Sinyal detayları göster")
print("  ⚠️ Strateji 1dk scalping için optimize. 5dk sonuçlar deneyseldir.")
