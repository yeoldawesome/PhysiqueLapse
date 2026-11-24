"""PhysiqueLapse as a desktop app (Windows and Mac).

Starts the same local server the browser version uses (on a free port, reachable only from
this computer) and shows the app in its own window (pywebview: Microsoft Edge WebView2 on
Windows 10 and 11, the system WebKit on a Mac). Closing the window stops everything.

First launch: the model files are downloaded in the background (progress in the job panel), and
an empty library opens on the "Start here" guide.

Run from a source checkout:  .venv\\Scripts\\python -m app.desktop   (in backend/)
The builds in packaging/ freeze this module into PhysiqueLapse.exe / PhysiqueLapse.app.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
import urllib.request

APP_NAME = "PhysiqueLapse"

_LOADING = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#0b0d12;color:#cfd3dc;font:15px system-ui,Segoe UI,sans-serif}
.w{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px}
.logo{width:44px;height:44px;border-radius:10px;background:linear-gradient(135deg,#6d8bff,#a78bfa)}
.s{width:22px;height:22px;border:3px solid #2a2f3a;border-top-color:#6d8bff;border-radius:50%;animation:r 0.9s linear infinite}
@keyframes r{to{transform:rotate(360deg)}} .m{color:#8b93a3;font-size:13px}
</style></head><body><div class="w"><div class="logo"></div><div><b>PhysiqueLapse</b></div><div class="s"></div>
<div class="m" id="m">Starting…</div></div></body></html>"""

log = logging.getLogger("physiquelapse.desktop")


def _setup_logging(data_dir) -> None:
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(logs / "physiquelapse.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # a windowed .exe has no console: anything printed would otherwise crash on a None stream
    if sys.stdout is None or sys.stderr is None:
        stream = open(logs / "console.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
        _give_child_processes_std_handles(stream)


def _give_child_processes_std_handles(stream) -> None:
    """A windowed .exe has no standard handles, and starting a child process that inherits them
    (ffmpeg for MP4 export) fails with WinError 50. Point stdin at NUL and stdout/stderr at the
    console log, at both the C-runtime and the Win32 level."""
    if os.name != "nt":
        return
    import ctypes
    import msvcrt

    try:
        null_fd = os.open(os.devnull, os.O_RDWR)
        out_fd = stream.fileno()
        for fd, src in ((0, null_fd), (1, out_fd), (2, out_fd)):
            os.dup2(src, fd)
        k32 = ctypes.windll.kernel32
        k32.SetStdHandle.argtypes = (ctypes.c_uint32, ctypes.c_void_p)
        for std, fd in ((-10 & 0xFFFFFFFF, 0), (-11 & 0xFFFFFFFF, 1), (-12 & 0xFFFFFFFF, 2)):
            k32.SetStdHandle(std, msvcrt.get_osfhandle(fd))
    except Exception as e:  # not fatal: only MP4 export depends on it
        log.warning("could not set up standard handles: %s", e)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str, timeout: float = 2.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def _post(url: str):
    req = urllib.request.Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def _running_instance(lock) -> str | None:
    """URL of an instance that is already running for this user, if any."""
    try:
        port = int(lock.read_text().strip())
        url = f"http://127.0.0.1:{port}"
        if _get(f"{url}/api/health", 1.0).get("status") == "ok":
            return url
    except Exception:
        pass
    return None


def main() -> None:
    import multiprocessing

    multiprocessing.freeze_support()
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

    from . import config

    config.ensure_dirs()
    _setup_logging(config.DATA_DIR)
    log.info("starting %s (frozen=%s), data in %s", APP_NAME, config.FROZEN, config.DATA_DIR)

    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True  # export downloads ("save to your computer")
    lock = config.DATA_DIR / "app.port"
    existing = _running_instance(lock) if lock.exists() else None

    server = None
    server_thread = None
    if existing is None:
        import uvicorn

        port = _free_port()
        os.environ["PORT"] = str(port)
        from .main import create_app

        server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_config=None, access_log=False))
        server_thread = threading.Thread(target=server.run, name="server", daemon=True)
        server_thread.start()
        url = f"http://127.0.0.1:{port}"
        lock.write_text(str(port))
    else:
        log.info("already running at %s: opening another window on it", existing)
        url = existing

    window = webview.create_window(APP_NAME, html=_LOADING, width=1440, height=920, min_size=(1000, 640), background_color="#0b0d12")

    def boot() -> None:
        def say(msg: str) -> None:
            try:
                window.evaluate_js(f"document.getElementById('m') && (document.getElementById('m').textContent = {json.dumps(msg)})")
            except Exception:
                pass

        deadline = time.time() + 120
        while True:
            try:
                if _get(f"{url}/api/health").get("status") == "ok":
                    break
            except Exception:
                pass
            if server_thread is not None and not server_thread.is_alive():
                say("The app could not start. See the log in " + str(config.DATA_DIR / "logs"))
                return
            if time.time() > deadline:
                say("Still starting… (the first launch can take a minute)")
                deadline = time.time() + 600
            time.sleep(0.25)
        start = "/"
        try:
            models = _get(f"{url}/api/system/models", 10)
            if models["missing"]:
                say(f"Downloading models ({models['missing_mb']} MB), one time…")
                _post(f"{url}/api/system/models/download")
            if _get(f"{url}/api/dashboard", 30)["counts"]["photos"] == 0:
                start = "/guide"
        except Exception as e:
            log.warning("first-run checks failed: %s", e)
        window.load_url(url + start)

    try:
        gui = "edgechromium" if sys.platform == "win32" else None  # Mac: Cocoa / WebKit
        webview.start(boot, gui=gui, private_mode=False, storage_path=str(config.DATA_DIR / "webview"))
    finally:
        if server is not None:
            log.info("window closed: stopping")
            server.should_exit = True
            if server_thread is not None:
                server_thread.join(timeout=15)
            try:
                if lock.read_text().strip() == str(server.config.port):
                    lock.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    main()
