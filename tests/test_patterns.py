"""Pattern detection birim testleri — backtest_v5 fonksiyonları için.

Sentetik fiyat serileri üreterek her pattern'in:
  (a) tetiklenmesi gereken senaryoda tetiklendiğini,
  (b) düz/noisy fiyatta false positive üretmediğini doğrular.

Çalıştırma: `python -m pytest tests/` veya `python tests/test_patterns.py`
Pytest zorunlu değil — basit assert'lerle de çalışır.
"""
import os
import sys
import numpy as np
import pandas as pd

# backtest_v5'i root'tan import et
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backtest_v5 import (
    find_swings,
    detect_double_bottom,
    detect_double_top,
    detect_head_shoulders,
    detect_inv_head_shoulders,
    detect_ascending_triangle,
    detect_descending_triangle,
    detect_patterns,
    SWING_WINDOW,
)


def _make_df(highs, lows, closes=None, opens=None, atr=1.0):
    """OHLC DataFrame oluştur. close varsayılan=low+high ortalaması.
    ATR sütunu detect_flag için gerekli."""
    n = len(highs)
    if closes is None:
        closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    if opens is None:
        opens = closes[:]
    return pd.DataFrame({
        'Open': opens, 'High': highs, 'Low': lows, 'Close': closes,
        'Volume': [1000] * n,
        'ATR': [atr] * n,
    })


def _flat_series(n, price=2000.0, jitter=0.5):
    """Düşük volatilite düz fiyat serisi — hiçbir pattern tetiklenmemeli."""
    rng = np.random.default_rng(42)
    highs, lows = [], []
    for _ in range(n):
        mid = price + rng.normal(0, jitter)
        highs.append(mid + abs(rng.normal(0, jitter / 2)))
        lows.append(mid - abs(rng.normal(0, jitter / 2)))
    return highs, lows


# ═════════════════════════════════════════
# TEST: find_swings çalışıyor mu?
# ═════════════════════════════════════════

def test_find_swings_detects_peaks_and_troughs():
    """Belirgin tepe ve dipler bulunmalı."""
    # 0..30 aralığında: 10. bar'da tepe, 20. bar'da dip
    highs = [2000.0] * 30
    lows = [1999.0] * 30
    for i in range(5, 10):
        highs[i] = 2000 + (i - 5) * 2   # yükselen
    highs[10] = 2015  # tepe
    for i in range(11, 16):
        highs[i] = 2015 - (i - 10) * 2  # alçalan

    for i in range(15, 20):
        lows[i] = 1999 - (i - 15) * 2
    lows[20] = 1989  # dip
    for i in range(21, 26):
        lows[i] = 1989 + (i - 20) * 2

    df = _make_df(highs, lows)
    sh, sl = find_swings(df, idx=28, lookback=28)
    assert any(abs(p - 2015) < 0.1 for _, p in sh), f"Tepe bulunamadı: {sh}"
    assert any(abs(p - 1989) < 0.1 for _, p in sl), f"Dip bulunamadı: {sl}"


# ═════════════════════════════════════════
# TEST: Double bottom senaryosu
# ═════════════════════════════════════════

def test_double_bottom_triggers_on_w_shape():
    """İki benzer dip + kırılım → DOUBLE_BOTTOM tetiklenmeli."""
    # W şekli: başta düşüş, dip1, tepe (neckline), dip2, kırılım yukarı
    pattern = []
    # Düşüş: 2020 → 1980
    for i in range(10):
        pattern.append(2020 - i * 4)
    # Dip1 @ 1980 (index 10)
    # Yükselme: 1980 → 2010
    for i in range(10):
        pattern.append(1980 + i * 3)
    # Tepe @ 2010 (index 20) = neckline
    # Düşüş: 2010 → 1981 (dip2, dip1'e çok yakın)
    for i in range(10):
        pattern.append(2010 - i * 3)
    # Dip2 @ 1980 (index 30)
    # Kırılım yukarı: 1981 → 2020
    for i in range(10):
        pattern.append(1981 + i * 4)

    highs = [p + 1 for p in pattern]
    lows = [p - 1 for p in pattern]
    df = _make_df(highs, lows, closes=pattern)

    sh, sl = find_swings(df, idx=len(df) - 1, lookback=40)
    current_price = pattern[-1]  # 2020 — neckline 2010'u aştı
    atr = 5.0  # tolerance = 0.25*5 = 1.25 → 1pt high/low wrap'ini tolere eder

    result = detect_double_bottom(sl, sh, current_price, atr, len(df) - 1)
    assert result is not None, f"Double bottom tetiklenmedi. swings: sl={sl}, sh={sh}"
    assert result['direction'] == 'LONG'
    assert result['pattern'] == 'DOUBLE_BOTTOM'


# ═════════════════════════════════════════
# TEST: False positive — düz fiyatta pattern olmamalı
# ═════════════════════════════════════════

def test_no_pattern_on_flat_noise():
    """Düz/noisy serilerde detect_patterns None döndürmeli."""
    highs, lows = _flat_series(80)
    df = _make_df(highs, lows)
    atr = 0.5
    result = detect_patterns(df, idx=len(df) - 1, atr=atr)
    assert result is None, f"Düz fiyatta pattern oluştu: {result}"


# ═════════════════════════════════════════
# TEST: Pattern fonksiyonları edge case'lerde crash etmiyor
# ═════════════════════════════════════════

def test_patterns_handle_empty_swings():
    """Swings boş olduğunda exception atmamalı, None dönmeli."""
    assert detect_double_bottom([], [], 2000, 1.0, 10) is None
    assert detect_double_top([], [], 2000, 1.0, 10) is None
    assert detect_head_shoulders([], [], 2000, 1.0, 10) is None
    assert detect_inv_head_shoulders([], [], 2000, 1.0, 10) is None
    assert detect_ascending_triangle([], [], 2000, 1.0, 10) is None
    assert detect_descending_triangle([], [], 2000, 1.0, 10) is None


def test_patterns_handle_single_swing():
    """Tek swing ile de crash olmamalı."""
    sh = [(10, 2010.0)]
    sl = [(15, 1990.0)]
    assert detect_double_bottom(sl, sh, 2000, 1.0, 20) is None
    assert detect_double_top(sh, sl, 2000, 1.0, 20) is None


def test_detect_patterns_with_short_df():
    """Çok kısa DataFrame'de patologya değil — None dönmeli."""
    df = _make_df([2000] * 5, [1999] * 5)
    result = detect_patterns(df, idx=4, atr=1.0)
    assert result is None


# ═════════════════════════════════════════
# Ana: pytest yoksa manuel çalıştır
# ═════════════════════════════════════════

if __name__ == '__main__':
    tests = [
        test_find_swings_detects_peaks_and_troughs,
        test_double_bottom_triggers_on_w_shape,
        test_no_pattern_on_flat_noise,
        test_patterns_handle_empty_swings,
        test_patterns_handle_single_swing,
        test_detect_patterns_with_short_df,
    ]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"✅ {t.__name__}")
        except AssertionError as e:
            print(f"❌ {t.__name__}: {e}")
            failures += 1
        except Exception as e:
            print(f"💥 {t.__name__} — exception: {type(e).__name__}: {e}")
            failures += 1
    print(f"\nSonuç: {len(tests) - failures}/{len(tests)} geçti")
    sys.exit(0 if failures == 0 else 1)
