import sqlite3
from datetime import datetime

DB_NAME = 'aurumpulse.db'


def init_db():
    """Veritabanını ve gerekli tabloları + index'leri oluşturur."""
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gold_history (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                price     REAL,
                rsi       REAL,
                dxy       REAL
            )
        ''')

        # Zaman sorgularını hızlandıran index (büyük veride kritik)
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_timestamp
            ON gold_history (timestamp DESC)
        ''')

        # Trade geçmişi tablosu
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trade_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                open_time   INTEGER,
                close_time  INTEGER,
                trend       TEXT,
                entry       REAL,
                exit_price  REAL,
                sl          REAL,
                tp1         REAL,
                tp2         REAL,
                result      TEXT,
                pnl         REAL,
                tp1_hit     INTEGER DEFAULT 0,
                pattern     TEXT DEFAULT '',
                lot         REAL DEFAULT 0.01
            )
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_trade_close
            ON trade_history (close_time DESC)
        ''')

        # Açık pozisyonlar — restart'ta korunsun diye persist edilir.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS active_positions (
                open_time    INTEGER PRIMARY KEY,
                trend        TEXT,
                entry        REAL,
                sl           REAL,
                tp1          REAL,
                tp2          REAL,
                tp1_hit      INTEGER DEFAULT 0,
                lot          REAL DEFAULT 0.01,
                pattern      TEXT DEFAULT '',
                data_json    TEXT
            )
        ''')

        conn.commit()
        print(f"✅ Veritabanı '{DB_NAME}' hazır.")

    except sqlite3.Error as e:
        print(f"❌ Veritabanı Başlatma Hatası: {e}")
    finally:
        if conn:
            conn.close()


def save_market_data(price, rsi, dxy):
    """Yeni piyasa verisini kaydeder."""
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO gold_history (price, rsi, dxy) VALUES (?, ?, ?)',
            (price, rsi, dxy)
        )
        conn.commit()
    except sqlite3.Error as e:
        print(f"❌ Veri Kaydetme Hatası: {e}")
    finally:
        if conn:
            conn.close()


def get_recent_history(limit=100, count_only=False):
    """
    Geçmiş verileri döner.
    count_only=True → sadece toplam kayıt sayısını int olarak döner (hafızaya 10k satır yüklemez).
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        if count_only:
            cursor.execute('SELECT COUNT(*) FROM gold_history')
            return cursor.fetchone()[0]

        cursor.execute(
            'SELECT * FROM gold_history ORDER BY timestamp DESC LIMIT ?',
            (limit,)
        )
        return cursor.fetchall()

    except sqlite3.Error as e:
        print(f"❌ Veri Çekme Hatası: {e}")
        return 0 if count_only else []
    finally:
        if conn:
            conn.close()


def get_history_paginated(page=1, per_page=50):
    """
    Sayfalı geçmiş sorgusu — büyük veri setleri için.
    Döner: {'records': [...], 'total': int, 'page': int, 'pages': int}
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) FROM gold_history')
        total = cursor.fetchone()[0]

        offset = (page - 1) * per_page
        cursor.execute(
            'SELECT * FROM gold_history ORDER BY timestamp DESC LIMIT ? OFFSET ?',
            (per_page, offset)
        )
        records = cursor.fetchall()

        return {
            'records': records,
            'total':   total,
            'page':    page,
            'pages':   (total + per_page - 1) // per_page
        }

    except sqlite3.Error as e:
        print(f"❌ Sayfalı Veri Çekme Hatası: {e}")
        return {'records': [], 'total': 0, 'page': page, 'pages': 0}
    finally:
        if conn:
            conn.close()


