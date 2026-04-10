"""
AurumPulse Backtest v4.1 — Chart Reader
═══════════════════════════════════════════════════════
GRAFİK OKUMA STRATEJİSİ:
  Bir trader gibi grafik okur:
  1) Otomatik S/R seviyeleri tespit (fiyat kümelenmesi)
  2) Trend yapısı analizi (HH/HL veya LH/LL)
  3) S/R seviyesinde dönüş mum kalıbı tespiti (pin bar, engulfing, doji)
  4) Çoklu onay ile giriş → yüksek WR

TIMEFRAME AGNOSTIK:
  Her parametre fiyat bazlı (ATR oranı). 1dk, 5dk, 15dk → aynı mantık.
  Veri ne gelirse gelsin adapte olur.

HEDEF: %70+ WR — sadece en temiz setup'larda trade aç
"""

import pandas as pd
import numpy as np
import yfinance as yf
import sys
import warnings
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════
# HESAP AYARLARI
# ═══════════════════════════════════════════
ACCOUNT_CONFIG = {
    'balance': 100.0,
    'risk_pct': 2.0,
    'max_risk_pct': 5.0,
    'contract_size': 100,
    'min_lot': 0.01,
    'max_lot': 0.05,
}

# ═══════════════════════════════════════════
# v4.1 CHART READER PARAMETRELERİ
# ═══════════════════════════════════════════

# S/R tespit
SR_LOOKBACK_MULT = 200      # Daha uzun lookback = daha güvenilir S/R (100→200)
SR_CLUSTER_ATR = 0.8         # Kümeleme toleransı genişletildi (0.5→0.8)
SR_MIN_TOUCHES = 3           # Min 3 dokunuş = daha güçlü seviye (2→3)
SR_PROXIMITY_ATR = 0.3       # Fiyat S/R'ye bu kadar yakınsa "seviyede" say

# Mum kalıpları
PIN_BAR_RATIO = 0.3          # Gövde/toplam oran < bu = pin bar
ENGULF_MIN_BODY_RATIO = 0.6  # Engulfing için min gövde oranı

# Çıkış kuralları
TP_ATR_MULT = 1.0            # TP = 1.0x ATR (hızlı kâr al)
SL_ATR_MULT = 1.2            # SL = 1.2x ATR (5dk'da ATR büyük, dar tutmak lazım)
TIME_STOP_MINUTES = 60       # Zaman stop (45→60dk, dönüşe zaman ver)
BREAKEVEN_TRIGGER = 0.80     # TP'ye %80 yaklaşınca SL → entry (0.60→0.80, erken BE önle)

# Counter-trend kontrolü
ALLOW_COUNTER_TREND = False  # Counter-trend trade'ler kapalı (hep kaybediyordu)

# Trend yapısı
TREND_SWING_LOOKBACK = 50    # Swing tespit penceresi (30→50, daha geniş bakış)
TREND_MIN_SWINGS = 3         # Trend tespiti için minimum swing sayısı

# Genel filtreler
COOLDOWN_MINUTES = 10        # İki trade arası min bekleme (5→10dk, overtrading önle)
LOSS_COOLDOWN_MINUTES = 30   # 3 kayıp sonrası bekleme (20→30dk)
MAX_CONSECUTIVE_LOSSES = 3
SESSION_FILTER = True
SESSION_START_UTC = 10
SESSION_END_UTC = 20

# Risk yönetimi
PROGRESSIVE_RISK = True
RISK_REDUCTION_AFTER_LOSSES = 3
REDUCED_RISK_PCT = 1.0
WINNING_STREAK_BOOST = True
STREAK_BOOST_AFTER = 2
STREAK_BOOST_RISK_PCT = 3.0

# ═══ KOMUT SATIRI ═══
BACKTEST_PERIOD_DAYS = 30     # DEFAULT: 30 gün / 5dk (artık 1dk'ya bağlı değil)
IS_5MIN_DATA = True           # Default: 5dk veri (1dk bağımlılığı kaldırıldı)

if '--period' in sys.argv:
    try:
        idx = sys.argv.index('--period')
        BACKTEST_PERIOD_DAYS = int(sys.argv[idx + 1])
    except (IndexError, ValueError):
        pass

# 7 gün ve altı = 1dk, üstü = 5dk (yfinance limiti)
IS_5MIN_DATA = BACKTEST_PERIOD_DAYS > 7

DEBUG_MODE = '--debug' in sys.argv


