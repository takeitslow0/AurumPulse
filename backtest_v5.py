"""
AurumPulse Backtest v5.2 — Pattern Trader + Triangle + Volume
═══════════════════════════════════════════════════════
GRAFİK KALIBI STRATEJİSİ:
  Otomatik grafik kalıbı tespiti + sabit $ hedefler

  Kalıplar:
  1) Double Bottom (W) → LONG  [%82 başarı]
  2) Double Top (M) → SHORT    [%65-70 başarı]
  3) Head & Shoulders → SHORT  [%82 başarı]
  4) Inv. Head & Shoulders → LONG [%84 başarı]
  5) Bull Flag → LONG          [trend devamı]
  6) Bear Flag → SHORT         [trend devamı]
  7) Ascending Triangle → LONG [%75 başarı]
  8) Descending Triangle → SHORT [%72 başarı]

  Hedefler:
  TP: +$20 sabit | SL: -$5 sabit | R:R = 4:1
  %25 WR bile kârlı (breakeven = %20 WR)

TIMEFRAME AGNOSTIK:
  Kalıplar fraktal — her timeframe'de aynı mantık.
  Default: 30 gün / 5dk veri.
"""

import pandas as pd
import numpy as np
import yfinance as yf
import sys
import random
import warnings
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════
# HESAP & TRADE AYARLARI
# ═══════════════════════════════════════════
ACCOUNT_CONFIG = {
    'balance': 100.0,
    'contract_size': 100,
    'min_lot': 0.01,
    'max_lot': 0.10,
}

# Sabit dolar hedefler
TP_DOLLARS = 20.0     # +$20 kâr hedefi
SL_DOLLARS = 5.0      # -$5 zarar limiti

# Lot hesaplama: TP_DOLLARS = lot * pip_distance * contract_size
# 0.01 lot ile $20 kâr = 200 pip ($20 / 0.01 / 100)
# 0.01 lot ile $5 zarar = 50 pip ($5 / 0.01 / 100)
TRADE_LOT = 0.01      # Default lot (küçük hesap)

# ═══ TRAILING STOP (#3) ═══
TRAILING_ENABLED = True
TRAILING_ACTIVATE_DOLLARS = 10.0   # $10 kâra ulaşınca trailing başlat
TRAILING_STEP_DOLLARS = 5.0        # Her $5 kâr adımında SL'i yukarı çek
# Örnek: Giriş $2000, LONG, $10 kâr → SL breakeven'a ($2000)
#         $15 kâr → SL $2000+$5 kâr'a, $20 kâr → TP kapanır

# ═══ PATTERN CONFIDENCE LOT (#5) ═══
CONFIDENCE_LOT_ENABLED = True
HIGH_CONF_THRESHOLD = 80           # Bu ve üstü → büyük lot
HIGH_CONF_LOT = 0.02              # Yüksek güven lotu (sabit mod)
LOW_CONF_LOT = 0.01               # Düşük güven lotu (sabit mod)

# ═══ EQUITY-BASED LOT SIZING ═══
EQUITY_LOT_ENABLED = True          # True: bakiyeye göre lot hesapla, False: sabit lot
EQUITY_RISK_PCT = 2.0              # Her trade'de bakiyenin %2'sini riske at
EQUITY_HIGH_CONF_MULT = 2.0       # Yüksek güven kalıbında lot x2
# Formül: lot = (bakiye * risk_pct / 100) / SL_DOLLARS / contract_size
# $100 bakiye → 0.01 lot | $500 → 0.05 lot | $1000 → 0.10 lot

# ═══ PARTIAL TP ═══
PARTIAL_TP_ENABLED = True
PARTIAL_TP_DOLLARS = 10.0          # $10 kârda pozisyonun yarısını kapat
# Örnek: 0.02 lot ile girdik, $10 kârda 0.01 lot kapatılır (+$10)
# Kalan 0.01 lot trailing ile devam eder

# ═══ DYNAMIC TP ═══
DYNAMIC_TP_ENABLED = True
DYNAMIC_TP_MIN = 10.0              # Min TP hedefi ($)
DYNAMIC_TP_MAX = 40.0              # Max TP hedefi ($)
DYNAMIC_TP_MULTIPLIER = 1.5        # pattern_height * multiplier * lot * contract_size = TP $
# Küçük kalıp → küçük hedef (min $10), büyük kalıp → büyük hedef (max $40)
# Sabit $20 TP devre dışı kalır, dinamik TP devreye girer

# ═══ EMA TREND FILTER (#6) ═══
EMA_FILTER_ENABLED = True          # LONG sadece bullish EMA'da, SHORT sadece bearish'te

# ═══ MULTI-TIMEFRAME TEYİDİ ═══
MTF_ENABLED = False
MTF_RESAMPLE = '1h'               # 5dk veriyi 1 saate resample et
MTF_EMA_FAST = 20                  # 1h EMA hızlı
MTF_EMA_SLOW = 50                  # 1h EMA yavaş
# LONG → 1h EMA20 > EMA50 (büyük trend yukarı)
# SHORT → 1h EMA20 < EMA50 (büyük trend aşağı)

# ═══ SPREAD & SLIPPAGE SİMÜLASYONU ═══
SPREAD_SLIPPAGE_ENABLED = True
SPREAD_PIPS = 3.0              # Gold tipik spread: 3 pip ($0.30/pip @ 0.01 lot)
SLIPPAGE_MIN_PIPS = 0.0        # Min slippage
SLIPPAGE_MAX_PIPS = 2.0        # Max slippage (random)
# Toplam maliyet: entry'de spread + slippage kadar kötü fiyattan giriş
# LONG: entry += spread + slippage (daha yüksek giriş)
# SHORT: entry -= spread + slippage (daha düşük giriş)
# Bu SL'e yaklaştırır ve TP'den uzaklaştırır → gerçekçi test

# Genel filtreler
COOLDOWN_MINUTES = 0        # Cooldown kapalı — max trade frekansı
SESSION_FILTER = True
SESSION_START_UTC = 10
SESSION_END_UTC = 20

# Swing tespit
SWING_WINDOW = 5            # Swing high/low tespit penceresi (her iki yanda N bar)
PATTERN_LOOKBACK = 40       # Kalıp arama penceresi (bar) (v5.7: 60 → 40)
DOUBLE_TOLERANCE_PCT = 0.25 # Double top/bottom fiyat toleransı (ATR yüzdesi) (v5.7: 0.15 → 0.25)
FLAG_MIN_POLE_ATR = 2.0     # Flag pole minimum yükseklik (ATR cinsinden)
FLAG_MAX_CONSOLIDATION = 20 # Flag konsolidasyon max bar sayısı

