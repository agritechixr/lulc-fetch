"""Desktop launcher (the Mac app's entry point).

Starts the local server, opens LULC Fetch in the default browser and shows a small control window
(open again · data folder · quit). Everything runs on this computer; the internet is only used by features
that need it (finding / downloading imagery, basemaps, address search).

    lulc-fetch-app               normal start
    lulc-fetch-app --headless    server only (no window, no browser), e.g. for testing
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

DEFAULT_PORT = 8765


def _alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/project", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def _free_port(preferred: int) -> int:
    for port in [preferred] + list(range(preferred + 1, preferred + 50)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _setup_logging(app_dir: Path):
    logs = app_dir / "logs"
    logs.mkdir(exist_ok=True)
    log_file = logs / "app.log"
    if log_file.exists() and log_file.stat().st_size > 5e6:
        log_file.replace(logs / "app.old.log")
    handlers = [logging.FileHandler(log_file, encoding="utf-8")]
    if getattr(sys, "frozen", False) or sys.stdout is None:   # a windowed app has no terminal: print() goes to the log too
        f = open(log_file, "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = f
    else:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=handlers)
    return log_file


def _reveal(folder: Path):
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    elif sys.platform.startswith("win"):
        os.startfile(str(folder))   # noqa: S606 (Explorer)
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def _control_window(url: str, app_dir: Path, stop):
    """A small native window: keeps the app responsive in the Dock and quits the server cleanly."""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("LULC Fetch")
    root.resizable(False, False)
    frm = ttk.Frame(root, padding=18)
    frm.grid()
    ttk.Label(frm, text="LULC Fetch is running", font=("Helvetica", 15, "bold")).grid(column=0, row=0, columnspan=3, sticky="w")
    ttk.Label(frm, text=f"Open in your browser: {url}", foreground="#555").grid(column=0, row=1, columnspan=3, sticky="w", pady=(4, 2))
    ttk.Label(frm, text=f"Data folder: {app_dir}", foreground="#555").grid(column=0, row=2, columnspan=3, sticky="w", pady=(0, 12))
    ttk.Button(frm, text="Open LULC Fetch", command=lambda: webbrowser.open(url)).grid(column=0, row=3, padx=(0, 6))
    ttk.Button(frm, text="Data folder", command=lambda: _reveal(app_dir)).grid(column=1, row=3, padx=6)

    def quit_app():
        root.destroy()
        stop()

    ttk.Button(frm, text="Quit", command=quit_app).grid(column=2, row=3, padx=(6, 0))
    ttk.Label(frm, text="Closing this window stops LULC Fetch.", foreground="#888").grid(column=0, row=4, columnspan=3, sticky="w", pady=(12, 0))
    ico = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "packaging" / "LULC Fetch.ico"
    if sys.platform.startswith("win") and ico.exists():
        root.iconbitmap(str(ico))
    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.createcommand("tk::mac::Quit", quit_app)            # Cmd+Q / Dock ▸ Quit
    root.createcommand("tk::mac::ReopenApplication", lambda: webbrowser.open(url))   # Dock icon clicked
    root.mainloop()


def _use_addons():
    """Make the deep-learning add-on (installed with pip --target next to the data folder) importable."""
    if not getattr(sys, "frozen", False):
        return
    from webapp import workspace as ws
    d = ws.APP_DIR / "addons" / f"py{sys.version_info.major}{sys.version_info.minor}"
    if d.is_dir() and str(d) not in sys.path:
        sys.path.append(str(d))


def main():
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--lulc-pyrunner":   # the Python editor's separate process
        from lulc_fetch import pyrunner
        pyrunner.cli(sys.argv[2:6])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--lulc-pip":   # installs the deep-learning add-on (frozen app)
        import runpy
        sys.argv = ["pip"] + sys.argv[2:]
        runpy.run_module("pip", run_name="__main__")
        return
    _use_addons()
    headless = "--headless" in sys.argv

    from webapp import workspace as ws
    app_dir = ws.APP_DIR
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "data").mkdir(exist_ok=True)
    os.chdir(app_dir)
    _setup_logging(app_dir)

    state = app_dir / ".app_state.json"
    try:   # already running? just open it
        port = json.loads(state.read_text())["port"]
        if _alive(port):
            webbrowser.open(f"http://127.0.0.1:{port}")
            return
    except (OSError, ValueError, KeyError):
        pass

    import uvicorn

    from webapp.server import app
    port = _free_port(int(os.environ.get("LULC_PORT", DEFAULT_PORT)))
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None))
    state.write_text(json.dumps({"port": port, "pid": os.getpid()}))
    logging.info("LULC Fetch starting at %s (data folder %s)", url, app_dir)

    def stop():
        server.should_exit = True

    try:
        if headless:
            server.run()
            return
        th = threading.Thread(target=server.run, daemon=True)
        th.start()
        for _ in range(150):
            if _alive(port):
                break
            time.sleep(0.2)
        webbrowser.open(url)
        _control_window(url, app_dir, stop)
        th.join(timeout=5)
    finally:
        state.unlink(missing_ok=True)
        logging.info("LULC Fetch stopped")


if __name__ == "__main__":
    main()