# ═══════════════════════════════════════════
# İNDİKATÖR HESAPLAMA (minimal — grafik okuma ağırlıklı)
# ═══════════════════════════════════════════
def calculate_indicators(df):
    close = df['Close']
    d = close.diff()

    # RSI (trend teyidi için)
    gain = d.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(com=13, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = (100 - 100 / (1 + rs)).fillna(50)

    # MACD (momentum teyidi için)
    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    df['MACD'] = ema_fast - ema_slow
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

    # ATR (her şeyin temeli)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - close.shift()).abs(),
        (df['Low'] - close.shift()).abs()
    ], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()

    # EMA'lar (trend yönü)
    df['EMA20'] = close.ewm(span=20, adjust=False).mean()
    df['EMA50'] = close.ewm(span=50, adjust=False).mean()

    return df


# ═══════════════════════════════════════════
# KATMAN 1: OTOMATİK S/R TESPİTİ
# ═══════════════════════════════════════════
def detect_sr_levels(df, idx, atr_val, bar_interval_min):
    """
    Fiyat kümelenmesi ile S/R seviyeleri tespit et.
    Geçmişte birden fazla kez dokunulan fiyat bölgeleri = S/R.
    """
    # Lookback'i timeframe'e göre ölçekle
    lookback = min(SR_LOOKBACK_MULT, idx)
    if lookback < 30:
        return [], []

    chunk = df.iloc[max(0, idx - lookback):idx]
    highs = chunk['High'].values
    lows = chunk['Low'].values
    cluster_dist = SR_CLUSTER_ATR * atr_val

    # Tüm swing high/low'ları topla
    price_levels = []
    for i in range(2, len(highs) - 2):
        # Swing high
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            price_levels.append(float(highs[i]))
        # Swing low
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            price_levels.append(float(lows[i]))

    if len(price_levels) < 2:
        return [], []

    # Kümeleme: yakın fiyatları grupla
    price_levels.sort()
    clusters = []
    current_cluster = [price_levels[0]]

    for p in price_levels[1:]:
        if p - current_cluster[-1] <= cluster_dist:
            current_cluster.append(p)
        else:
            if len(current_cluster) >= SR_MIN_TOUCHES:
                clusters.append(np.mean(current_cluster))
            current_cluster = [p]
    if len(current_cluster) >= SR_MIN_TOUCHES:
        clusters.append(np.mean(current_cluster))

    current_price = float(df.iloc[idx]['Close'])

    # Destek = altındaki seviyeler, Direnç = üstündeki
    supports = sorted([c for c in clusters if c < current_price], reverse=True)
    resistances = sorted([c for c in clusters if c > current_price])

    return supports, resistances


# ═══════════════════════════════════════════
# KATMAN 2: TREND YAPISI (HH/HL veya LH/LL)
# ═══════════════════════════════════════════
def detect_trend_structure(df, idx):
    """
    Swing highs ve swing lows ile trend yapısını belirle.
    HH + HL = uptrend, LH + LL = downtrend
    """
    lookback = min(TREND_SWING_LOOKBACK, idx - 2)
    if lookback < 10:
        return 'neutral', 0

    chunk = df.iloc[max(0, idx - lookback):idx + 1]
    highs = chunk['High'].values
    lows = chunk['Low'].values

    swing_highs = []
    swing_lows = []

    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            swing_highs.append(float(highs[i]))
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            swing_lows.append(float(lows[i]))

    if len(swing_highs) < TREND_MIN_SWINGS or len(swing_lows) < TREND_MIN_SWINGS:
        return 'neutral', 0

    # Son 3 swing'i karşılaştır
    recent_highs = swing_highs[-3:]
    recent_lows = swing_lows[-3:]

    hh_count = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i] > recent_highs[i-1])
    hl_count = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i] > recent_lows[i-1])
    lh_count = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i] < recent_highs[i-1])
    ll_count = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i] < recent_lows[i-1])

    bull_score = hh_count + hl_count
    bear_score = lh_count + ll_count

    if bull_score >= 3:
        return 'bullish', bull_score
    elif bear_score >= 3:
        return 'bearish', bear_score
    elif bull_score >= 2 and bear_score == 0:
        return 'bullish', bull_score
    elif bear_score >= 2 and bull_score == 0:
        return 'bearish', bear_score

    return 'neutral', 0


