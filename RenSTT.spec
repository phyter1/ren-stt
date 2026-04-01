# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for RenSTT.app

import os

REPO = SPECPATH

a = Analysis(
    [os.path.join(REPO, 'stt-cli.py')],
    pathex=[REPO],
    datas=[
        (os.path.join(REPO, 'config.py'), '.'),
        (os.path.join(REPO, 'stt-indicator.py'), '.'),
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
        'sounddevice',
        'numpy',
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

pyz = PYZ(a.pure)

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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
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
