# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = [
    'pystray._win32',
    'PIL._tkinter_finder',
    'buzz_ingest',
    'collection_runtime',
    'collection_sync_state',
    'engagement_correlation',
    'engagement_dashboard',
]
hiddenimports += collect_submodules('pystray')


a = Analysis(
    ['tracker_app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('config.example.json', '.'),
        ('README.md', '.'),
        ('DASHBOARD_GUIDE.md', '.'),
    ],
    hiddenimports=hiddenimports,
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
    [],
    exclude_binaries=True,
    name='CivitAITracker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CivitAITracker',
)