# ═══════════════════════════════════════════
# KATMAN 3: MUM KALIBI TESPİTİ
# ═══════════════════════════════════════════
def detect_candle_pattern(df, idx):
    """
    Dönüş mum kalıplarını tespit et:
    - Pin Bar (hammer/shooting star): uzun fitil + küçük gövde
    - Engulfing: önceki mumu tamamen yutan güçlü mum
    - Doji: çok küçük gövde = kararsızlık
    Returns: ('bullish'/'bearish'/'neutral', pattern_name, strength)
    """
    if idx < 1:
        return 'neutral', '', 0

    curr = df.iloc[idx]
    prev = df.iloc[idx - 1]

    c_open = float(curr['Open'])
    c_close = float(curr['Close'])
    c_high = float(curr['High'])
    c_low = float(curr['Low'])
    c_range = c_high - c_low
    c_body = c_close - c_open
    c_body_abs = abs(c_body)

    p_open = float(prev['Open'])
    p_close = float(prev['Close'])
    p_body = p_close - p_open

    if c_range < 0.01:
        return 'neutral', '', 0

    body_ratio = c_body_abs / c_range

    # ── PIN BAR (Hammer / Shooting Star) ──
    if body_ratio < PIN_BAR_RATIO:
        upper_wick = c_high - max(c_open, c_close)
        lower_wick = min(c_open, c_close) - c_low

        # Bullish pin bar (hammer): uzun alt fitil
        if lower_wick > 2 * upper_wick and lower_wick > 0.6 * c_range:
            return 'bullish', 'PIN_BAR', 2

        # Bearish pin bar (shooting star): uzun üst fitil
        if upper_wick > 2 * lower_wick and upper_wick > 0.6 * c_range:
            return 'bearish', 'PIN_BAR', 2

    # ── ENGULFING ──
    if body_ratio > ENGULF_MIN_BODY_RATIO:
        # Bullish engulfing: önceki kırmızı, şimdiki yeşil ve yutmuş
        if p_body < 0 and c_body > 0 and c_body_abs > abs(p_body):
            return 'bullish', 'ENGULFING', 3

        # Bearish engulfing
        if p_body > 0 and c_body < 0 and c_body_abs > abs(p_body):
            return 'bearish', 'ENGULFING', 3

    # ── DOJI (kararsızlık = potansiyel dönüş) ──
    if body_ratio < 0.15:
        # Doji tek başına yön vermez, kontekste göre yön belirlenir
        return 'neutral', 'DOJI', 1

    return 'neutral', '', 0


