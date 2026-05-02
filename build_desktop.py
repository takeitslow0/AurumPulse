"""PyInstaller ile AurumPulse.exe (Windows) / AurumPulse (Mac/Linux) build.

Çalıştırma:
  python build_desktop.py

Çıktı:
  dist/AurumPulse.exe  (Windows)
  dist/AurumPulse      (Mac/Linux)

Tek dosya, ~80-100MB. İçinde Python interpreter, Flask, PyWebView,
HTML şablonlar ve tüm bağımlılıklar var. Çift tıklayıp çalıştır.
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Build öncesi temizlik
for d in ('build', 'dist', '__pycache__'):
    p = ROOT / d
    if p.exists():
        print(f"[build] Temizleniyor: {p}")
        shutil.rmtree(p, ignore_errors=True)
for spec in ROOT.glob('*.spec'):
    spec.unlink(missing_ok=True)

# PyInstaller args
sep = ';' if sys.platform == 'win32' else ':'
args = [
    'pyinstaller',
    '--name=AurumPulse',
    '--onefile',
    '--windowed',  # console penceresi açılmasın (Windows GUI app)
    '--clean',
    f'--add-data=index.html{sep}.',
    f'--add-data=crypto.html{sep}.',
    f'--add-data=.env.example{sep}.',
    # Hidden imports — PyInstaller bazen bulamıyor
    '--hidden-import=engineio.async_drivers.threading',
    '--hidden-import=engineio.async_drivers.eventlet',
    '--hidden-import=simple_websocket',
    '--hidden-import=feedparser',
    '--hidden-import=webview.platforms.winforms',
    '--hidden-import=webview.platforms.cocoa',
    '--hidden-import=webview.platforms.gtk',
    '--collect-all=webview',
    '--collect-all=flask_socketio',
    '--collect-all=engineio',
    'launcher.py',
]

# Optional icon
icon_path = ROOT / 'icon.ico'
if icon_path.exists():
    args.insert(-1, f'--icon={icon_path}')

print(f"[build] Komut: {' '.join(args)}")
result = subprocess.run(args, cwd=str(ROOT))
if result.returncode != 0:
    print("[build] HATA: PyInstaller başarısız.")
    sys.exit(result.returncode)

dist_file = 'AurumPulse.exe' if sys.platform == 'win32' else 'AurumPulse'
out = ROOT / 'dist' / dist_file
if out.exists():
    print(f"\n[build] ✅ Başarılı! Çıktı: {out}")
    print(f"[build] Boyut: {out.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"[build] Çift tıklayıp çalıştırabilirsin.")
else:
    print("[build] HATA: Çıktı dosyası bulunamadı.")
    sys.exit(1)
