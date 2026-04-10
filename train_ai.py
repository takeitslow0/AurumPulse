import pandas as pd
import numpy as np
import yfinance as yf
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score
import joblib
import warnings
warnings.filterwarnings('ignore')

print("⏳ Adım 1/4: Eğitim Verisi İndiriliyor...")
try:
    df = yf.download("GC=F", interval="1m", period="7d", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.dropna(inplace=True)
except Exception as e:
    print(f"❌ Veri Hatası: {e}")
    exit()

print("🧮 Adım 2/4: İleri Seviye (Feature Engineering) Özellikler Çıkarılıyor...")

# TEMEL İNDİKATÖRLER
df['MA20'] = df['Close'].rolling(window=20).mean()
df['MA50'] = df['Close'].rolling(window=50).mean()

tp = (df['High'] + df['Low'] + df['Close']) / 3
vol = df['Volume'].replace(0, np.nan).fillna((df['High'] - df['Low']) * 1000)
df['VWAP'] = (tp * vol).rolling(window=20).sum() / vol.rolling(window=20).sum()

delta = df['Close'].diff()
gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
loss = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
df['RSI'] = 100 - (100 / (1 + gain / loss))

ema_fast = df['Close'].ewm(span=12, adjust=False).mean()
ema_slow = df['Close'].ewm(span=26, adjust=False).mean()
df['MACD'] = ema_fast - ema_slow
df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

high_low = df['High'] - df['Low']
high_close = np.abs(df['High'] - df['Close'].shift())
low_close = np.abs(df['Low'] - df['Close'].shift())
df['ATR'] = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).rolling(14).mean()

# 🧠 YENİ: İLERİ SEVİYE YAPAY ZEKA ÖZELLİKLERİ (FEATURES) 🧠
# 1. Eğim (Slopes) - Hız ve Momentum
df['RSI_Slope'] = df['RSI'] - df['RSI'].shift(3)
df['MACD_Hist_Slope'] = df['MACD_Hist'] - df['MACD_Hist'].shift(3)

# 2. Fiyat İvmesi (Price Momentum)
df['Mom_3m'] = df['Close'].pct_change(3) * 100
df['Mom_5m'] = df['Close'].pct_change(5) * 100

# 3. Mesafeler (Fiyatın ortalamalardan ne kadar saptığı)
df['Dist_VWAP'] = (df['Close'] - df['VWAP']) / df['VWAP'] * 100
df['Dist_MA20'] = (df['Close'] - df['MA20']) / df['MA20'] * 100
df['Dist_MA50'] = (df['Close'] - df['MA50']) / df['MA50'] * 100

# 4. Volatilite (Bollinger Sıkışması)
sma20 = df['Close'].rolling(20).mean()
std20 = df['Close'].rolling(20).std()
df['BB_Width'] = (std20 * 4) / sma20 * 100

# 🎯 HEDEF (TARGET) DÜZELTİLDİ: SADECE NET YÜKSELİŞLERİ TAHMİN ET
# "3 mum sonraki kapanış, şu anki fiyattan + 0.2x ATR kadar yüksek mi?"
df['Target'] = (df['Close'].shift(-3) > (df['Close'] + (df['ATR'] * 0.2))).astype(int)

df.dropna(inplace=True)

# Yapay Zeka bu 10 farklı veriye bakarak karar verecek
features = ['RSI', 'RSI_Slope', 'MACD_Hist', 'MACD_Hist_Slope', 
            'Dist_VWAP', 'Dist_MA20', 'Dist_MA50', 'Mom_3m', 'Mom_5m', 'BB_Width']
X = df[features]
y = df['Target']

print("🧠 Adım 3/4: Hiper-Parametreleri Optimize Edilmiş Model Eğitiliyor...")
# Zaman Serisi verilerinde shuffle=False ÇOK ÖNEMLİDİR. Geleceği geçmişe sızdırmayız.
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, shuffle=False)

# Daha büyük bir orman (1000 ağaç) ve class_weight='balanced' (dengesizliği çözer)
model = RandomForestClassifier(n_estimators=1000, max_depth=12, min_samples_leaf=5, class_weight='balanced', random_state=42, n_jobs=-1)
model.fit(X_train, y_train)

print("📈 Adım 4/4: Model Test Ediliyor...\n")
predictions = model.predict(X_test)
accuracy = accuracy_score(y_test, predictions)

print("═════════════════════════════════════════════════════")
print("🤖 YENİ NESİL YAPAY ZEKA (AI) EĞİTİM RAPORU")
print("═════════════════════════════════════════════════════")
print(f"Model Doğruluğu (Accuracy) : % {accuracy * 100:.2f}")
print("─────────────────────────────────────────────────────")
print(classification_report(y_test, predictions, target_names=["DÜŞÜŞ/YATAY (0)", "GÜÇLÜ YÜKSELİŞ (1)"]))
print("═════════════════════════════════════════════════════")

joblib.dump(model, 'aurumpulse_ai_model.pkl')
print("\n✅ Yeni Zeka başarıyla eğitildi ve 'aurumpulse_ai_model.pkl' güncellendi!")