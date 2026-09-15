# -*- mode: python ; coding: utf-8 -*-
# Build (paths below are relative to THIS spec file, i.e. the build/ folder):
#   pyinstaller build/SmartFileOrganizer.spec --distpath build/dist --workpath build/work
#
# Produces a single windowed SmartFileOrganizer.exe that bundles the default
# rules and icon. pypdf / python-docx are pulled in as hidden imports so the
# packaged app can search PDF and Word content (they're imported lazily).

a = Analysis(
    ['../app/organizer_gui.py'],
    pathex=['../app'],
    binaries=[],
    datas=[
        ('../app/rules.json', '.'),
        ('../app/organizer.ico', '.'),
    ],
    hiddenimports=['pypdf', 'docx'],
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
    name='SmartFileOrganizer',
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
    icon=['../app/organizer.ico'],
)
