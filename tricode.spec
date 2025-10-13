# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Collect tiktoken_ext data files and submodules
tiktoken_datas = collect_data_files('tiktoken_ext')
tiktoken_hiddenimports = collect_submodules('tiktoken_ext')

a = Analysis(
    ['tricode.py'],
    pathex=[],
    binaries=[],
    datas=tiktoken_datas,
    hiddenimports=tiktoken_hiddenimports + ['tiktoken_ext.openai_public'],
    hookspath=['.'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='tricode',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
