# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for Webcam RTSP Streamer (Single-file EXE)
#
# Usage:
#   1. Install PyInstaller: pip install pyinstaller
#   2. Ensure ffmpeg.exe, mediamtx.exe, mediamtx.yml are in src/ folder
#   3. Run: pyinstaller build.spec
#   4. Find output: dist/WebcamRTSP.exe
#
# Note: On first run, the exe extracts ffmpeg.exe and mediamtx.exe to a temp folder.
#       This causes a slight delay on startup but makes distribution simpler.

import os

block_cipher = None

# Path to source directory
src_path = os.path.join(os.path.dirname(SPEC), 'src')

# Binary files to include (ffmpeg, mediamtx)
binaries = []
datas = []

# Add ffmpeg.exe if present
ffmpeg_path = os.path.join(src_path, 'ffmpeg.exe')
if os.path.exists(ffmpeg_path):
    binaries.append((ffmpeg_path, '.'))

# Add mediamtx.exe if present
mediamtx_path = os.path.join(src_path, 'mediamtx.exe')
if os.path.exists(mediamtx_path):
    binaries.append((mediamtx_path, '.'))

# Add mediamtx.yml config if present
mediamtx_yml_path = os.path.join(src_path, 'mediamtx.yml')
if os.path.exists(mediamtx_yml_path):
    datas.append((mediamtx_yml_path, '.'))

# Add icon
icon_path = os.path.join(src_path, 'logo.ico')
if os.path.exists(icon_path):
    datas.append((icon_path, '.'))

a = Analysis(
    [os.path.join(src_path, 'rtsp_streamer_gui.py')],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='WebcamRTSP',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # No console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path if os.path.exists(icon_path) else None,
)