# Triangle tespit
TRIANGLE_MIN_TOUCHES = 3    # Min temas noktası (üst+alt toplam)
TRIANGLE_SLOPE_TOL = 0.10   # Düz çizgi toleransı (ATR yüzdesi)
TRIANGLE_MIN_BARS = 10      # Triangle min genişlik (bar)
TRIANGLE_MAX_BARS = 50      # Triangle max genişlik (bar)

# ═══ VOLUME TEYİDİ (#4) ═══
VOLUME_CONFIRM_ENABLED = False
VOLUME_MULTIPLIER = 1.3     # Breakout bar hacmi >= ortalama * bu çarpan

# ═══ KOMUT SATIRI ═══
BACKTEST_PERIOD_DAYS = 30
if '--period' in sys.argv:
    try:
        idx = sys.argv.index('--period')
        BACKTEST_PERIOD_DAYS = int(sys.argv[idx + 1])
    except (IndexError, ValueError):
        pass

IS_5MIN_DATA = BACKTEST_PERIOD_DAYS > 7
DEBUG_MODE = '--debug' in sys.argv


# ═══════════════════════════════════════════
# İNDİKATÖRLER (minimal)
# ═══════════════════════════════════════════
def calculate_indicators(df):
    close = df['Close']
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - close.shift()).abs(),
        (df['Low'] - close.shift()).abs()
    ], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()

    # EMA'lar (trend yönü)
    df['EMA20'] = close.ewm(span=20, adjust=False).mean()
    df['EMA50'] = close.ewm(span=50, adjust=False).mean()

    # RSI (teyit)
    d = close.diff()
    gain = d.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(com=13, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = (100 - 100 / (1 + rs)).fillna(50)

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

    return df


# ═══════════════════════════════════════════
# SWING NOKTALARI TESPİTİ
# ═══════════════════════════════════════════
def find_swings(df, idx, lookback):
    """
    Son 'lookback' bar içindeki swing high ve swing low noktalarını bul.
    Her swing: (bar_index, price)
    """
    start = max(0, idx - lookback)
    chunk_h = df['High'].values
    chunk_l = df['Low'].values
    w = SWING_WINDOW

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


# ═══════════════════════════════════════════
# KALIP 1: DOUBLE BOTTOM (W) → LONG
# ═══════════════════════════════════════════
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
        if bar_dist < 5 or bar_dist > PATTERN_LOOKBACK:
            continue

        # Fiyat benzerliği kontrolü
        tolerance = DOUBLE_TOLERANCE_PCT * atr
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


# ═══════════════════════════════════════════
# KALIP 2: DOUBLE TOP (M) → SHORT
# ═══════════════════════════════════════════
def detect_double_top(swing_highs, swing_lows, current_price, atr, idx):
    """
    M kalıbı: İki benzer tepe + aradaki dip (neckline)
    Neckline kırılımında SHORT
    """
    if len(swing_highs) < 2 or len(swing_lows) < 1:
        return None

    for i in range(len(swing_highs) - 1, 0, -1):
        high2_idx, high2 = swing_highs[i]
        high1_idx, high1 = swing_highs[i - 1]

        bar_dist = high2_idx - high1_idx
        if bar_dist < 5 or bar_dist > PATTERN_LOOKBACK:
            continue

        tolerance = DOUBLE_TOLERANCE_PCT * atr
        if abs(high1 - high2) > tolerance:
            continue

        mid_lows = [l for l in swing_lows if high1_idx < l[0] < high2_idx]
        if not mid_lows:
            continue
        neckline_idx, neckline = min(mid_lows, key=lambda x: x[1])

        if current_price < neckline:
            pattern_height = max(high1, high2) - neckline
            return {
                'pattern': 'DOUBLE_TOP',
                'direction': 'SHORT',
                'neckline': neckline,
                'height': pattern_height,
                'confidence': 68,
                'high1': high1, 'high2': high2,
            }

    return None


# ═══════════════════════════════════════════
# KALIP 3: HEAD & SHOULDERS → SHORT
# ═══════════════════════════════════════════
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
        tolerance = DOUBLE_TOLERANCE_PCT * atr * 2
        if abs(ls - rs) > tolerance:
            continue

        # Bar mesafeleri mantıklı mı
        if (h_idx - ls_idx) < 3 or (rs_idx - h_idx) < 3:
            continue
        if (rs_idx - ls_idx) > PATTERN_LOOKBACK:
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


# ═══════════════════════════════════════════
# KALIP 4: INVERSE HEAD & SHOULDERS → LONG
# ═══════════════════════════════════════════
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

        tolerance = DOUBLE_TOLERANCE_PCT * atr * 2
        if abs(ls - rs) > tolerance:
            continue

        if (h_idx - ls_idx) < 3 or (rs_idx - h_idx) < 3:
            continue
        if (rs_idx - ls_idx) > PATTERN_LOOKBACK:
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


# ═══════════════════════════════════════════
# KALIP 5 & 6: BULL FLAG / BEAR FLAG
# ═══════════════════════════════════════════
def detect_flag(df, idx, atr):
    """
    Flag kalıbı: Güçlü hareket (pole) + küçük konsolidasyon (flag)
    Bull flag: Yukarı pole + hafif düşüş konsolidasyonu → LONG
    Bear flag: Aşağı pole + hafif yükseliş konsolidasyonu → SHORT
    """
    if idx < 30:
        return None

    close = float(df.iloc[idx]['Close'])
    lookback = min(FLAG_MAX_CONSOLIDATION + 10, idx)

    # Son N bar'da konsolidasyon var mı? (düşük volatilite bölgesi)
    recent = df.iloc[idx - lookback:idx + 1]
    recent_range = recent['High'].max() - recent['Low'].min()
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
    pole_move = float(pole_chunk['Close'].iloc[-1]) - float(pole_chunk['Close'].iloc[0])
    pole_abs = abs(pole_move)

    if pole_abs < FLAG_MIN_POLE_ATR * atr:
        return None

    # Yön belirleme
    if pole_move > 0:
        # Bull flag: yukarı pole + konsolidasyon
        # Konsolidasyon hafif düşüş veya yatay olmalı
        consol_slope = close - float(df.iloc[idx - lookback]['Close'])
        if consol_slope > 0.5 * atr:  # Konsolidasyon yukarı gidiyorsa flag değil
            return None
        return {
            'pattern': 'BULL_FLAG',
            'direction': 'LONG',
            'neckline': recent['High'].max(),
            'height': pole_abs,
            'confidence': 65,
            'pole_move': pole_move,
        }
    else:
        # Bear flag
        consol_slope = close - float(df.iloc[idx - lookback]['Close'])
        if consol_slope < -0.5 * atr:
            return None
        return {
            'pattern': 'BEAR_FLAG',
            'direction': 'SHORT',
            'neckline': recent['Low'].min(),
            'height': pole_abs,
            'confidence': 65,
            'pole_move': pole_move,
        }


# ═══════════════════════════════════════════
# KALIP 7: ASCENDING TRIANGLE → LONG
# ═══════════════════════════════════════════
def detect_ascending_triangle(swing_highs, swing_lows, current_price, atr, idx):
    """
    Ascending Triangle: Düz direnç + yükselen dipler
    Direnç kırılımında LONG
    En az 2 swing high benzer seviyede (düz direnç)
    En az 2 swing low yükselen (artan destek)
    """
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return None

    # Son swing high'ları kontrol et — benzer seviyede mi? (düz direnç)
    for i in range(len(swing_highs) - 1, 0, -1):
        h2_idx, h2 = swing_highs[i]
        h1_idx, h1 = swing_highs[i - 1]

        bar_dist = h2_idx - h1_idx
        if bar_dist < TRIANGLE_MIN_BARS or bar_dist > TRIANGLE_MAX_BARS:
            continue

        # Düz direnç kontrolü: iki tepe benzer seviyede
        tolerance = TRIANGLE_SLOPE_TOL * atr
        if abs(h1 - h2) > tolerance:
            continue

        resistance = (h1 + h2) / 2

        # Aradaki swing low'lar yükselen mi?
        mid_lows = [l for l in swing_lows if h1_idx < l[0] < h2_idx]
        if not mid_lows:
            # h1'den önceki son low'u da dahil et
            pre_lows = [l for l in swing_lows if l[0] < h1_idx]
            post_lows = [l for l in swing_lows if h1_idx < l[0] <= h2_idx]
            if pre_lows and post_lows:
                low_before = pre_lows[-1][1]
                low_after = post_lows[-1][1] if post_lows else low_before
                if low_after <= low_before:
                    continue  # Yükselmiyor
            else:
                continue
        else:
            # En az bir yükselen dip olmalı
            if len(mid_lows) >= 2:
                if mid_lows[-1][1] <= mid_lows[0][1]:
                    continue  # Yükselmiyor

        # Fiyat direnci kırmış mı?
        if current_price > resistance:
            pattern_height = resistance - min(l[1] for l in swing_lows[-3:])
            return {
                'pattern': 'ASC_TRIANGLE',
                'direction': 'LONG',
                'neckline': resistance,
                'height': pattern_height,
                'confidence': 75,
                'resistance': resistance,
            }

    return None


# ═══════════════════════════════════════════
# KALIP 8: DESCENDING TRIANGLE → SHORT
# ═══════════════════════════════════════════
def detect_descending_triangle(swing_highs, swing_lows, current_price, atr, idx):
    """
    Descending Triangle: Düz destek + alçalan tepeler
    Destek kırılımında SHORT
    """
    if len(swing_lows) < 2 or len(swing_highs) < 2:
        return None

    for i in range(len(swing_lows) - 1, 0, -1):
        l2_idx, l2 = swing_lows[i]
        l1_idx, l1 = swing_lows[i - 1]

        bar_dist = l2_idx - l1_idx
        if bar_dist < TRIANGLE_MIN_BARS or bar_dist > TRIANGLE_MAX_BARS:
            continue

        # Düz destek kontrolü
        tolerance = TRIANGLE_SLOPE_TOL * atr
        if abs(l1 - l2) > tolerance:
            continue

        support = (l1 + l2) / 2

        # Aradaki swing high'lar alçalan mı?
        mid_highs = [h for h in swing_highs if l1_idx < h[0] < l2_idx]
        if not mid_highs:
            pre_highs = [h for h in swing_highs if h[0] < l1_idx]
            post_highs = [h for h in swing_highs if l1_idx < h[0] <= l2_idx]
            if pre_highs and post_highs:
                high_before = pre_highs[-1][1]
                high_after = post_highs[-1][1] if post_highs else high_before
                if high_after >= high_before:
                    continue  # Alçalmıyor
            else:
                continue
        else:
            if len(mid_highs) >= 2:
                if mid_highs[-1][1] >= mid_highs[0][1]:
                    continue  # Alçalmıyor

        # Fiyat desteği kırmış mı?
        if current_price < support:
            pattern_height = max(h[1] for h in swing_highs[-3:]) - support
            return {
                'pattern': 'DESC_TRIANGLE',
                'direction': 'SHORT',
                'neckline': support,
                'height': pattern_height,
                'confidence': 72,
                'support': support,
            }

    return None


# ═══════════════════════════════════════════
# VOLUME TEYİDİ (#4)
# ═══════════════════════════════════════════
def check_volume_confirmation(df, idx, lookback=20):
    """
    Breakout bar'ında hacim ortalamanın üstünde mi?
    Volume sütunu yoksa True döndür (filtre devre dışı)
    """
    if 'Volume' not in df.columns:
        return True

    vol = df['Volume'].values
    current_vol = float(vol[idx])

    # Sıfır veya NaN volume varsa filtre devre dışı
    if current_vol <= 0 or np.isnan(current_vol):
        return True

    start = max(0, idx - lookback)
    avg_vol = float(np.nanmean(vol[start:idx]))

    if avg_vol <= 0:
        return True

    return current_vol >= avg_vol * VOLUME_MULTIPLIER


# ═══════════════════════════════════════════
# ANA KALIP TESPİT MOTORU
# ═══════════════════════════════════════════
def detect_patterns(df, idx, atr):
    """Tüm kalıpları tara, en güvenilir olanı döndür"""
    swing_highs, swing_lows = find_swings(df, idx, PATTERN_LOOKBACK)
    current_price = float(df.iloc[idx]['Close'])

    patterns_found = []

    # Double Bottom
    p = detect_double_bottom(swing_lows, swing_highs, current_price, atr, idx)
    if p:
        patterns_found.append(p)

    # Double Top (v5.7'te %15 WR, zarar — devre dışı)
    # p = detect_double_top(swing_highs, swing_lows, current_price, atr, idx)
    # if p:
    #     patterns_found.append(p)

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

    # Ascending Triangle (v5.2'de test edildi, düşük WR — devre dışı)
    # p = detect_ascending_triangle(swing_highs, swing_lows, current_price, atr, idx)
    # if p:
    #     patterns_found.append(p)

    # Descending Triangle (v5.2'de test edildi, düşük WR — devre dışı)
    # p = detect_descending_triangle(swing_highs, swing_lows, current_price, atr, idx)
    # if p:
    #     patterns_found.append(p)

    if not patterns_found:
        return None

    # En yüksek güvenilirliğe sahip kalıbı seç
    return max(patterns_found, key=lambda x: x['confidence'])


# ═══════════════════════════════════════════
# YARDIMCI
# ═══════════════════════════════════════════
def get_bar_interval_minutes(df):
    if len(df) < 2:
        return 1
    diff = (df.index[1] - df.index[0]).total_seconds() / 60
    return max(1, round(diff))


# ═══════════════════════════════════════════
# ANA BACKTEST
# ═══════════════════════════════════════════
print("=" * 60)
print("   AURUMPULSE v5.7 — PATTERN TRADER")
print("   Multi-TF + Trailing + EMA")
print("=" * 60)
print()

if BACKTEST_PERIOD_DAYS <= 7:
    dl_interval = "1m"
    dl_period = "7d"
    data_label = "1dk"
    print(f"📊 Altın (XAU/USD) son {BACKTEST_PERIOD_DAYS} günlük 1dk veriler indiriliyor...")
else:
    dl_interval = "5m"
    dl_period = f"{BACKTEST_PERIOD_DAYS}d"
    data_label = "5dk"
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
df.dropna(subset=['ATR', 'EMA20', 'RSI'], inplace=True)
print(f"✅ İndikatörlü veri: {len(df)} mum\n")

# ═══ MULTI-TIMEFRAME: 1h resample ═══
df_htf = None
if MTF_ENABLED and IS_5MIN_DATA:
    print("📐 Multi-timeframe: 5dk → 1h resample ediliyor...")
    df_htf = df.resample(MTF_RESAMPLE).agg({
        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
    }).dropna()
    df_htf['EMA_FAST'] = df_htf['Close'].ewm(span=MTF_EMA_FAST, adjust=False).mean()
    df_htf['EMA_SLOW'] = df_htf['Close'].ewm(span=MTF_EMA_SLOW, adjust=False).mean()
    df_htf['HTF_TREND'] = np.where(df_htf['EMA_FAST'] > df_htf['EMA_SLOW'], 'bullish', 'bearish')
    print(f"✅ 1h veri: {len(df_htf)} mum\n")


def get_htf_trend(timestamp):
    """5dk bar'ının ait olduğu 1h bar'daki trendi döndür"""
    if df_htf is None or not MTF_ENABLED:
        return None
    # En yakın geçmiş 1h bar'ı bul
    mask = df_htf.index <= timestamp
    if mask.any():
        return df_htf.loc[mask].iloc[-1]['HTF_TREND']
    return None


bar_interval_min = get_bar_interval_minutes(df)
cooldown_bars = max(0, COOLDOWN_MINUTES // bar_interval_min) if COOLDOWN_MINUTES > 0 else 0

# SL/TP pip mesafelerini hesapla (referans: default lot)
sl_pips_ref = SL_DOLLARS / (TRADE_LOT * ACCOUNT_CONFIG['contract_size'])
tp_pips_ref = TP_DOLLARS / (TRADE_LOT * ACCOUNT_CONFIG['contract_size'])

print(f"⏱️ Timeframe: {bar_interval_min}dk per bar")
print(f"⚙️ v5.7 Pattern Trader parametreleri:")
print(f"   TP: +${TP_DOLLARS} | SL: -${SL_DOLLARS} | R:R = {TP_DOLLARS/SL_DOLLARS:.0f}:1")
print(f"   Lot: {LOW_CONF_LOT} (normal) / {HIGH_CONF_LOT} (yüksek güven)")
print(f"   Cooldown: {COOLDOWN_MINUTES}dk = {cooldown_bars} bar")
print(f"   Swing penceresi: ±{SWING_WINDOW} bar | Kalıp lookback: {PATTERN_LOOKBACK} bar")
print(f"   Seans: {SESSION_START_UTC}:00-{SESSION_END_UTC}:00 UTC")
print(f"   Kalıplar: Double Bottom, H&S, Inv H&S, Bull/Bear Flag")
print(f"   Spread+Slip: {'AÇIK' if SPREAD_SLIPPAGE_ENABLED else 'KAPALI'} ({SPREAD_PIPS}+{SLIPPAGE_MIN_PIPS}-{SLIPPAGE_MAX_PIPS} pip)")
print()

# ── Backtest Değişkenleri ──
balance = ACCOUNT_CONFIG['balance']
initial_balance = balance
in_position = False
position_type = None
entry_price = 0
sl_price = 0
tp_price = 0
entry_bar = 0
entry_pattern = ''
entry_timestamp = ''
entry_lot = TRADE_LOT
entry_remaining_lot = TRADE_LOT
entry_partial_done = False
entry_tp_dollars = TP_DOLLARS
trailing_sl_level = 0

last_trade_bar = -999
last_pattern_bar = -999   # Aynı kalıbı tekrar kullanmayı önle

trades = []
max_balance = balance
max_drawdown = 0
equity_curve = []
pattern_stats = {}

# Filtre sayaçları
filtered_no_pattern = 0
filtered_session = 0
filtered_cooldown = 0
filtered_ema = 0
filtered_volume = 0
filtered_mtf = 0

start_idx = PATTERN_LOOKBACK + SWING_WINDOW + 20

random.seed(42)  # Tekrarlanabilir slippage sonuçları
print("🤖 Pattern Trader Motoru simüle ediliyor...\n")

for i in range(start_idx, len(df)):
    row = df.iloc[i]
    close = float(row['Close'])
    high = float(row['High'])
    low = float(row['Low'])
    atr = float(row['ATR'])

    # ── Pozisyon Takibi ──
    if in_position:
        remaining_lot_value = entry_remaining_lot * ACCOUNT_CONFIG['contract_size']
        full_lot_value = entry_lot * ACCOUNT_CONFIG['contract_size']

        # ── PARTIAL TP: $10 kârda yarı pozisyon kapat ──
        if PARTIAL_TP_ENABLED and not entry_partial_done and entry_remaining_lot > ACCOUNT_CONFIG['min_lot']:
            if position_type == 'LONG':
                partial_target = entry_price + (PARTIAL_TP_DOLLARS / full_lot_value)
                if high >= partial_target:
                    # Yarısını kapat
                    half_lot = round(entry_lot / 2, 2)
                    if half_lot >= ACCOUNT_CONFIG['min_lot']:
                        partial_pnl = PARTIAL_TP_DOLLARS / 2  # Yarı lot ile $10 mesafe = $5
                        balance += partial_pnl
                        entry_remaining_lot = round(entry_lot - half_lot, 2)
                        entry_partial_done = True
                        # SL'i breakeven'a çek
                        trailing_sl_level = entry_price
                        sl_price = entry_price
                        if DEBUG_MODE:
                            print(f"  [PARTIAL] {half_lot} lot kapatıldı +${partial_pnl:.0f} | Kalan: {entry_remaining_lot} lot | SL → BE")
            else:  # SHORT
                partial_target = entry_price - (PARTIAL_TP_DOLLARS / full_lot_value)
                if low <= partial_target:
                    half_lot = round(entry_lot / 2, 2)
                    if half_lot >= ACCOUNT_CONFIG['min_lot']:
                        partial_pnl = PARTIAL_TP_DOLLARS / 2
                        balance += partial_pnl
                        entry_remaining_lot = round(entry_lot - half_lot, 2)
                        entry_partial_done = True
                        trailing_sl_level = entry_price
                        sl_price = entry_price
                        if DEBUG_MODE:
                            print(f"  [PARTIAL] {half_lot} lot kapatıldı +${partial_pnl:.0f} | Kalan: {entry_remaining_lot} lot | SL → BE")

        # ── TRAILING STOP (#3) — kalan lot üzerinden ──
        if TRAILING_ENABLED:
            if position_type == 'LONG':
                unrealized = (high - entry_price) * remaining_lot_value
                if unrealized >= TRAILING_ACTIVATE_DOLLARS:
                    steps = int((unrealized - TRAILING_ACTIVATE_DOLLARS) / TRAILING_STEP_DOLLARS)
                    locked_profit = steps * TRAILING_STEP_DOLLARS
                    new_sl = entry_price + (locked_profit / remaining_lot_value)
                    if new_sl > trailing_sl_level:
                        trailing_sl_level = new_sl
                        sl_price = trailing_sl_level
                        if DEBUG_MODE:
                            print(f"  [TRAIL] SL → ${sl_price:.2f} (locked +${locked_profit:.0f})")
            else:  # SHORT
                unrealized = (entry_price - low) * remaining_lot_value
                if unrealized >= TRAILING_ACTIVATE_DOLLARS:
                    steps = int((unrealized - TRAILING_ACTIVATE_DOLLARS) / TRAILING_STEP_DOLLARS)
                    locked_profit = steps * TRAILING_STEP_DOLLARS
                    new_sl = entry_price - (locked_profit / remaining_lot_value)
                    if new_sl < trailing_sl_level:
                        trailing_sl_level = new_sl
                        sl_price = trailing_sl_level
                        if DEBUG_MODE:
                            print(f"  [TRAIL] SL → ${sl_price:.2f} (locked +${locked_profit:.0f})")

        # ── SL / TP KONTROL — kalan lot üzerinden ──
        if position_type == 'LONG':
            if low <= sl_price:
                pnl = (sl_price - entry_price) * remaining_lot_value
                if entry_partial_done:
                    pnl += PARTIAL_TP_DOLLARS / 2  # Partial kârı ekle (zaten balance'a eklendi, trade kaydı için)
                    pnl -= PARTIAL_TP_DOLLARS / 2  # Aslında zaten eklendi, sadece kalan lot PnL'i
                exit_type = 'TRAIL_SL' if sl_price > entry_price else ('P_SL' if entry_partial_done else 'SL')
                balance += pnl
                total_pnl = pnl + (PARTIAL_TP_DOLLARS / 2 if entry_partial_done else 0)
                trades.append({'type': 'LONG', 'entry': entry_price, 'exit': round(sl_price, 2),
                              'pnl': round(total_pnl, 2), 'result': exit_type, 'pattern': entry_pattern,
                              'lot': entry_lot, 'timestamp': entry_timestamp, 'bars': i - entry_bar})
                in_position = False
                last_trade_bar = i
            elif high >= tp_price:
                pnl = (tp_price - entry_price) * remaining_lot_value
                balance += pnl
                total_pnl = pnl + (PARTIAL_TP_DOLLARS / 2 if entry_partial_done else 0)
                exit_type = 'P_TP' if entry_partial_done else 'TP'
                trades.append({'type': 'LONG', 'entry': entry_price, 'exit': round(tp_price, 2),
                              'pnl': round(total_pnl, 2), 'result': exit_type, 'pattern': entry_pattern,
                              'lot': entry_lot, 'timestamp': entry_timestamp, 'bars': i - entry_bar})
                in_position = False
                last_trade_bar = i

        elif position_type == 'SHORT':
            if high >= sl_price:
                pnl = (entry_price - sl_price) * remaining_lot_value
                exit_type = 'TRAIL_SL' if sl_price < entry_price else ('P_SL' if entry_partial_done else 'SL')
                balance += pnl
                total_pnl = pnl + (PARTIAL_TP_DOLLARS / 2 if entry_partial_done else 0)
                trades.append({'type': 'SHORT', 'entry': entry_price, 'exit': round(sl_price, 2),
                              'pnl': round(total_pnl, 2), 'result': exit_type, 'pattern': entry_pattern,
                              'lot': entry_lot, 'timestamp': entry_timestamp, 'bars': i - entry_bar})
                in_position = False
                last_trade_bar = i
            elif low <= tp_price:
                pnl = (entry_price - tp_price) * remaining_lot_value
                balance += pnl
                total_pnl = pnl + (PARTIAL_TP_DOLLARS / 2 if entry_partial_done else 0)
                exit_type = 'P_TP' if entry_partial_done else 'TP'
                trades.append({'type': 'SHORT', 'entry': entry_price, 'exit': round(tp_price, 2),
                              'pnl': round(total_pnl, 2), 'result': exit_type, 'pattern': entry_pattern,
                              'lot': entry_lot, 'timestamp': entry_timestamp, 'bars': i - entry_bar})
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
    if (i - last_trade_bar) < cooldown_bars:
        filtered_cooldown += 1
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
                filtered_session += 1
                equity_curve.append(balance)
                continue

    # ── KALIP TESPİTİ ──
    pattern = detect_patterns(df, i, atr)

    if pattern is None:
        filtered_no_pattern += 1
        equity_curve.append(balance)
        continue

    # ── EMA TREND TEYİDİ (#6) ──
    ema20 = float(row['EMA20'])
    ema50 = float(row['EMA50'])
    ema_trend = 'bullish' if ema20 > ema50 else 'bearish'

    trend_match = (pattern['direction'] == 'LONG' and ema_trend == 'bullish') or \
                  (pattern['direction'] == 'SHORT' and ema_trend == 'bearish')

    # EMA filtresi aktifse, trend uyuşmazsa işlemi reddet
    if EMA_FILTER_ENABLED and not trend_match:
        filtered_ema += 1
        equity_curve.append(balance)
        continue

    # ── MULTI-TIMEFRAME TEYİDİ ──
    if MTF_ENABLED and df_htf is not None:
        htf_trend = get_htf_trend(df.index[i])
        if htf_trend is not None:
            mtf_match = (pattern['direction'] == 'LONG' and htf_trend == 'bullish') or \
                        (pattern['direction'] == 'SHORT' and htf_trend == 'bearish')
            if not mtf_match:
                filtered_mtf += 1
                equity_curve.append(balance)
                continue

    # ── VOLUME TEYİDİ (#4) ──
    if VOLUME_CONFIRM_ENABLED and not check_volume_confirmation(df, i):
        filtered_volume += 1
        equity_curve.append(balance)
        continue

    # ── LOT HESAPLA ──
    if EQUITY_LOT_ENABLED:
        # Bakiyeye göre dinamik lot
        risk_amount = balance * (EQUITY_RISK_PCT / 100.0)
        base_lot = risk_amount / SL_DOLLARS
        # Min/max sınırla
        base_lot = max(ACCOUNT_CONFIG['min_lot'], min(ACCOUNT_CONFIG['max_lot'], base_lot))
        # Yüksek güvenli kalıpta lot çarpanı
        if CONFIDENCE_LOT_ENABLED and pattern['confidence'] >= HIGH_CONF_THRESHOLD:
            trade_lot = min(ACCOUNT_CONFIG['max_lot'], round(base_lot * EQUITY_HIGH_CONF_MULT, 2))
        else:
            trade_lot = round(base_lot, 2)
        trade_lot = max(ACCOUNT_CONFIG['min_lot'], trade_lot)
    else:
        # Sabit lot modu
        if CONFIDENCE_LOT_ENABLED and pattern['confidence'] >= HIGH_CONF_THRESHOLD:
            trade_lot = HIGH_CONF_LOT
        else:
            trade_lot = LOW_CONF_LOT

    # ── DYNAMIC TP ──
    if DYNAMIC_TP_ENABLED and pattern.get('height', 0) > 0:
        # Pattern yüksekliğine göre TP hesapla
        raw_tp_dollars = pattern['height'] * DYNAMIC_TP_MULTIPLIER * trade_lot * ACCOUNT_CONFIG['contract_size']
        actual_tp_dollars = max(DYNAMIC_TP_MIN, min(DYNAMIC_TP_MAX, raw_tp_dollars))
    else:
        actual_tp_dollars = TP_DOLLARS

    # SL/TP pip mesafelerini bu lot'a göre hesapla
    trade_sl_pips = SL_DOLLARS / (trade_lot * ACCOUNT_CONFIG['contract_size'])
    trade_tp_pips = actual_tp_dollars / (trade_lot * ACCOUNT_CONFIG['contract_size'])

    # ── PARTIAL TP seviyesi ──
    partial_tp_pips = PARTIAL_TP_DOLLARS / (trade_lot * ACCOUNT_CONFIG['contract_size']) if PARTIAL_TP_ENABLED else 0

    # ── POZİSYON AÇ ──
    if balance > 5:
        # Spread + slippage maliyeti
        if SPREAD_SLIPPAGE_ENABLED:
            slippage = random.uniform(SLIPPAGE_MIN_PIPS, SLIPPAGE_MAX_PIPS)
            total_cost = SPREAD_PIPS + slippage
        else:
            total_cost = 0

        if pattern['direction'] == 'LONG':
            actual_entry = close + total_cost  # Daha kötü fiyattan giriş
            sl = actual_entry - trade_sl_pips
            tp = actual_entry + trade_tp_pips
        else:
            actual_entry = close - total_cost  # Daha kötü fiyattan giriş
            sl = actual_entry + trade_sl_pips
            tp = actual_entry - trade_tp_pips

        in_position = True
        position_type = pattern['direction']
        entry_price = actual_entry
        sl_price = sl
        tp_price = tp
        entry_bar = i
        entry_pattern = pattern['pattern']
        entry_lot = trade_lot
        entry_remaining_lot = trade_lot  # Partial TP sonrası kalan lot
        entry_partial_done = False       # Partial TP yapıldı mı
        entry_tp_dollars = actual_tp_dollars
        entry_timestamp = str(df.index[i])[:16] if hasattr(df.index[i], 'strftime') else ''
        last_pattern_bar = i
        trailing_sl_level = sl  # Trailing için başlangıç SL

        # İstatistik
        pname = pattern['pattern']
        if pname not in pattern_stats:
            pattern_stats[pname] = {'count': 0, 'wins': 0, 'losses': 0, 'pnl': 0}
        pattern_stats[pname]['count'] += 1

        if DEBUG_MODE:
            trend_str = "✓TREND" if trend_match else "✗COUNTER"
            print(f"  [OPEN] {entry_timestamp} | {position_type} @ ${close:.2f} | "
                  f"{pname} conf={pattern['confidence']}% lot={trade_lot} | "
                  f"SL=${sl:.2f} TP=${tp:.2f} | {trend_str}")

    equity_curve.append(balance)


# ═══════════════════════════════════════════
# SONUÇLAR
# ═══════════════════════════════════════════
total_trades = len(trades)
winning = sum(1 for t in trades if t['pnl'] > 0)
losing = sum(1 for t in trades if t['pnl'] < 0)
win_rate = (winning / total_trades * 100) if total_trades > 0 else 0

gross_profit = sum(t['pnl'] for t in trades if t['pnl'] > 0)
gross_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf')

avg_win = (gross_profit / winning) if winning > 0 else 0
avg_loss = (gross_loss / losing) if losing > 0 else 0

# Streak
max_win_streak = 0
max_loss_streak = 0
cs = 0; st = None
for t in trades:
    if t['pnl'] > 0:
        if st == 'w': cs += 1
        else: cs = 1; st = 'w'
        max_win_streak = max(max_win_streak, cs)
    elif t['pnl'] < 0:
        if st == 'l': cs += 1
        else: cs = 1; st = 'l'
        max_loss_streak = max(max_loss_streak, cs)

# Pattern stats update
for t in trades:
    pn = t['pattern']
    if pn in pattern_stats:
        if t['pnl'] > 0:
            pattern_stats[pn]['wins'] += 1
        else:
            pattern_stats[pn]['losses'] += 1
        pattern_stats[pn]['pnl'] = pattern_stats[pn].get('pnl', 0) + t['pnl']

tp_count = sum(1 for t in trades if t['result'] == 'TP')
sl_count = sum(1 for t in trades if t['result'] == 'SL')
trail_count = sum(1 for t in trades if t['result'] == 'TRAIL_SL')
partial_tp_count = sum(1 for t in trades if t['result'] in ('P_TP', 'P_SL'))

print()
print("=" * 60)
print("   AURUMPULSE v5.7 BACKTEST RAPORU")
print("   6 Kalıp + Multi-TF + Trailing + EMA")
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
print(f"  Kazanan            : {winning}  (TP: {tp_count}, Trail+: {trail_count}, Partial: {partial_tp_count})")
print(f"  Kaybeden           : {losing}  (SL: {sl_count})")
print(f"  Kazanma Oranı      : %{win_rate:.1f}")
print(f"  Maks Kazanç Serisi : {max_win_streak}")
print(f"  Maks Kayıp Serisi  : {max_loss_streak}")
print()
print("─── KALIP PERFORMANSI ───")
for pn, ps in sorted(pattern_stats.items(), key=lambda x: x[1]['count'], reverse=True):
    wr = (ps['wins'] / ps['count'] * 100) if ps['count'] > 0 else 0
    ppnl = ps.get('pnl', 0)
    print(f"  {pn:22s} : {ps['count']:3d} trade | WR: %{wr:.0f} ({ps['wins']}W/{ps['losses']}L) | ${ppnl:+.2f}")
print()
print("─── FİLTRE İSTATİSTİKLERİ ───")
print(f"  Kalıp bulunamadı   : {filtered_no_pattern} bar")
print(f"  EMA trend uyumsuz  : {filtered_ema} bar")
print(f"  Volume yetersiz    : {filtered_volume} bar")
print(f"  MTF trend uyumsuz  : {filtered_mtf} bar")
print(f"  Seans dışı         : {filtered_session} bar")
print(f"  Cooldown           : {filtered_cooldown} bar")
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
print(f"  Ort. Kazanç/Kayıp  : {rr}x R:R")
print(f"  Maks. Drawdown     : %{max_drawdown:.1f}")
print()

print("─── v5.7 PARAMETRELERİ ───")
print(f"  Strateji           : Pattern Trader (6 kalıp)")
print(f"  TP                 : +${TP_DOLLARS} | SL: -${SL_DOLLARS} | R:R: {TP_DOLLARS/SL_DOLLARS:.0f}:1")
print(f"  Lot                : {LOW_CONF_LOT} (normal) / {HIGH_CONF_LOT} (yüksek güven)")
print(f"  Trailing Stop      : {'AÇIK' if TRAILING_ENABLED else 'KAPALI'} (${TRAILING_ACTIVATE_DOLLARS}+ → BE, +${TRAILING_STEP_DOLLARS} adım)")
print(f"  EMA Filtre         : {'AÇIK' if EMA_FILTER_ENABLED else 'KAPALI'} (5dk EMA20/50)")
print(f"  Multi-TF           : {'AÇIK' if MTF_ENABLED else 'KAPALI'} ({MTF_RESAMPLE} EMA{MTF_EMA_FAST}/{MTF_EMA_SLOW} trend teyidi)")
print(f"  Partial TP         : {'AÇIK' if PARTIAL_TP_ENABLED else 'KAPALI'} (${PARTIAL_TP_DOLLARS}+ → yarı lot kapat + SL→BE)")
print(f"  Dynamic TP         : {'AÇIK' if DYNAMIC_TP_ENABLED else 'KAPALI'} (min ${DYNAMIC_TP_MIN} - max ${DYNAMIC_TP_MAX}, x{DYNAMIC_TP_MULTIPLIER} height)")
print(f"  Güven Lot          : {'AÇIK' if CONFIDENCE_LOT_ENABLED else 'KAPALI'} (conf≥{HIGH_CONF_THRESHOLD} → x{EQUITY_HIGH_CONF_MULT})")
print(f"  Equity Lot         : {'AÇIK' if EQUITY_LOT_ENABLED else 'KAPALI'} (bakiye %{EQUITY_RISK_PCT} risk → dinamik lot)")
print(f"  Spread+Slippage    : {'AÇIK' if SPREAD_SLIPPAGE_ENABLED else 'KAPALI'} (spread:{SPREAD_PIPS}pip + slip:{SLIPPAGE_MIN_PIPS}-{SLIPPAGE_MAX_PIPS}pip)")
print(f"  Cooldown           : {COOLDOWN_MINUTES}dk")
print(f"  Seans              : {SESSION_START_UTC}:00-{SESSION_END_UTC}:00 UTC")
print(f"  Veri               : {BACKTEST_PERIOD_DAYS} gün ({data_label})")
print()

if total_trades < 30:
    print("─── ⚠️ İSTATİSTİKSEL UYARI ───")
    print(f"  {total_trades} trade istatistiksel olarak YETERSİZ.")
    print(f"  → 'python backtest_v5.py --period 60' ile 60 gün test edin.")
    print()

# ─── GÜNLÜK BREAKDOWN ───
if total_trades > 0:
    from collections import defaultdict
    daily = defaultdict(lambda: {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0})
    day_names_tr = {0: 'Pzt', 1: 'Sal', 2: 'Çar', 3: 'Per', 4: 'Cum', 5: 'Cmt', 6: 'Paz'}

    for t in trades:
        ts = t.get('timestamp', '')
        if ts:
            day_key = ts[:10]  # YYYY-MM-DD
            daily[day_key]['trades'] += 1
            daily[day_key]['pnl'] += t['pnl']
            if t['pnl'] > 0:
                daily[day_key]['wins'] += 1
            elif t['pnl'] < 0:
                daily[day_key]['losses'] += 1

    sorted_days = sorted(daily.items())
    print("─── GÜNLÜK PERFORMANS ───")
    print(f"  {'TARİH':12s} {'GÜN':4s} {'İŞLEM':>5s} {'W':>3s} {'L':>3s} {'WR':>5s} {'P/L':>10s} {'BAKİYE':>10s}")
    print(f"  {'─'*12} {'─'*4} {'─'*5} {'─'*3} {'─'*3} {'─'*5} {'─'*10} {'─'*10}")

    running_balance = initial_balance
    total_active_days = 0
    profitable_days = 0
    best_day_pnl = -999
    worst_day_pnl = 999
    best_day_name = ''
    worst_day_name = ''

    for day_str, d in sorted_days:
        running_balance += d['pnl']
        wr = (d['wins'] / d['trades'] * 100) if d['trades'] > 0 else 0
        # Gün adı
        try:
            from datetime import datetime
            dt = datetime.strptime(day_str, '%Y-%m-%d')
            day_name = day_names_tr.get(dt.weekday(), '?')
        except:
            day_name = '?'

        icon = "✅" if d['pnl'] >= 0 else "❌"
        print(f"  {day_str:12s} {day_name:4s} {d['trades']:5d} {d['wins']:3d} {d['losses']:3d} "
              f"%{wr:4.0f} {icon} ${d['pnl']:+8.2f} ${running_balance:9.2f}")

        total_active_days += 1
        if d['pnl'] >= 0:
            profitable_days += 1
        if d['pnl'] > best_day_pnl:
            best_day_pnl = d['pnl']
            best_day_name = day_str
        if d['pnl'] < worst_day_pnl:
            worst_day_pnl = d['pnl']
            worst_day_name = day_str

    print()
    avg_daily_pnl = net / total_active_days if total_active_days > 0 else 0
    avg_trades_day = total_trades / total_active_days if total_active_days > 0 else 0
    print(f"  Aktif Gün Sayısı   : {total_active_days}")
    print(f"  Kârlı Gün          : {profitable_days} / {total_active_days} (%{profitable_days/total_active_days*100:.0f})")
    print(f"  Ort. Günlük Kâr    : ${avg_daily_pnl:+.2f}")
    print(f"  Ort. Trade/Gün     : {avg_trades_day:.1f}")
    print(f"  En İyi Gün         : {best_day_name} (${best_day_pnl:+.2f})")
    print(f"  En Kötü Gün        : {worst_day_name} (${worst_day_pnl:+.2f})")
    print()

    # 10 trade/gün simülasyonu
    if avg_trades_day > 0:
        scale_factor = 10.0 / avg_trades_day
        projected_daily = avg_daily_pnl * scale_factor
        projected_monthly = projected_daily * 22  # 22 iş günü
        print(f"─── 10 TRADE/GÜN PROJEKSİYONU ───")
        print(f"  Mevcut ort.        : {avg_trades_day:.1f} trade/gün → ${avg_daily_pnl:+.2f}/gün")
        print(f"  10 trade/gün ile   : ~${projected_daily:+.2f}/gün")
        print(f"  Aylık projeksiyon  : ~${projected_monthly:+.2f}/ay (22 iş günü)")
        print(f"  ⚠️ Bu lineer projeksiyon — gerçek sonuçlar farklı olabilir.")
        print()

    # ── BİLEŞİK BÜYÜME SİMÜLASYONU ──
    if EQUITY_LOT_ENABLED and total_active_days > 0:
        print(f"─── BİLEŞİK BÜYÜME SİMÜLASYONU (Equity Lot) ───")
        # Günlük getiri oranını hesapla (bakiyeye göre)
        daily_returns = []
        for day_str, d in sorted_days:
            daily_returns.append(d['pnl'])

        # Gerçek backtest compound etkisi zaten bakiyede yansıyor
        # Gelecek projeksiyonu: aynı günlük dağılım tekrar ederse
        sim_balance = balance  # Backtest sonu bakiye ile devam
        target_daily = 300.0
        target_reached_day = None

        print(f"  Backtest sonu bakiye: ${balance:.2f}")
        print(f"  Hedef günlük kâr   : ${target_daily:.0f}")
        print()

        # Ortalama günlük getiri ORANI (bakiyeye göre)
        avg_daily_return_pct = (net / initial_balance) / total_active_days * 100
        print(f"  Ort. günlük getiri : %{avg_daily_return_pct:.1f} (bakiyeye göre)")
        print()

        # İleriye dönük simülasyon: aynı oran devam ederse
        print(f"  {'GÜN':>4s} {'BAKİYE':>10s} {'LOT':>6s} {'TAHMİNİ KÂR':>12s} {'GÜNLÜK $':>10s}")
        print(f"  {'─'*4} {'─'*10} {'─'*6} {'─'*12} {'─'*10}")

        sim_b = balance
        milestones = [300, 500, 1000]
        milestone_hit = {}

        for day in range(1, 61):  # 60 gün ileriye projeksiyon
            # O günkü lot hesabı
            risk_amt = sim_b * (EQUITY_RISK_PCT / 100.0)
            day_lot = max(ACCOUNT_CONFIG['min_lot'], min(ACCOUNT_CONFIG['max_lot'], risk_amt / SL_DOLLARS))
            # Günlük tahmini kâr (aynı WR ve ort. trade/gün ile)
            avg_win_per_trade = avg_daily_pnl / max(1, avg_trades_day) if avg_daily_pnl > 0 else 0
            # Lot oranına göre scale
            original_avg_lot = 0.015  # backtest ortalaması yaklaşık
            lot_scale = day_lot / original_avg_lot if original_avg_lot > 0 else 1
            projected_day_pnl = avg_daily_pnl * lot_scale
            sim_b += projected_day_pnl

            # Milestone'ları kontrol et
            for ms in milestones:
                if ms not in milestone_hit and projected_day_pnl >= ms:
                    milestone_hit[ms] = day

            if target_reached_day is None and projected_day_pnl >= target_daily:
                target_reached_day = day

            # Her 5 günde bir veya milestone'da yazdır
            if day <= 5 or day % 5 == 0 or day in [target_reached_day]:
                print(f"  {day:4d} ${sim_b:9.2f} {day_lot:6.2f} {lot_scale:11.1f}x ${projected_day_pnl:9.2f}")

        print()
        if target_reached_day:
            print(f"  🎯 $300/gün hedefine tahminen {target_reached_day}. günde ulaşılır")
        else:
            print(f"  ⚠️ 60 gün içinde $300/gün hedefine ulaşılamadı")
            print(f"  → Lot max sınırı ({ACCOUNT_CONFIG['max_lot']}) darboğaz olabilir")
        print(f"  📊 60. gün tahmini bakiye: ${sim_b:.2f}")
        print()

# Trade listesi
if 0 < total_trades <= 60:
    print("─── TÜM İŞLEMLER ───")
    for idx_t, t in enumerate(trades, 1):
        icon = "✅" if t['pnl'] > 0 else "❌"
        lot_str = f"{t.get('lot', TRADE_LOT)}"
        print(f"  #{idx_t:2d} {icon} {t['type']:5s} | ${t['entry']:.2f} → ${t['exit']:.2f} | "
              f"P/L: ${t['pnl']:+.2f} | {t['result']:7s} | {t['pattern']} | "
              f"lot:{lot_str} B:{t['bars']} {t.get('timestamp','')}")
    print()

print("=" * 60)
if net >= 0:
    print(f"  ✅ STRATEJİ KÂRDA (${'+' if net>=0 else ''}{net:.2f}). WR: %{win_rate:.1f}")
else:
    print(f"  ⚠️ STRATEJİ ZARARDA (${net:.2f}). WR: %{win_rate:.1f}")
    be_wr = (SL_DOLLARS / (TP_DOLLARS + SL_DOLLARS)) * 100
    print(f"  ℹ️ Breakeven WR: %{be_wr:.0f} (R:R {TP_DOLLARS/SL_DOLLARS:.0f}:1)")
print("=" * 60)
print()
print("─── KULLANIM ───")
print(f"  python backtest_v5.py              → 30 gün, 5dk (default)")
print(f"  python backtest_v5.py --period 7   → 7 gün, 1dk")
print(f"  python backtest_v5.py --period 60  → 60 gün, 5dk")
print(f"  python backtest_v5.py --debug      → Kalıp detayları")
print()
print("─── BACKUP PLAN (gelecek iyileştirmeler) ───")
print(f"  ✅ Trailing stop (v5.1)")
print(f"  ✅ Güven lot boyutu (v5.1)")
print(f"  ✅ EMA trend filtresi (v5.1)")
print(f"  ✅ Ascending/Descending Triangle (v5.2)")
print(f"  ✅ Volume teyidi (v5.2)")
print(f"  1. Cup & Handle kalıbı ekle")
print(f"  2. Wedge (kama) kalıpları ekle")
print(f"  3. Multi-timeframe: 5dk'da kalıp tespit + 1dk'da giriş")
