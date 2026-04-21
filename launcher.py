"""
PyInstaller EXE 엔트리포인트.
- 8501 포트부터 빈 포트를 자동 탐색 (겹침 방지)
- 앱 시작 후 브라우저 자동 열기
"""
import sys
import os
import socket
import threading
import time
import webbrowser
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
    # PyInstaller --onedir: data files land in _MEIPASS (_internal/)
    _DATA_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).parent
    _DATA_DIR = BASE_DIR

os.chdir(BASE_DIR)
sys.path.insert(0, str(BASE_DIR))


def _find_free_port(start: int = 8501, max_tries: int = 20) -> int:
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))  # 0.0.0.0 바인드 — 이미 사용 중인 포트 정확히 감지
                return port
            except OSError:
                continue
    return start  # fallback — Streamlit이 자체 처리하도록


def _open_browser(port: int, delay: float = 3.0) -> None:
    """Streamlit 기동 후 브라우저를 자동으로 엽니다."""
    time.sleep(delay)
    webbrowser.open(f"http://localhost:{port}")


if __name__ == "__main__":
    port = _find_free_port(8501)

    threading.Thread(target=_open_browser, args=(port,), daemon=True).start()

    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit",
        "run",
        str(_DATA_DIR / "main.py"),
        f"--server.port={port}",
        "--server.headless=true",
        "--server.fileWatcherType=none",
        "--browser.gatherUsageStats=false",
        "--browser.serverAddress=localhost",
        "--global.developmentMode=false",
    ]
    sys.exit(stcli.main())
