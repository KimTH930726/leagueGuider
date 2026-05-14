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
        # bind 체크: 일반 TCP 점유 감지
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
            except OSError:
                continue
        # connect 체크: Docker/WSL2처럼 bind는 되지만 실제 응답 있는 포트 제외
        try:
            with socket.create_connection(("localhost", port), timeout=0.3):
                continue  # 이미 응답 중 — 다른 포트로
        except OSError:
            pass
        return port
    return start  # fallback


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


_STATE_FILE = BASE_DIR / ".app_state"  # "port:pid" 형식


def _read_saved_state() -> tuple[int, int] | tuple[None, None]:
    """이전 실행 시 저장한 포트 + PID 읽기."""
    try:
        parts = _STATE_FILE.read_text().strip().split(":")
        return int(parts[0]), int(parts[1])
    except Exception:
        return None, None


def _save_state(port: int, pid: int) -> None:
    try:
        _STATE_FILE.write_text(f"{port}:{pid}")
    except Exception:
        pass


def _is_pid_alive(pid: int) -> bool:
    """대상 프로세스를 절대 건드리지 않는 PID 생존 체크.
    Windows에서 os.kill(pid, 0)은 내부적으로 OpenProcess(PROCESS_ALL_ACCESS)
    + TerminateProcess(handle, 0)을 호출 — 재사용된 PID라면 무관한 프로세스를
    죽이거나 ACCESS_DENIED로 잔재 상태를 만들 수 있어 사용 금지."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = wintypes.DWORD(0)
        try:
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _is_our_app_running(port: int, pid: int) -> bool:
    """저장된 PID가 살아있고 포트도 응답 중일 때만 우리 앱으로 판단.
    Docker 등 다른 서비스가 같은 포트를 점유해도 PID 불일치로 걸러냄."""
    if not _is_pid_alive(pid):
        return False
    try:
        with socket.create_connection(("localhost", port), timeout=1):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    # 이미 실행 중인 우리 앱이 있으면 브라우저만 열고 종료
    saved_port, saved_pid = _read_saved_state()
    if saved_port and saved_pid and _is_our_app_running(saved_port, saved_pid):
        webbrowser.open(f"http://localhost:{saved_port}")
        sys.exit(0)

    # 상태 파일 초기화 (이전 앱이 죽어있는 경우) — 잔재 락이 있어도 실패 무시
    try:
        _STATE_FILE.unlink(missing_ok=True)
    except OSError:
        pass

    port = _find_free_port(8501)
    _save_state(port, os.getpid())

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
