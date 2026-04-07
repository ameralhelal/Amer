# -*- mode: python ; coding: utf-8 -*-
# تجميع نسخة ويندوز: pyinstaller --noconfirm CryptoTrading.spec
# الناتج: dist/CryptoTrading/ — انسخ المجلد كاملاً أو اضغطه ZIP للتوزيع
from PyInstaller.utils.hooks import collect_all

datas = [('theme_dark.qss', '.'), ('theme_light.qss', '.')]
binaries = []
hiddenimports = []

for pkg in ('PyQt6', 'sklearn', 'joblib', 'certifi'):
    try:
        tmp_ret = collect_all(pkg)
        datas += tmp_ret[0]
        binaries += tmp_ret[1]
        hiddenimports += tmp_ret[2]
    except Exception:
        pass


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CryptoTrading',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CryptoTrading',
)
