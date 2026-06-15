# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

block_cipher = None

project_root = Path(__file__).parent.resolve()
sys.path.insert(0, str(project_root))

a = Analysis(
    ['main.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / 'ui' / 'web_editor' / 'editor.html'), 'ui/web_editor'),
        (str(project_root / 'ui' / 'web_editor' / 'editor.js'), 'ui/web_editor'),
        (str(project_root / 'ui' / 'web_editor' / 'vendor' / 'monaco'), 'ui/web_editor/vendor/monaco'),
    ],
    hiddenimports=[
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebChannel',
        'chromadb',
        'chromadb.db.impl.sqlite',
        'chromadb.telemetry.product.posthog',
        'pypika',
        'tokenizers',
        'loguru',
        'pydantic',
        'yaml',
        'pathspec',
        'duckduckgo_search',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'scipy',
        'tkinter',
        'pytest',
        'PIL',
        'cv2',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='OlliDesk',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
