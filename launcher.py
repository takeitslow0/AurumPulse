"""AurumPulse masaüstü uygulaması — Flask backend + PyWebView native pencere.

Çalıştırma:
  python launcher.py        # geliştirme modunda
  AurumPulse.exe            # PyInstaller ile build edildikten sonra

İlk açılışta %APPDATA%/AurumPulse/ klasörü oluşturulur (Win) veya
~/.aurumpulse/ (Mac/Linux). Veritabanı ve .env burada saklanır.
"""
from __future__ import annotations
import os
import sys
import socket
import threading
import time
import shutil
import urllib.request
from pathlib import Path


# ─── Path setup (BACKEND IMPORT ÖNCESİ olmalı) ───
def _user_data_dir() -> Path:
    """Platform-uygun kullanıcı veri klasörü."""
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
    """PyInstaller içindeyken kaynak dosyaların bulunduğu klasör."""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def _ensure_env_file(data_dir: Path) -> Path:
    """İlk çalıştırmada .env dosyasını .env.example'dan kopyalar."""
    env_path = data_dir / '.env'
    if not env_path.exists():
        sample = _bundle_dir() / '.env.example'
        if sample.exists():
            shutil.copy(sample, env_path)
            print(f"[launcher] İlk çalıştırma: .env oluşturuldu → {env_path}")
            print(f"[launcher] Lütfen API anahtarlarını düzenleyin ve tekrar açın.")
    return env_path


def _find_free_port() -> int:
    """OS'tan boş bir port iste."""
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_server(port: int, timeout: float = 30.0) -> bool:
    """Backend health endpoint'inin yanıt vermesini bekle."""
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

    # PyInstaller bundle içinde HTML dosyalarını bulmak için
    if getattr(sys, 'frozen', False):
        os.chdir(_bundle_dir())

    # .env hazırla
    _ensure_env_file(data_dir)

    # 2) Boş port + backend'i thread'de başlat
    port = _find_free_port()
    os.environ['PORT'] = str(port)

    # NOT: backend.py import edilirken thread'leri başlatıyor (top-level threading.Thread).
    # Bu yüzden import sadece bir kere ve port set edildikten sonra olmalı.
    print(f"[launcher] AurumPulse başlatılıyor — port={port}, data={data_dir}")
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
        return 1

    print(f"[launcher] Backend hazır → http://127.0.0.1:{port}/")

    # 3) PyWebView ile native pencere
    try:
        import webview
    except ImportError:
        print("[launcher] HATA: pywebview yüklü değil. Çalıştır: pip install pywebview")
        return 2

    window = webview.create_window(
        'AurumPulse — XAU/USD Sinyal Terminali',
        f'http://127.0.0.1:{port}/',
        width=1600,
        height=900,
        min_size=(1024, 600),
        confirm_close=False,
    )

    def _on_close():
        print("[launcher] Pencere kapanıyor — backend durduruluyor...")
        try:
            backend._shutdown_event.set()
        except Exception:
            pass

    window.events.closed += _on_close

    webview.start(debug=False)

    # PyWebView dönüşü = pencere kapandı
    backend._shutdown_event.set()
    print("[launcher] Çıkıldı.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