def purge_old_records(keep_days=30):
    """
    30 günden eski kayıtları siler — DB şişmesini önler.
    Cron ile veya manuel çağrılabilir.
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM gold_history WHERE timestamp < datetime('now', ?)",
            (f'-{keep_days} days',)
        )
        deleted = cursor.rowcount
        conn.commit()
        print(f"🗑️ {deleted} eski kayıt silindi.")
        return deleted
    except sqlite3.Error as e:
        print(f"❌ Temizleme Hatası: {e}")
        return 0
    finally:
        if conn:
            conn.close()


def save_trade(trade: dict):
    """Kapanan trade'i veritabanına kaydeder."""
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO trade_history
                (open_time, close_time, trend, entry, exit_price,
                 sl, tp1, tp2, result, pnl, tp1_hit, pattern, lot)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            trade.get('open_time', 0),
            trade.get('close_time', 0),
            trade.get('trend', ''),
            trade.get('entry', 0),
            trade.get('exit_price', 0),
            trade.get('sl', 0),
            trade.get('tp1', 0),
            trade.get('tp2', 0),
            trade.get('result', ''),
            trade.get('pnl', 0),
            1 if trade.get('tp1_hit') else 0,
            trade.get('pattern', ''),
            trade.get('lot', 0.01),
        ))
        conn.commit()
    except sqlite3.Error as e:
        print(f"❌ Trade Kaydetme Hatası: {e}")
    finally:
        if conn:
            conn.close()


def load_all_trades():
    """
    Tüm trade geçmişini DB'den yükler.
    Backend başlangıcında _trade_history listesini doldurmak için kullanılır.
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT open_time, close_time, trend, entry, exit_price,
                   sl, tp1, tp2, result, pnl, tp1_hit, pattern, lot
            FROM trade_history
            ORDER BY close_time ASC
        ''')
        rows = cursor.fetchall()
        trades = []
        for r in rows:
            trades.append({
                'open_time':  r[0],
                'close_time': r[1],
                'trend':      r[2],
                'entry':      r[3],
                'exit_price': r[4],
                'sl':         r[5],
                'tp1':        r[6],
                'tp2':        r[7],
                'result':     r[8],
                'pnl':        r[9],
                'tp1_hit':    bool(r[10]),
                'pattern':    r[11] or '',
                'lot':        r[12] or 0.01,
            })
        print(f"📂 DB'den {len(trades)} trade yüklendi.")
        return trades
    except sqlite3.Error as e:
        print(f"❌ Trade Yükleme Hatası: {e}")
        return []
    finally:
        if conn:
            conn.close()


def save_active_positions(positions: list):
    """Açık pozisyonları DB'ye replace-all stratejisi ile yazar.
    Max 3 pozisyon olduğu için delete+insert maliyeti ihmal edilebilir.
    Her mutation (append/pop/update) sonrası çağrılır.
    """
    conn = None
    try:
        import json
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM active_positions')
        for pos in positions:
            cursor.execute('''
                INSERT INTO active_positions
                    (open_time, trend, entry, sl, tp1, tp2, tp1_hit, lot, pattern, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                int(pos.get('open_time', 0)),
                pos.get('trend', ''),
                float(pos.get('entry', 0)),
                float(pos.get('sl', 0)),
                float(pos.get('tp1', 0)),
                float(pos.get('tp2', 0)),
                1 if pos.get('tp1_hit') else 0,
                float(pos.get('lot', 0.01)),
                pos.get('pattern', ''),
                json.dumps(pos, default=str),
            ))
        conn.commit()
    except sqlite3.Error as e:
        print(f"❌ Aktif pozisyon kaydetme hatası: {e}")
    finally:
        if conn:
            conn.close()


def load_active_positions():
    """Restart sonrası açık pozisyonları DB'den yükler."""
    conn = None
    try:
        import json
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT data_json FROM active_positions')
        rows = cursor.fetchall()
        positions = []
        for (blob,) in rows:
            try:
                positions.append(json.loads(blob))
            except (json.JSONDecodeError, TypeError):
                continue
        return positions
    except sqlite3.Error as e:
        print(f"❌ Aktif pozisyon yükleme hatası: {e}")
        return []
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    init_db()
    count = get_recent_history(count_only=True)
    print(f"📊 Toplam kayıt sayısı: {count}")
    trades = load_all_trades()
    print(f"📊 Toplam trade sayısı: {len(trades)}")