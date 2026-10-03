# PyInstaller recipe for the LULC Fetch desktop app (macOS .app and Windows .exe).
# Build:  macOS  packaging/build_mac.sh      Windows  packaging\build_windows.ps1
# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
import re as _re
# macOS wants a plain number: "v0.0.1-beta" → "0.0.1" (the full tag is kept in the info string)
APP_VERSION = (_re.match(r"\d+(\.\d+)*", os.environ.get("LULC_VERSION", "0.0.2").lstrip("v")) or _re.match(r".*", "0.0.2")).group(0)
WIN, MAC = sys.platform.startswith("win"), sys.platform == "darwin"
datas = [(os.path.join(ROOT, "webapp", "static"), os.path.join("webapp", "static")),
         (os.path.join(ROOT, "lulc_fetch", "agri", "data"), os.path.join("lulc_fetch", "agri", "data")),   # crop labels + knowledge base
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
# the whole standard library: add-ons installed later (PyTorch) import modules LULC Fetch itself never uses
import importlib.util
_SKIP = {"test", "idlelib", "turtledemo", "lib2to3", "pydoc_data", "ensurepip", "antigravity", "this", "__phello__"}
for _m in sorted(sys.stdlib_module_names):
    if _m in _SKIP or _m.startswith("_test"):
        continue
    try:
        _spec = importlib.util.find_spec(_m)
    except (ImportError, ValueError):
        _spec = None
    if _spec is None:
        continue
    hiddenimports.append(_m)
    if _spec.submodule_search_locations:
        hiddenimports += [x for x in collect_submodules(_m, on_error="ignore") if ".test" not in x and not x.startswith(("tkinter.test", "unittest.test"))]
# pip is bundled so the app can install the optional deep-learning add-on (PyTorch) into its data folder
d, b, h = collect_all("pip")
datas += d
binaries += b
hiddenimports += h
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
    # PyTorch and ultralytics (YOLO & SAM, AGPL-3.0) are optional add-ons installed by the app itself, never bundled
    excludes=["matplotlib", "IPython", "jupyter", "notebook", "pytest", "PyQt5", "PySide6", "playwright",
              "torch", "torchvision", "segmentation_models_pytorch", "timm", "huggingface_hub", "safetensors", "PIL",
              "ultralytics", "cv2", "polars",
              # PyTorch's own dependencies: never half-bundled (they would hide the add-on's complete copies)
              "tqdm", "jinja2", "markupsafe", "sympy", "mpmath", "networkx", "fsspec", "filelock", "hf_xet", "httpx2", "httpcore2", "yaml"],
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
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "CFBundleGetInfoString": f"LULC Fetch {os.environ.get('LULC_VERSION', APP_VERSION).lstrip('v')}",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": os.environ.get("LULC_MIN_MACOS") or "14.0",
            "NSHumanReadableCopyright": "Apache License 2.0",
        },
    )
