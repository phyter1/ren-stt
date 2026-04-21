# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for RenSTT.app

import os

REPO = SPECPATH

a = Analysis(
    [os.path.join(REPO, 'stt-cli.py')],
    pathex=[REPO],
    datas=[
        (os.path.join(REPO, 'config.py'), '.'),
        (os.path.join(REPO, 'stt-menubar.py'), '.'),
        (os.path.join(REPO, 'stt-server.py'), '.'),
        (os.path.join(REPO, 'config.example.json'), '.'),
        (os.path.join(REPO, 'requirements-server.txt'), '.'),
        (os.path.join(REPO, 'requirements-client.txt'), '.'),
    ],
    hiddenimports=[
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._darwin',
        'pynput._util',
        'pynput._util.darwin',
        'AppKit',
        'Cocoa',
        'Quartz',
        'CoreFoundation',
        'ApplicationServices',
        'objc',
        'PyObjCTools',
        'PyObjCTools.AppHelper',
    ],
    noarchive=False,
)

b = Analysis(
    [os.path.join(REPO, 'stt-indicator.py')],
    pathex=[REPO],
    hiddenimports=[
        'sounddevice',
        'numpy',
        'AppKit',
        'Quartz',
        'CoreFoundation',
        'objc',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)
pyz_indicator = PYZ(b.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RenSTT',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    target_arch='arm64',
)

exe_indicator = EXE(
    pyz_indicator,
    b.scripts,
    [],
    exclude_binaries=True,
    name='stt-indicator',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    target_arch='arm64',
)

coll = COLLECT(
    exe,
    exe_indicator,
    a.binaries,
    b.binaries,
    a.datas,
    b.datas,
    strip=False,
    upx=False,
    name='RenSTT',
)

app = BUNDLE(
    coll,
    name='RenSTT.app',
    icon=None,
    bundle_identifier='com.ren-stt.client',
    info_plist={
        'CFBundleDisplayName': 'Ren STT',
        'CFBundleShortVersionString': '1.0.0',
        'LSUIElement': True,
        'LSMinimumSystemVersion': '12.0',
        'NSMicrophoneUsageDescription': 'Ren STT needs microphone access to record speech for transcription.',
        'NSAppleEventsUsageDescription': 'Ren STT needs accessibility to detect hotkeys and paste transcribed text.',
    },
)
