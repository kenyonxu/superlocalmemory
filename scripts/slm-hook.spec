# -*- mode: python ; coding: utf-8 -*-
# LLD reference: .backup/active-brain/lld/LLD-06-windows-binary-and-legacy-migration.md §4.2
# Mode: onedir (not onefile) — LLD-06 §4.1, verification claim 7.
# console=False, upx=False, strip=True.

block_cipher = None

a = Analysis(
    ['../src/superlocalmemory/hook_binary_entry.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Deny-list — anything imported here would blow up cold-start and size.
        'torch', 'sentence_transformers', 'fastapi', 'uvicorn',
        'numpy', 'scipy', 'lightgbm', 'onnxruntime', 'transformers',
        'httpx', 'mcp', 'pydantic', 'lark',
        'superlocalmemory.core.engine',
        'superlocalmemory.core.embeddings',
        'superlocalmemory.retrieval',
        'superlocalmemory.learning',
    ],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='slm-hook',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,                 # UPX adds 30-60 ms cold-start + AV heuristics
    console=False,             # no console flash on Windows
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,    # handled in scripts/sign-macos.sh post-build
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=True,
    upx=False,
    name='slm-hook',
)
