"""
run.bat 개발 실행용 헬퍼.
빈 포트를 자동 탐색해 Streamlit을 시작하고 브라우저를 자동으로 엽니다.
subprocess 없이 stcli.main() 직접 호출 — venv 외부 실행 시에도 안전.
"""
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))


def find_free_port(start: int = 8502, max_tries: int = 20) -> int:
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))  # 0.0.0.0 바인드 — 이미 사용 중인 포트 정확히 감지
                return port
            except OSError:
                continue
    return start


def open_browser(port: int, delay: float = 4.0) -> None:
    time.sleep(delay)
    webbrowser.open(f"http://localhost:{port}")


if __name__ == "__main__":
    port = find_free_port()
    print(f"[AI리그] 포트 {port} 사용", flush=True)

    threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit", "run", str(BASE_DIR / "main.py"),
        f"--server.port={port}",
        "--server.headless=true",
        "--server.fileWatcherType=none",
        "--browser.gatherUsageStats=false",
    ]
    sys.exit(stcli.main())
