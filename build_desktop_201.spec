# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

ROOT = Path(SPECPATH).resolve()
datas = [(str(ROOT / 'assets'), 'assets')]
for package in ('faster_whisper', 'edge_tts'):
    datas += collect_data_files(package)
for package in ('faster-whisper', 'ctranslate2', 'edge-tts'):
    datas += copy_metadata(package)
binaries = collect_dynamic_libs('ctranslate2')

a = Analysis([str(ROOT / 'translator_desktop.py')], pathex=[str(ROOT)],
             binaries=binaries, datas=datas,
             hiddenimports=['faster_whisper', 'ctranslate2', 'edge_tts', 'av', 'PIL.ImageTk'],
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['argostranslate', 'torch', 'torchaudio', 'torchvision', 'transformers',
                       'ctranslate2.converters', 'ctranslate2.specs', 'tensorflow', 'scipy',
                       'spacy', 'pandas', 'matplotlib', 'IPython', 'pytest'],
             noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='常客AI', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          disable_windowed_traceback=False)
COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='常客AI')
