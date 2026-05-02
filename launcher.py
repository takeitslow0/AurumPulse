"""AurumPulse masaüstü launcher — Flask backend + tarayıcıda otomatik aç.

Çalıştırma:
  python launcher.py        # geliştirme/normal kullanım
  pythonw launcher.py       # konsol penceresi olmadan

İlk açılışta %APPDATA%/AurumPulse/ (Windows) veya ~/.aurumpulse/
oluşturulur; veritabanı ve .env burada kalıcı.

Tarayıcı (varsayılan: Chrome/Edge/Firefox) yeni sekmede açılır.
Sekmeyi kapatınca arka plan çalışmaya devam eder. Tamamen durdurmak
için açtığın konsol penceresini kapat (veya pythonw ile sessiz
çalıştırdıysan: Görev Yöneticisi'nden 'pythonw.exe' sonlandır).
"""
from __future__ import annotations
import os
import sys
import socket
import threading
import time
import shutil
import webbrowser
import urllib.request
from pathlib import Path


def _user_data_dir() -> Path:
    if sys.platform == 'win32':
        base = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming'))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    p = base / 'AurumPulse'
    p.mkdir(parents=True, exist_ok=True)
    return p


def _bundle_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def _ensure_env_file(data_dir: Path) -> tuple[Path, bool]:
    """İlk çalıştırmada .env'i .env.example'dan kopyalar.
    Döner: (env_path, was_created_first_time)."""
    env_path = data_dir / '.env'
    created = False
    if not env_path.exists():
        sample = _bundle_dir() / '.env.example'
        if sample.exists():
            shutil.copy(sample, env_path)
            created = True
            print(f"[launcher] İLK AÇILIŞ — .env oluşturuldu: {env_path}")
            print(f"[launcher] Bu dosyayı editle, API anahtarlarını gir, sonra tekrar çalıştır.")
    return env_path, created


def _find_free_port() -> int:
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_server(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=0.5)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def main() -> int:
    # 1) Path'leri ayarla — BACKEND IMPORT'TAN ÖNCE
    data_dir = _user_data_dir()
    os.environ['AURUMPULSE_DATA_DIR'] = str(data_dir)

    if getattr(sys, 'frozen', False):
        os.chdir(_bundle_dir())

    env_path, first_run = _ensure_env_file(data_dir)
    if first_run:
        # İlk açılışta .env yeni oluşturuldu, key'ler boş — kullanıcı düzenlesin
        try:
            os.startfile(str(env_path))  # type: ignore[attr-defined]  (Windows-only)
        except Exception:
            pass
        input("\n.env dosyasını düzenledikten sonra ENTER'a bas (veya pencereyi kapat)... ")
        return 0

    # 2) Boş port + backend
    port = _find_free_port()
    os.environ['PORT'] = str(port)

    print(f"[launcher] AurumPulse başlatılıyor — port={port}")
    print(f"[launcher] Veri klasörü: {data_dir}")

    import backend

    def _run_server():
        try:
            backend.socketio.run(
                backend.app,
                host='127.0.0.1',
                port=port,
                debug=False,
                use_reloader=False,
                log_output=False,
                allow_unsafe_werkzeug=True,
            )
        except Exception as e:
            print(f"[launcher] Server crash: {e}")

    server_thread = threading.Thread(target=_run_server, daemon=True, name='flask-server')
    server_thread.start()

    if not _wait_for_server(port):
        print("[launcher] HATA: Backend 30 saniyede başlamadı.")
        input("ENTER'a bas çıkmak için... ")
        return 1

    url = f'http://127.0.0.1:{port}/'
    print(f"[launcher] ✅ Hazır → {url}")
    print(f"[launcher] Tarayıcı açılıyor... (sekmeyi kapatsan bile arka plan çalışır)")
    print(f"[launcher] Tamamen durdurmak için: bu pencereyi kapat (Ctrl+C)")

    # 3) Tarayıcıda aç
    try:
        webbrowser.open(url, new=2)  # 2 = new tab
    except Exception as e:
        print(f"[launcher] Tarayıcı açılamadı, manuel ziyaret et: {url} ({e})")

    # 4) Konsol açık kalsın, kullanıcı kapatınca dur
    try:
        while True:
            time.sleep(1)
            if not server_thread.is_alive():
                print("[launcher] Server thread öldü — çıkılıyor.")
                return 1
    except KeyboardInterrupt:
        print("\n[launcher] Ctrl+C — kapatılıyor...")
        try:
            backend._shutdown_event.set()
        except Exception:
            pass
        time.sleep(1)
        return 0


if __name__ == '__main__':
    sys.exit(main())
