# PyInstaller recipe for the LULC Fetch desktop app (macOS .app and Windows .exe).
# Build:  macOS  packaging/build_mac.sh      Windows  packaging\build_windows.ps1
# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
WIN, MAC = sys.platform.startswith("win"), sys.platform == "darwin"
datas = [(os.path.join(ROOT, "webapp", "static"), os.path.join("webapp", "static")),
         (os.path.join(SPECPATH, "LULC Fetch.ico"), "packaging")]
binaries = []
hiddenimports = (collect_submodules("webapp") + collect_submodules("lulc_fetch") + collect_submodules("uvicorn")
                 + collect_submodules("keyring.backends") + ["multipart", "python_multipart", "openpyxl", "shapefile"])

# packages with native libraries / data files / lazy imports that need everything collected
for pkg in ("rasterio", "xgboost", "lightgbm", "sklearn", "pystac_client", "planetary_computer", "pystac", "shapely", "pyarrow"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += collect_data_files("certifi")
if WIN:   # Windows wheels keep their DLLs in "<package>.libs" folders (delvewheel)
    try:
        from PyInstaller.utils.hooks import collect_delvewheel_libs_directory
        for pkg in ("rasterio", "shapely", "pyarrow", "numpy", "scipy", "sklearn", "pandas"):
            try:
                datas, binaries = collect_delvewheel_libs_directory(pkg, datas=datas, binaries=binaries)
            except Exception:
                pass
    except ImportError:
        pass

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["matplotlib", "IPython", "jupyter", "notebook", "pytest", "PyQt5", "PySide6", "playwright"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = os.path.join(SPECPATH, "LULC Fetch.icns" if MAC else "LULC Fetch.ico")
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LULC Fetch", console=False, icon=icon,
          argv_emulation=False, version=None)
coll = COLLECT(exe, a.binaries, a.datas, name="LULC Fetch")
if MAC:
    app = BUNDLE(
        coll,
        name="LULC Fetch.app",
        icon=icon,
        bundle_identifier="org.lulcfetch.app",
        info_plist={
            "CFBundleName": "LULC Fetch",
            "CFBundleDisplayName": "LULC Fetch",
            "CFBundleShortVersionString": os.environ.get("LULC_VERSION", "0.1.0").lstrip("v"),
            "CFBundleVersion": os.environ.get("LULC_VERSION", "0.1.0").lstrip("v"),
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": os.environ.get("LULC_MIN_MACOS") or "14.0",
            "NSHumanReadableCopyright": "Apache License 2.0",
        },
    )
