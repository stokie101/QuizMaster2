# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path.cwd()

# Debug builds show a console window with live errors/tracebacks (for tracing a
# crash). Set QUIZMASTER_DEBUG_CONSOLE=1 (scripts/build_quizmaster.ps1 -Console).
# Default is a windowed release build with no console.
DEBUG_CONSOLE = os.environ.get('QUIZMASTER_DEBUG_CONSOLE') == '1'

pyside_datas, pyside_binaries, pyside_hiddenimports = collect_all('PySide6')

# Sounds ship as loose data files (they are excluded from web_assets_bundle to
# avoid a multi-MB base64 string breaking the frozen build). resolve_sound_file
# loads them from here; the desktop app plays them via QMediaPlayer.
DATA_DIRS = [
    ('core/assets/sounds', 'core/assets/sounds'),
]

DATA_FILES = [
    ('config/production.env', 'config'),
    ('License.txt', '.'),
    ('THIRD_PARTY_LICENSES.txt', '.'),
]

_FORBIDDEN_PREFIXES = ('data', 'logs', 'avatar_cache', 'auth', 'cache', 'config/settings.ini')

datas = list(pyside_datas)
for source, target in DATA_DIRS + DATA_FILES:
    norm = source.replace('\\', '/')
    if any(norm == p or norm.startswith(p + '/') for p in _FORBIDDEN_PREFIXES):
        raise SystemExit(f"Refusing to bundle runtime/test data into the exe: {source!r}")
    if (ROOT / source).exists():
        datas.append((source, target))

hiddenimports = list(pyside_hiddenimports)
hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('fastapi')
hiddenimports += collect_submodules('socketio')
hiddenimports += collect_submodules('engineio')
hiddenimports += collect_submodules('tiktoklive')
hiddenimports += collect_submodules('keyring')
# websockets exposes connect() from a submodule it imports lazily, so a frozen
# build that collected only the top-level package raises ModuleNotFoundError the
# first time the hosted bridge dials out. The reconnect loop treats that as one
# more failed attempt, so the hosted control dock would work in development and
# silently do nothing in a release build.
hiddenimports += collect_submodules('websockets')
# ServiceRegistry imports app services by dotted strings from service_configurations.py.
# PyInstaller cannot reliably see those imports statically, so collect the app package.
hiddenimports += collect_submodules('core')
hiddenimports += collect_submodules('config')
hiddenimports += [
    'core.resources.web_assets_bundle',
    'core.resources.frontend_resources_rc',
    'core.services.subscription_gate',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebChannel',
]

excludes = [
    'pytest',
    'unittest',
    'tkinter',
    'IPython',
    'notebook',
    'matplotlib',
    'numpy.tests',
    'pandas.tests',
]

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[str(ROOT)],
    binaries=pyside_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['packaging/pyinstaller_release_runtime.py'],
    excludes=excludes,
    noarchive=False,
    optimize=2,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# One-folder build (exclude_binaries=True + COLLECT). QtWebEngine apps must NOT
# be one-file: the QtWebEngineProcess helper cannot reliably find its resources
# when everything self-extracts to a temp dir, which makes the app fail to launch.
# One-folder keeps all Qt/WebEngine files beside the exe in a stable layout; the
# Inno installer still wraps the whole folder into a single Setup.exe.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='QuizMaster',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=DEBUG_CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='core/assets/images/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='QuizMaster',
)
