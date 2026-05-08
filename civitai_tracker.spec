# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = [
    'requests',
    'pystray._win32',
    'PIL._tkinter_finder',
    'buzz_ingest',
    'collection_runtime',
    'collection_sync_state',
    'engagement_correlation',
    'engagement_dashboard',
]
hiddenimports += collect_submodules('pystray')
hiddenimports += collect_submodules('customtkinter')

exe_icon = 'assets/app_icon.ico' if Path('assets/app_icon.ico').exists() else None
datas = [
    ('config.example.json', '.'),
    ('README.md', '.'),
    ('DASHBOARD_GUIDE.md', '.'),
    ('UPDATE_GUIDE.md', '.'),
]
if Path('assets/app_icon.png').exists():
    datas.append(('assets/app_icon.png', 'assets'))
if Path('assets/app_icon.ico').exists():
    datas.append(('assets/app_icon.ico', 'assets'))
if Path('assets/fonts').exists():
    datas.append(('assets/fonts', 'assets/fonts'))
datas += collect_data_files('customtkinter')


a = Analysis(
    ['tracker_app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
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
    icon=exe_icon,
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
