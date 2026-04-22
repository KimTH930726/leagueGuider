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
    # 번들 모델 사용 — HuggingFace 네트워크 체크 전면 차단
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
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


def _wait_and_open_browser(port: int, timeout: float = 30.0) -> None:
    """Streamlit이 실제로 응답할 때까지 기다린 후 브라우저를 엽니다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                break
        except OSError:
            time.sleep(0.5)
    webbrowser.open(f"http://localhost:{port}")


_PORT_FILE = BASE_DIR / ".app_port"


def _read_saved_port() -> int | None:
    """이전 실행 시 저장한 포트 읽기."""
    try:
        return int(_PORT_FILE.read_text().strip())
    except Exception:
        return None


def _save_port(port: int) -> None:
    try:
        _PORT_FILE.write_text(str(port))
    except Exception:
        pass


def _is_our_app_running(port: int) -> bool:
    """저장된 포트에 실제로 우리 앱이 응답 중인지 확인."""
    try:
        with socket.create_connection(("localhost", port), timeout=1):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    # 이미 실행 중인 우리 앱이 있으면 브라우저만 열고 종료
    saved_port = _read_saved_port()
    if saved_port and _is_our_app_running(saved_port):
        webbrowser.open(f"http://localhost:{saved_port}")
        sys.exit(0)

    # 포트 파일 초기화 (이전 포트가 죽어있는 경우)
    _PORT_FILE.unlink(missing_ok=True)

    port = _find_free_port(8501)
    _save_port(port)

    threading.Thread(target=_wait_and_open_browser, args=(port,), daemon=True).start()

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
