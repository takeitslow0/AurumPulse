import yfinance as yf
import pandas as pd
from flask import Flask, jsonify
from flask_cors import CORS
import threading
import time
from database import init_db, save_market_data, get_recent_history

app = Flask(__name__)
CORS(app)

# Veritabanını Başlat
init_db()

# RSI Hesaplama Fonksiyonu
def calculate_rsi(data, window=14):
    delta = data.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=window - 1, adjust=False).mean()
    ema_down = down.ewm(com=window - 1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

# Arka Plan Veri Kaydedici
def background_scanner():
    print("🕵️ Arka plan veri kaydedici (Worker) aktif...")
    while True:
        try:
            gold_df = yf.Ticker("GC=F").history(period="1d", interval="1m")
            dxy_df = yf.Ticker("DX-Y.NYB").history(period="1d", interval="1m")
            
            if not gold_df.empty:
                current_price = round(gold_df['Close'].iloc[-1], 2)
                current_rsi = round(calculate_rsi(gold_df['Close']).iloc[-1], 2)
                current_dxy = round(dxy_df['Close'].iloc[-1], 2) if not dxy_df.empty else 0
                
                save_market_data(current_price, current_rsi, current_dxy)
                print(f"💾 [DB Kayıt] Fiyat: ${current_price} | RSI: {current_rsi}")
        except Exception as e:
            print(f"⚠️ Kayıt Hatası: {e}")
            
        time.sleep(60)

worker_thread = threading.Thread(target=background_scanner, daemon=True)
worker_thread.start()

@app.route('/api/market_data')
def get_market_data():
    try:
        # Verileri Çek
        gold_df = yf.Ticker("GC=F").history(period="1d", interval="1m")
        dxy_df = yf.Ticker("DX-Y.NYB").history(period="1d", interval="1m")

        if gold_df.empty:
            return jsonify({"error": "Altın verisi çekilemedi"}), 500

        # Teknik Analiz
        gold_df['RSI'] = calculate_rsi(gold_df['Close'])
        last_row = gold_df.iloc[-1]

        # GARANTİLİ KORELASYON NOTU (Hatayı düzelttiğimiz yer)
        market_note = "Piyasa analizi yapılıyor..."
        if not dxy_df.empty and len(dxy_df) >= 2:
            first_val = dxy_df['Close'].iloc[0] # Günün ilk verisi
            last_val = dxy_df['Close'].iloc[-1] # Günün son verisi
            
            if last_val > first_val:
                market_note = "⚠️ Dolar Endeksi (DXY) bugün yükselişte. Altın fiyatı üzerinde satış baskısı olabilir."
            elif last_val < first_val:
                market_note = "✅ Dolar Endeksi (DXY) bugün düşüşte. Bu durum altın fiyatını destekliyor."
            else:
                market_note = "⚖️ Dolar yatay seyrediyor. Altın kendi teknik seviyelerine göre hareket edecektir."
        elif not dxy_df.empty:
            market_note = "Piyasa verileri toplanıyor, trend henüz netleşmedi."

        # Mum Grafiği Verileri
        candles = []
        for index, row in gold_df.tail(35).iterrows():
            candles.append({
                'x': int(index.timestamp() * 1000),
                'y': [round(row['Open'], 2), round(row['High'], 2), round(row['Low'], 2), round(row['Close'], 2)]
            })

        # Veritabanı Kayıt Sayısı
        db_records = get_recent_history(10000)
        history_count = len(db_records) if db_records else 0

        # JSON Yanıtı
        return jsonify({
            "gold_price": round(last_row['Close'], 2),
            "gold_rsi": round(last_row['RSI'], 2),
            "dxy_price": round(dxy_df['Close'].iloc[-1], 2) if not dxy_df.empty else "N/A",
            "market_note": market_note,
            "candles": candles,
            "history_count": history_count
        })

    except Exception as e:
        print(f"API Hatası: {e}")
        return jsonify({"error": "Sunucu içi bir hata oluştu"}), 500

if __name__ == '__main__':
    print("🚀 AurumPulse Engine v2.0 Başlatıldı!")
    app.run(debug=True, port=5000, use_reloader=False)