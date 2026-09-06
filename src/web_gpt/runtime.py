from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import time
from urllib.request import build_opener, ProxyHandler
from urllib.parse import urlsplit

CDP_PORT = 19231
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def data_dir() -> Path:
    base = Path.home() / "Library/Application Support" if platform.system() == "Darwin" else Path.home() / ".local/share"
    path = Path(os.environ.get("WEB_GPT_DATA_DIR", base / "codex-web-gpt"))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def chatgpt_path(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "chatgpt.com":
        return None
    return parsed.path.rstrip("/")


def project_url(url: str) -> str:
    path = chatgpt_path(url)
    if path is None or not re.fullmatch(r"/g/g-p-[a-zA-Z0-9-]+/project", path):
        raise ValueError("请提供 ChatGPT 项目首页的完整 https://chatgpt.com/g/g-p-…/project 地址。")
    return "https://chatgpt.com" + path


def conversation_key(url: str) -> str | None:
    path = chatgpt_path(url)
    match = re.fullmatch(r"(?:/g/[a-zA-Z0-9-]+)?/c/([a-zA-Z0-9-]+)", path or "")
    return match.group(1) if match else None


def default_project() -> str | None:
    path = data_dir() / "settings.json"
    return json.loads(path.read_text()).get("default_project_url") if path.exists() else None


def set_default_project(url: str | None) -> dict:
    value = project_url(url) if url else None
    destination = data_dir() / "settings.json"
    temporary = destination.with_suffix(".tmp")
    with temporary.open("w") as handle:
        os.chmod(temporary, 0o600)
        json.dump({"default_project_url": value}, handle)
    temporary.replace(destination)
    return {"status": "configured", "default_project_url": value}


def debugger_url() -> str | None:
    try:
        # macOS system proxies can intercept even loopback requests. This local
        # health probe must reach Chrome directly; website traffic is unchanged.
        with build_opener(ProxyHandler({})).open(f"{CDP_URL}/json/version", timeout=2) as response:
            return json.load(response).get("webSocketDebuggerUrl")
    except (OSError, ValueError):
        return None


def browser_running() -> bool:
    return debugger_url() is not None


def launch_browser() -> dict:
    if browser_running():
        return {"status": "already_running", "endpoint": CDP_URL}
    if not CHROME.is_file():
        return {"status": "chrome_missing", "message": f"Chrome not found at {CHROME}"}
    profile = data_dir() / "chrome-profile"
    profile.mkdir(exist_ok=True, mode=0o700)
    # This is a separate profile; never attach debugging to the user's daily browser.
    process = subprocess.Popen(
        [str(CHROME), f"--remote-debugging-port={CDP_PORT}",
         "--remote-debugging-address=127.0.0.1", f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check", default_project() or "https://chatgpt.com/"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if browser_running():
            return {"status": "launched", "message": "已使用原专用 Chrome 配置启动，继续复用已保存的登录态。"}
        if process.poll() is not None:
            break
        time.sleep(0.3)
    return {"status": "browser_start_failed", "message": "启动后未连接到专用 Chrome。请检查其窗口或本机端口，不能视为启动成功。"}


class BrowserBusy(Exception):
    pass


class BrowserLock:
    """Serialize sends across CLI invocations and multiple Codex MCP processes."""

    def __enter__(self):
        self.file = (data_dir() / "browser.lock").open("a")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.file.close()
            raise BrowserBusy from error
        return self

    def __exit__(self, *_):
        self.file.close()


def save_session(session: dict) -> None:
    folder = data_dir() / "sessions"
    folder.mkdir(exist_ok=True, mode=0o700)
    destination = folder / f"{session['session_id']}.json"
    temporary = destination.with_suffix(".tmp")
    with temporary.open("w") as handle:
        os.chmod(temporary, 0o600)
        json.dump(session, handle)
    temporary.replace(destination)


def load_session(session_id: str) -> dict | None:
    from uuid import UUID
    try:
        if str(UUID(session_id)) != session_id:
            return None
    except ValueError:
        return None
    path = data_dir() / "sessions" / f"{session_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())