# ═══════════════════════════════════════════
# ANA SİNYAL MOTORU — GRAFİK OKUMA
# ═══════════════════════════════════════════
def generate_chart_signal(df, idx, atr_val, bar_interval_min):
    """
    Bir trader gibi grafik okur:
    1. S/R seviyesine yakınlık var mı?
    2. Trend yapısı ne diyor?
    3. Dönüş mum kalıbı var mı?
    4. Momentum (MACD/RSI) teyit ediyor mu?

    Sadece 3+ onay varsa trade aç → yüksek WR
    """
    row = df.iloc[idx]
    close = float(row['Close'])
    rsi = float(row['RSI'])
    macd_h = float(row['MACD_Hist'])
    ema20 = float(row['EMA20'])
    ema50 = float(row['EMA50'])

    score = 0
    reasons = []
    trend = 'neutral'

    # ── 1. S/R SEVİYESİ ANALİZİ ──
    supports, resistances = detect_sr_levels(df, idx, atr_val, bar_interval_min)
    proximity = SR_PROXIMITY_ATR * atr_val
    at_support = False
    at_resistance = False
    sr_level = 0

    if supports and (close - supports[0]) <= proximity:
        at_support = True
        sr_level = supports[0]
        score += 2
        reasons.append(f"S/R_destek(${sr_level:.1f})")

    if resistances and (resistances[0] - close) <= proximity:
        at_resistance = True
        sr_level = resistances[0]
        score += 2
        reasons.append(f"S/R_direnç(${sr_level:.1f})")

    # S/R'de değilse → sinyal yok
    if not at_support and not at_resistance:
        return 'neutral', 0, ['S/R_yok']

    # ── 2. TREND YAPISI ──
    trend_dir, trend_score = detect_trend_structure(df, idx)

    # Destek + uptrend = güçlü long, Direnç + downtrend = güçlü short
    # Destek + downtrend = riskli (trend kırılımı olabilir)
    if at_support and trend_dir == 'bullish':
        score += 2
        reasons.append("Trend_destek_uyumu")
        trend = 'bullish'
    elif at_resistance and trend_dir == 'bearish':
        score += 2
        reasons.append("Trend_direnç_uyumu")
        trend = 'bearish'
    elif at_support and trend_dir == 'neutral':
        score += 1
        reasons.append("Destek_nötr_trend")
        trend = 'bullish'
    elif at_resistance and trend_dir == 'neutral':
        score += 1
        reasons.append("Direnç_nötr_trend")
        trend = 'bearish'
    elif at_support and trend_dir == 'bearish':
        if not ALLOW_COUNTER_TREND:
            return 'neutral', 0, ['Counter_trend_bloklandı']
        score += 0
        reasons.append("Destek_counter_trend")
        trend = 'bullish'
    elif at_resistance and trend_dir == 'bullish':
        if not ALLOW_COUNTER_TREND:
            return 'neutral', 0, ['Counter_trend_bloklandı']
        score += 0
        reasons.append("Direnç_counter_trend")
        trend = 'bearish'

    # ── 3. MUM KALIBI ──
    candle_dir, candle_pattern, candle_strength = detect_candle_pattern(df, idx)

    if candle_pattern:
        if candle_dir == trend:
            score += candle_strength
            reasons.append(f"{candle_pattern}_uyumlu")
        elif candle_dir == 'neutral' and candle_pattern == 'DOJI':
            score += 1
            reasons.append("DOJI_dönüş")
        # Ters yönlü mum kalıbı → sinyal zayıflar
        elif candle_dir != 'neutral' and candle_dir != trend:
            score -= 1
            reasons.append(f"{candle_pattern}_ters")

    # ── 4. MOMENTUM TEYİDİ ──
    # MACD histogram yönü
    if idx >= 2:
        hist_prev = float(df.iloc[idx - 1]['MACD_Hist'])
        if trend == 'bullish' and macd_h > hist_prev:
            score += 1
            reasons.append("MACD_dönüş↑")
        elif trend == 'bearish' and macd_h < hist_prev:
            score += 1
            reasons.append("MACD_dönüş↓")

    # RSI uyumu (aşırılık değil, yön teyidi)
    if trend == 'bullish' and rsi < 50:
        score += 1
        reasons.append("RSI_uygun↑")
    elif trend == 'bearish' and rsi > 50:
        score += 1
        reasons.append("RSI_uygun↓")

    # ── 5. EMA YAKINLIK (fiyat EMA'ya yakınsa pullback girişi) ──
    ema_dist = abs(close - ema20) / atr_val if atr_val > 0 else 999
    if ema_dist < 1.0:
        score += 1
        reasons.append("EMA20_yakın")

    return trend, score, reasons


# ═══════════════════════════════════════════
# POZİSYON BOYUTU
# ═══════════════════════════════════════════
def calculate_position_size(sl_distance, balance, risk_pct):
    risk_amount = balance * (risk_pct / 100)
    contract = ACCOUNT_CONFIG['contract_size']
    min_lot = ACCOUNT_CONFIG['min_lot']
    max_lot = ACCOUNT_CONFIG['max_lot']
    if sl_distance <= 0:
        return min_lot, risk_amount
    raw_lot = risk_amount / (sl_distance * contract)
    lot = round(max(min(raw_lot, max_lot), min_lot), 2)
    actual_risk = lot * sl_distance * contract
    return lot, actual_risk


def get_bar_interval_minutes(df):
    if len(df) < 2:
        return 1
    diff = (df.index[1] - df.index[0]).total_seconds() / 60
    return max(1, round(diff))


# ═══════════════════════════════════════════
# ANA BACKTEST
# ═══════════════════════════════════════════
print("=" * 60)
print("   AURUMPULSE v4.1 — CHART READER")
print("   Grafik Okuma + S/R Bounce + Mum Kalıpları")
print("=" * 60)
print()

if BACKTEST_PERIOD_DAYS <= 7:
    dl_interval = "1m"
    dl_period = "7d"
    data_label = "1dk"
    IS_5MIN_DATA = False
    print("📊 Altın (XAU/USD) son 7 günlük 1dk veriler indiriliyor...")
else:
    dl_interval = "5m"
    dl_period = f"{BACKTEST_PERIOD_DAYS}d"
    data_label = "5dk"
    IS_5MIN_DATA = True
    print(f"📊 Altın (XAU/USD) son {BACKTEST_PERIOD_DAYS} günlük 5dk veriler indiriliyor...")

