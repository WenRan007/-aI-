# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os

ROOT = Path(SPECPATH)
APP_SOURCE = Path(
    os.environ.get(
        "CHANGKE_APP_SOURCE",
        str(Path.home() / "Desktop" / "常客AI2.2"),
    )
)

a = Analysis(
    [str(ROOT / "online_updater.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(APP_SOURCE / "常客AI核心.exe"), "."),
        (str(ROOT / "dist" / "online_update_config.json"), "."),
    ],
    hiddenimports=["tkinter.filedialog", "tkinter.messagebox"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="常客AI在线更新器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
