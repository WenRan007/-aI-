# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('assets', 'assets'), ('translator_desktop_payload.bin', '.')]
binaries = []
# faster-whisper/ctranslate2 的 CUDA 运行库（随软件分发，缺少时程序会自动回退 CPU）
for dll in ('cublas64_12.dll', 'cublasLt64_12.dll', 'nvblas64_12.dll'):
    binaries.append((r'D:\cuda_pkg\cublas_extract\nvidia\cublas\bin' + '\\' + dll, 'ctranslate2'))
hiddenimports = ['desktop_account', 'desktop_account_ui', 'translation_highlights', 'argostranslate.translate', 'argostranslate.settings', 'tkinter.filedialog', 'tkinter.messagebox', 'tkinter.ttk']
for package in ('faster_whisper', 'ctranslate2', 'edge_tts'):
    tmp_ret = collect_all(package)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

a = Analysis(['translator_bootstrap.py'], pathex=[], binaries=binaries, datas=datas, hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='常客AI', debug=False, bootloader_ignore_signals=False, strip=False, upx=True, console=False, disable_windowed_traceback=False, argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None)
COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, upx_exclude=[], name='常客AI')