try:
    df = yf.download("GC=F", interval=dl_interval, period=dl_period, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.dropna(inplace=True)
    total_bars = len(df)
    print(f"✅ {total_bars} adet mum yüklendi ({data_label}).\n")
except Exception as e:
    print(f"❌ Veri indirilirken hata: {e}")
    exit()

print("🧮 İndikatörler hesaplanıyor...")
df = calculate_indicators(df)
df.dropna(subset=['RSI', 'MACD', 'ATR', 'EMA20', 'EMA50'], inplace=True)
print(f"✅ İndikatörlü veri: {len(df)} mum\n")

bar_interval_min = get_bar_interval_minutes(df)
print(f"⏱️ Timeframe: {bar_interval_min}dk per bar")

# Zaman bazlı → bar sayısına çevir
cooldown_bars = max(1, COOLDOWN_MINUTES // bar_interval_min)
loss_cooldown_bars = max(1, LOSS_COOLDOWN_MINUTES // bar_interval_min)
time_stop_bars = max(3, TIME_STOP_MINUTES // bar_interval_min)

# Minimum sinyal skoru: %70+ WR için en az 4 puan gerekli
# (S/R=2 + trend_uyum=2 = 4 temel, + mum/momentum bonusları)
MIN_SIGNAL_SCORE = 3

print(f"⚙️ v4.1 Chart Reader parametreleri:")
print(f"   Cooldown: {COOLDOWN_MINUTES}dk = {cooldown_bars} bar")
print(f"   Kayıp cooldown: {LOSS_COOLDOWN_MINUTES}dk = {loss_cooldown_bars} bar")
print(f"   Zaman stop: {TIME_STOP_MINUTES}dk = {time_stop_bars} bar")
print(f"   TP: {TP_ATR_MULT}x ATR | SL: {SL_ATR_MULT}x ATR")
print(f"   Min sinyal skoru: {MIN_SIGNAL_SCORE}")
print(f"   S/R lookback: {SR_LOOKBACK_MULT} bar | Küme: {SR_CLUSTER_ATR}x ATR")
print(f"   Seans: {SESSION_START_UTC}:00-{SESSION_END_UTC}:00 UTC")
print()

# ── Backtest Değişkenleri ──
balance = ACCOUNT_CONFIG['balance']
initial_balance = balance
in_position = False
position_type = None
entry_price = 0
sl_price = 0
tp_price = 0
lot_size = 0
entry_bar = 0
entry_score = 0
entry_timestamp = ''
entry_reasons = []
breakeven_locked = False

last_trade_bar = -999
consecutive_losses = 0
consecutive_wins = 0

trades = []
max_balance = balance
max_drawdown = 0
equity_curve = []

# Filtre sayaçları
filtered_by_sr = 0
filtered_by_score = 0
filtered_by_risk = 0
filtered_by_session = 0

start_idx = max(60, SR_LOOKBACK_MULT + 10)

print("🤖 Chart Reader Motoru simüle ediliyor...\n")

for i in range(start_idx, len(df)):
    row = df.iloc[i]
    close = float(row['Close'])
    high = float(row['High'])
    low = float(row['Low'])
    atr_val = float(row['ATR'])

    # ── Pozisyon Takibi ──
    if in_position:
        bars_in = i - entry_bar

        # Breakeven lock
        if not breakeven_locked:
            tp_dist_total = abs(tp_price - entry_price)
            if tp_dist_total > 0:
                if position_type == 'LONG':
                    progress = (close - entry_price) / tp_dist_total
                else:
                    progress = (entry_price - close) / tp_dist_total
                if progress >= BREAKEVEN_TRIGGER:
                    sl_price = entry_price
                    breakeven_locked = True

        # SL check
        hit_sl = False
        if position_type == 'LONG' and low <= sl_price:
            hit_sl = True
            exit_price = sl_price
        elif position_type == 'SHORT' and high >= sl_price:
            hit_sl = True
            exit_price = sl_price

        if hit_sl:
            if position_type == 'LONG':
                pnl = (exit_price - entry_price) * lot_size * ACCOUNT_CONFIG['contract_size']
            else:
                pnl = (entry_price - exit_price) * lot_size * ACCOUNT_CONFIG['contract_size']
            balance += pnl
            result = 'BE' if breakeven_locked and abs(pnl) < 0.5 else ('SL' if pnl < 0 else 'WIN')
            if pnl > 0:
                consecutive_losses = 0
                consecutive_wins += 1
            elif pnl < -0.01:
                consecutive_losses += 1
                consecutive_wins = 0
            trades.append({'type': position_type, 'entry': entry_price, 'exit': round(exit_price, 2),
                          'pnl': round(pnl, 2), 'result': result, 'lot': lot_size,
                          'score': entry_score, 'timestamp': entry_timestamp,
                          'bars': bars_in, 'reasons': entry_reasons})
            in_position = False
            last_trade_bar = i

        # TP check
        elif (position_type == 'LONG' and high >= tp_price) or \
             (position_type == 'SHORT' and low <= tp_price):
            exit_price = tp_price
            if position_type == 'LONG':
                pnl = (exit_price - entry_price) * lot_size * ACCOUNT_CONFIG['contract_size']
            else:
                pnl = (entry_price - exit_price) * lot_size * ACCOUNT_CONFIG['contract_size']
            balance += pnl
            consecutive_losses = 0
            consecutive_wins += 1
            trades.append({'type': position_type, 'entry': entry_price, 'exit': round(exit_price, 2),
                          'pnl': round(pnl, 2), 'result': 'TP', 'lot': lot_size,
                          'score': entry_score, 'timestamp': entry_timestamp,
                          'bars': bars_in, 'reasons': entry_reasons})
            in_position = False
            last_trade_bar = i

        # Time stop
        elif bars_in >= time_stop_bars:
            if position_type == 'LONG':
                pnl = (close - entry_price) * lot_size * ACCOUNT_CONFIG['contract_size']
            else:
                pnl = (entry_price - close) * lot_size * ACCOUNT_CONFIG['contract_size']
            balance += pnl
            if pnl > 0:
                consecutive_losses = 0
                consecutive_wins += 1
            elif pnl < -0.01:
                consecutive_losses += 1
                consecutive_wins = 0
            trades.append({'type': position_type, 'entry': entry_price, 'exit': round(close, 2),
                          'pnl': round(pnl, 2), 'result': 'TIME', 'lot': lot_size,
                          'score': entry_score, 'timestamp': entry_timestamp,
                          'bars': bars_in, 'reasons': entry_reasons})
            in_position = False
            last_trade_bar = i

        if balance > max_balance:
            max_balance = balance
        dd = (max_balance - balance) / max_balance * 100 if max_balance > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd
        equity_curve.append(balance)
        continue

    # ── COOLDOWN ──
    req_cooldown = loss_cooldown_bars if consecutive_losses >= MAX_CONSECUTIVE_LOSSES else cooldown_bars
    if (i - last_trade_bar) < req_cooldown:
        equity_curve.append(balance)
        continue

    # ── SESSION FİLTRESİ ──
    if SESSION_FILTER:
        bar_time = df.index[i]
        if hasattr(bar_time, 'hour'):
            hour = bar_time.hour
            if hasattr(bar_time, 'tzinfo') and bar_time.tzinfo is not None:
                import pytz
                hour = bar_time.astimezone(pytz.UTC).hour
            if hour < SESSION_START_UTC or hour >= SESSION_END_UTC:
                filtered_by_session += 1
                equity_curve.append(balance)
                continue

    # ── ATR MİNİMUM ──
    if atr_val < 0.2:
        equity_curve.append(balance)
        continue

    # ── SİNYAL ÜRETİMİ: GRAFİK OKUMA ──
    trend, score, reasons = generate_chart_signal(df, i, atr_val, bar_interval_min)

    if trend == 'neutral':
        if 'S/R_yok' in reasons:
            filtered_by_sr += 1
        else:
            filtered_by_score += 1
        equity_curve.append(balance)
        continue

    # Minimum skor kontrolü
    if score < MIN_SIGNAL_SCORE:
        filtered_by_score += 1
        if DEBUG_MODE and filtered_by_score <= 10:
            ts = str(df.index[i])[:16]
            print(f"  [DEBUG-QF] {ts} | {trend} score={score}<{MIN_SIGNAL_SCORE} | {reasons}")
        equity_curve.append(balance)
        continue

    # ── SL / TP HESAPLA ──
    sl_distance = SL_ATR_MULT * atr_val
    tp_distance = TP_ATR_MULT * atr_val

    if trend == 'bullish':
        sl = close - sl_distance
        tp = close + tp_distance
    else:
        sl = close + sl_distance
        tp = close - tp_distance

    # ── RİSK HESAPLA ──
    current_risk_pct = ACCOUNT_CONFIG['risk_pct']
    if PROGRESSIVE_RISK and consecutive_losses >= RISK_REDUCTION_AFTER_LOSSES:
        current_risk_pct = REDUCED_RISK_PCT
    elif WINNING_STREAK_BOOST and consecutive_wins >= STREAK_BOOST_AFTER:
        current_risk_pct = STREAK_BOOST_RISK_PCT

    lot, risk_usd = calculate_position_size(sl_distance, balance, current_risk_pct)
    risk_pct_actual = (risk_usd / balance) * 100 if balance > 0 else 100

    # Min lot ile risk aşılıyorsa yine de izin ver (küçük hesap gerçeği)
    # Risk kontrolü sadece büyük lot pozisyonları engeller
    if risk_pct_actual > ACCOUNT_CONFIG['max_risk_pct'] and lot > ACCOUNT_CONFIG['min_lot']:
        filtered_by_risk += 1
        equity_curve.append(balance)
        continue

    # ── POZİSYON AÇ ──
    if balance > 10:
        in_position = True
        position_type = 'LONG' if trend == 'bullish' else 'SHORT'
        entry_price = close
        sl_price = sl
        tp_price = tp
        lot_size = lot
        entry_bar = i
        entry_score = score
        breakeven_locked = False
        entry_timestamp = str(df.index[i])[:16] if hasattr(df.index[i], 'strftime') else ''
        entry_reasons = reasons.copy()

        if DEBUG_MODE:
            print(f"  [OPEN] {entry_timestamp} | {position_type} @ ${close:.2f} | "
                  f"SL=${sl:.2f} TP=${tp:.2f} | Score={score} | {reasons}")

    equity_curve.append(balance)


# ═══════════════════════════════════════════
# SONUÇLAR
# ═══════════════════════════════════════════
total_trades = len(trades)
winning = sum(1 for t in trades if t['pnl'] > 0)
losing = sum(1 for t in trades if t['pnl'] < 0)
be_count = sum(1 for t in trades if abs(t['pnl']) < 0.01)
win_rate = (winning / total_trades * 100) if total_trades > 0 else 0

gross_profit = sum(t['pnl'] for t in trades if t['pnl'] > 0)
gross_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf')

avg_win = (gross_profit / winning) if winning > 0 else 0
avg_loss = (gross_loss / losing) if losing > 0 else 0

max_win_streak = 0
max_loss_streak = 0
cs = 0
st = None
for t in trades:
    if t['pnl'] > 0:
        if st == 'w': cs += 1
        else: cs = 1; st = 'w'
        max_win_streak = max(max_win_streak, cs)
    elif t['pnl'] < 0:
        if st == 'l': cs += 1
        else: cs = 1; st = 'l'
        max_loss_streak = max(max_loss_streak, cs)

tp_count = sum(1 for t in trades if t['result'] == 'TP')
sl_count = sum(1 for t in trades if t['result'] == 'SL')
be_result = sum(1 for t in trades if t['result'] == 'BE')
time_count = sum(1 for t in trades if t['result'] == 'TIME')
win_other = sum(1 for t in trades if t['result'] == 'WIN')

print()
print("=" * 60)
print("   AURUMPULSE v4.1 BACKTEST RAPORU")
print("   Chart Reader | S/R Bounce + Mum Kalıpları")
print("=" * 60)
print()
_bpd = (60 / bar_interval_min * 24)
_days = total_bars / _bpd
print(f"  Toplam Veri        : {total_bars} mum ({_days:.0f} gün, {data_label})")
print(f"  Analiz Edilen      : {len(df) - start_idx} mum")
print(f"  Bar Aralığı        : {bar_interval_min}dk")
print()
print("─── İŞLEM İSTATİSTİKLERİ ───")
print(f"  Toplam İşlem       : {total_trades}")
print(f"  Kazanan            : {winning}  (TP: {tp_count}, Diğer: {win_other})")
print(f"  Kaybeden           : {losing}  (SL: {sl_count})")
print(f"  Breakeven          : {be_result}")
print(f"  Zaman Stop         : {time_count}")
print(f"  Kazanma Oranı      : %{win_rate:.1f}")
print(f"  Maks Kazanç Serisi : {max_win_streak}")
print(f"  Maks Kayıp Serisi  : {max_loss_streak}")
print()
print("─── FİLTRE İSTATİSTİKLERİ ───")
print(f"  S/R'de değil       : {filtered_by_sr} bar")
print(f"  Skor yetersiz      : {filtered_by_score}")
print(f"  Risk reddi         : {filtered_by_risk}")
print(f"  Seans dışı         : {filtered_by_session} bar")
print()
print("─── FİNANSAL PERFORMANS ───")
print(f"  Başlangıç Bakiye   : ${initial_balance:.2f}")
print(f"  Güncel Bakiye      : ${balance:.2f}")
net = balance - initial_balance
print(f"  Net Kâr/Zarar      : ${'+' if net>=0 else ''}{net:.2f}")
print(f"  Getiri             : %{'+' if net>=0 else ''}{net/initial_balance*100:.1f}")
print()
print("─── RİSK METRİKLERİ ───")
print(f"  Profit Factor      : {profit_factor}")
print(f"  Ort. Kazanç        : ${avg_win:.2f}")
print(f"  Ort. Kayıp         : ${avg_loss:.2f}")
rr = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0
print(f"  Ort. Kazanç/Kayıp  : {rr}x")
print(f"  Maks. Drawdown     : %{max_drawdown:.1f}")
print()

print("─── v4.1 PARAMETRELERİ ───")
print(f"  Strateji           : Chart Reader (S/R + Trend + Mum Kalıbı)")
print(f"  TP                 : {TP_ATR_MULT}x ATR")
print(f"  SL                 : {SL_ATR_MULT}x ATR")
print(f"  Zaman Stop         : {TIME_STOP_MINUTES}dk ({time_stop_bars} bar)")
print(f"  Breakeven Lock     : TP'ye %{int(BREAKEVEN_TRIGGER*100)} yaklaşınca")
print(f"  Min Sinyal Skoru   : {MIN_SIGNAL_SCORE}")
print(f"  S/R Lookback       : {SR_LOOKBACK_MULT} bar")
print(f"  S/R Küme Toleransı : {SR_CLUSTER_ATR}x ATR")
print(f"  S/R Yakınlık       : {SR_PROXIMITY_ATR}x ATR")
print(f"  Cooldown           : {COOLDOWN_MINUTES}dk | Kayıp: {LOSS_COOLDOWN_MINUTES}dk")
print(f"  Seans              : {SESSION_START_UTC}:00-{SESSION_END_UTC}:00 UTC")
print(f"  Veri               : {BACKTEST_PERIOD_DAYS} gün ({data_label})")
print()

if total_trades < 30:
    print("─── ⚠️ İSTATİSTİKSEL UYARI ───")
    print(f"  {total_trades} trade istatistiksel olarak YETERSİZ.")
    print(f"  Min sinyal skorunu düşürmeyi deneyin: --score 3")
    if BACKTEST_PERIOD_DAYS <= 7:
        print(f"  → 'python backtest_v4.py --period 60' ile 60 gün test edin.")
    print()

# Trade listesi
if total_trades <= 60 and total_trades > 0:
    print("─── TÜM İŞLEMLER ───")
    for idx_t, t in enumerate(trades, 1):
        icon = "✅" if t['pnl'] > 0 else ("⚪" if abs(t['pnl']) < 0.01 else "❌")
        r_str = ','.join(t.get('reasons', [])[:3])
        print(f"  #{idx_t:2d} {icon} {t['type']:5s} | ${t['entry']:.2f} → ${t['exit']:.2f} | "
              f"P/L: ${t['pnl']:+.2f} | {t['result']:4s} | Lot:{t['lot']} | "
              f"S:{t['score']} B:{t['bars']} | {r_str} {t.get('timestamp','')}")
    print()

print("=" * 60)
if net >= 0:
    print(f"  ✅ STRATEJİ KÂRDA (${'+' if net>=0 else ''}{net:.2f}). WR: %{win_rate:.1f}")
else:
    print(f"  ⚠️ STRATEJİ ZARARDA (${net:.2f}). Optimizasyona devam edilmeli.")
print("=" * 60)
print()
print("─── KULLANIM ───")
print(f"  python backtest_v4.py              → 7 gün, 1dk")
print(f"  python backtest_v4.py --period 30  → 30 gün, 5dk")
print(f"  python backtest_v4.py --period 60  → 60 gün, 5dk")
print(f"  python backtest_v4.py --debug      → Sinyal detayları")
print(f"  ✅ Timeframe agnostik: aynı strateji her timeframe'de çalışır")
