"""
OS 키체인 래퍼 (Windows Credential Manager / macOS Keychain / Linux Secret Service).

keyring 라이브러리가 없거나 키체인 데몬이 없는 환경에서는
graceful fallback — 경고 로그만 남기고 빈 문자열 반환.
"""
from app.shared.logger import get_logger

_SERVICE = "leagueguider"
_logger = get_logger()
_keyring_available: bool | None = None  # None = 미확인


def _check() -> bool:
    global _keyring_available
    if _keyring_available is None:
        try:
            import keyring  # noqa: F401
            _keyring_available = True
        except ImportError:
            _keyring_available = False
            _logger.warning("keyring 패키지 없음 — 민감 정보 암호화 비활성 (pip install keyring)")
    return _keyring_available


def save_secret(key: str, value: str) -> bool:
    """OS 키체인에 저장. 성공 True, 실패 False."""
    if not _check():
        return False
    try:
        import keyring
        keyring.set_password(_SERVICE, key, value or "")
        return True
    except Exception as e:
        _logger.warning(f"키체인 저장 실패 ({key}): {e}")
        return False


def load_secret(key: str) -> str:
    """OS 키체인에서 조회. 없거나 실패 시 빈 문자열."""
    if not _check():
        return ""
    try:
        import keyring
        return keyring.get_password(_SERVICE, key) or ""
    except Exception as e:
        _logger.warning(f"키체인 조회 실패 ({key}): {e}")
        return ""


def delete_secret(key: str) -> None:
    """OS 키체인 항목 삭제. 없어도 무시."""
    if not _check():
        return
    try:
        import keyring
        from keyring.errors import PasswordDeleteError
        keyring.delete_password(_SERVICE, key)
    except Exception:
        pass
