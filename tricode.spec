# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Ensure tiktoken encodings and related resources are bundled
tiktoken_datas = collect_data_files('tiktoken')
tiktoken_hidden = collect_submodules('tiktoken')

# Some versions of tiktoken rely on tiktoken_ext dynamic bits
tiktoken_ext_datas = collect_data_files('tiktoken_ext')
tiktoken_ext_hidden = collect_submodules('tiktoken_ext')

# ddgs dynamically imports engine modules; ensure they are bundled
ddgs_hidden = collect_submodules('ddgs')


a = Analysis(
    ['tricode.py'],
    pathex=[],
    binaries=[],
    datas=tiktoken_datas + tiktoken_ext_datas,
    hiddenimports=tiktoken_hidden + tiktoken_ext_hidden + ddgs_hidden + ['tiktoken_ext.openai_public'],
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
