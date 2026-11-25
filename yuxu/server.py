from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen
import webbrowser

from . import __version__
from .app import YuxuApplication
from . import files, store

DEFAULT_PORT = 8765


def service_port() -> int:
    configured = os.environ.get("YUXU_PORT")
    if configured:
        try:
            port = int(configured)
        except ValueError:
            raise ValueError("YUXU_PORT must be an integer between 1 and 65535") from None
        if not 1 <= port <= 65535:
            raise ValueError("YUXU_PORT must be an integer between 1 and 65535")
        return port
    port_file = store.runtime_dir() / "service.port"
    if port_file.is_file():
        try:
            port = int(port_file.read_text(encoding="utf-8").strip())
            if 1 <= port <= 65535:
                return port
        except (OSError, ValueError):
            pass
    return DEFAULT_PORT


def _state_path(name: str) -> Path:
    return store.runtime_dir() / name


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _control_token() -> str:
    path = _state_path("control.token")
    if path.is_file():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    _write_text(path, token)
    return token


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "Yuxu"
    sys_version = ""

    def do_GET(self) -> None:
        self.server.application.handle(self)

    def do_POST(self) -> None:
        self.server.application.handle(self)

    def do_HEAD(self) -> None:
        self.server.application.handle(self)

    def log_message(self, format: str, *args) -> None:
        return


def serve(owner: str = "local-cli", on_started: Callable[[int], None] | None = None) -> None:
    runtime = store.runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)
    http_server = LocalHTTPServer(("127.0.0.1", service_port()), RequestHandler)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    application = YuxuApplication(_control_token(), started_at, owner)
    application.http_server = http_server
    http_server.application = application
    scheduler_stop = threading.Event()
    scheduler_thread = threading.Thread(
        target=files.backup_scheduler_loop,
        args=(scheduler_stop,),
        name="yuxu-backup-scheduler",
        daemon=True,
    )
    pid = str(os.getpid())
    _write_text(_state_path("service.pid"), pid)
    _write_text(_state_path("service.port"), str(http_server.server_address[1]))
    try:
        if on_started:
            on_started(int(http_server.server_address[1]))
        scheduler_thread.start()
        http_server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        scheduler_stop.set()
        http_server.server_close()
        for name in ("service.pid", "service.port", "control.token"):
            path = _state_path(name)
            try:
                if name != "service.pid" or path.read_text(encoding="utf-8").strip() == pid:
                    path.unlink(missing_ok=True)
            except OSError:
                pass


def _request_status(timeout: float = 0.7) -> dict | None:
    url = f"http://127.0.0.1:{service_port()}/api/status"
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("version") != __version__ or data.get("host") != "127.0.0.1":
        return None
    return payload


def status() -> dict:
    payload = _request_status()
    if payload:
        return payload
    return {"status": "ok", "account_id": None, "account_name": None, "data": {"state": "stopped", "owner": None}, "request_id": None, "error": None}


def ensure_service(*, owner: str = "mcp-sdk") -> dict:
    existing = _request_status()
    if existing:
        return existing
    runtime = store.runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)
    project_root = Path(__file__).resolve().parent.parent
    script = project_root / "run_yuxu.py"
    command = [sys.executable, "-X", "utf8", "-B", str(script), "serve", "--background-child", "--owner", owner]
    kwargs = {
        "cwd": str(project_root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "env": os.environ.copy(),
    }
    kwargs["env"].pop("YUXU_TOKEN", None)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    child = subprocess.Popen(command, **kwargs)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if child.poll() is not None:
            break
        running = _request_status()
        if running:
            return running
        time.sleep(0.1)
    running = _request_status()
    if running:
        return running
    raise RuntimeError("本机服务未能启动；请检查端口和运行目录后再重试。")


def stop() -> dict:
    payload = _request_status()
    if payload is None:
        return status()
    token_path = _state_path("control.token")
    if not token_path.is_file():
        raise RuntimeError("找不到本机服务控制令牌，无法安全停止服务。")
    token = token_path.read_text(encoding="utf-8").strip()
    request = Request(
        f"http://127.0.0.1:{service_port()}/api/control/stop",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "X-Yuxu-Control-Token": token,
        },
        method="POST",
    )
    with urlopen(request, timeout=2) as response:
        stopped = json.loads(response.read().decode("utf-8"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _request_status(timeout=0.3) is None:
            return stopped
        time.sleep(0.1)
    raise RuntimeError("停止请求已发送，但本机服务仍在响应。")


def _start_windows_tray(port: int) -> None:
    environment = os.environ.copy()
    environment.pop("YUXU_TOKEN", None)
    environment["YUXU_PORT"] = str(port)
    subprocess.Popen(
        [sys.executable, "-X", "utf8", "-B", "-m", "yuxu.tray", str(port)],
        cwd=Path(__file__).resolve().parent.parent,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def launch_web(*, open_browser: bool = True, background: bool = False) -> None:
    current = ensure_service(owner="web-background") if background else _request_status()
    if current:
        data = current["data"]
        url = f"http://127.0.0.1:{data['port']}/"
        print(f"本机服务：owner={data.get('owner', 'unknown')}，URL={url}")
        if data.get("owner") == "mcp-sdk":
            print("当前服务由 MCP SDK 宿主持有；宿主关闭时服务会被回收，账本数据保留。若需网页独立持有，请等该 MCP 宿主关闭后重新运行此启动器。")
        elif data.get("owner") in ("web-cli", "local-cli"):
            print("服务由本机命令行持有；关闭网页或独立 MCP 客户端不会停止服务。运行 `run_yuxu.py stop` 可显式停止。")
        elif data.get("owner") == "web-background":
            print("余序正在后台运行。运行 `run_yuxu.py stop` 可显式停止。")
        else:
            print("当前服务拥有者未知；关闭前请先检查 `run_yuxu.py status`。账本数据保留，MCP 冷启动可再次读取。")
        if open_browser:
            webbrowser.open(url)
            if background and os.name == "nt":
                try:
                    _start_windows_tray(data["port"])
                except OSError as error:
                    print(f"托盘图标未能启动：{error}", file=sys.stderr)
        return

    def started(port: int) -> None:
        url = f"http://127.0.0.1:{port}/"
        print(f"网页服务由当前启动器终端持有，URL={url}")
        print("关闭浏览器或独立 MCP 客户端不会停止服务；关闭本终端会停止服务。也可在另一终端运行 `run_yuxu.py stop`。")
        if open_browser:
            webbrowser.open(url)

    try:
        serve(owner="web-cli", on_started=started)
    except OSError:
        current = _request_status()
        if current is None:
            raise
        data = current["data"]
        url = f"http://127.0.0.1:{data['port']}/"
        print(f"复用刚启动的本机服务：owner={data.get('owner', 'unknown')}，URL={url}")
        if open_browser:
            webbrowser.open(url)
