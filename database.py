import sqlite3
from datetime import datetime

# Veritabanı dosyasının adı
DB_NAME = 'aurumpulse.db'

def init_db():
    """Veritabanını ve gerekli tabloları oluşturur. Varsa dokunmaz."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Altın ve DXY fiyatlarını saklayacağımız tablo
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gold_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                price REAL,
                rsi REAL,
                dxy REAL
            )
        ''')
        conn.commit()
    except sqlite3.Error as e:
        print(f"❌ Veritabanı Başlatma Hatası: {e}")
    finally:
        # İşlem bitince veya hata olunca bağlantıyı mutlaka kapatıyoruz (Memory leak önlemek için)
        if conn:
            conn.close()

def save_market_data(price, rsi, dxy):
    """Yeni piyasa verilerini veritabanına kaydeder."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO gold_history (price, rsi, dxy)
            VALUES (?, ?, ?)
        ''', (price, rsi, dxy))
        
        conn.commit()
    except sqlite3.Error as e:
        print(f"❌ Veri Kaydetme Hatası: {e}")
    finally:
        if conn:
            conn.close()

def get_recent_history(limit=50):
    """Geçmiş verileri tarihe göre azalan (en yeni en üstte) sırayla çeker."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM gold_history ORDER BY timestamp DESC LIMIT ?', (limit,))
        data = cursor.fetchall()
        return data
    except sqlite3.Error as e:
        print(f"❌ Veri Çekme Hatası: {e}")
        return []  # Hata durumunda kodun çökmemesi için boş liste döndür
    finally:
        if conn:
            conn.close()

# Eğer bu dosya doğrudan terminalden çalıştırılırsa test et
if __name__ == "__main__":
    init_db()
    print(f"✅ Veritabanı '{DB_NAME}' başarıyla kontrol edildi ve kullanıma hazır!")