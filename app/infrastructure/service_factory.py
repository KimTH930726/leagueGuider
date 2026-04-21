"""
Search 전용 싱글톤 팩토리.

역할:
  - SearchService 가 사용하는 Embedder / LLM 을 프로세스 내 1회만 생성
  - 설정 변경 시 invalidate() 로 리셋 → 다음 검색에서 재초기화

분리 원칙 (중요):
  - SyncService  : 대량 배치 embedding → 자체 인스턴스 사용 (sync_service.py)
  - ReportService: 장시간 LLM 호출   → fresh 클라이언트 사용 (report_service.py)
  - 이유: 동일 CPU-bound 인스턴스 공유 시 스레드 경합 → 90초+ 타임아웃 발생

사용:
    from app.infrastructure.service_factory import get_embedder, get_llm, invalidate

    embedder = get_embedder(config)  # 검색용 싱글톤
    llm      = get_llm(config)       # 검색어 확장(optional)용

    invalidate()                     # 설정 변경 후 호출
"""
import threading
from typing import Optional
from app.shared.logger import get_logger

logger = get_logger()

_embedder = None   # Search 전용
_llm = None        # Search 쿼리 확장 전용 (optional)
_lock = threading.Lock()


def get_embedder(config) -> Optional[object]:
    """Search용 임베딩 모델 싱글톤. 최초 1회만 로드."""
    global _embedder
    if _embedder is not None:
        return _embedder
    with _lock:
        if _embedder is not None:
            return _embedder
        _embedder = _create_embedder(config)
    return _embedder


def get_llm(config) -> Optional[object]:
    """Search 쿼리 확장용 LLM 싱글톤 (use_llm_rewrite=True 일 때만 사용)."""
    global _llm
    if _llm is not None:
        return _llm
    with _lock:
        if _llm is not None:
            return _llm
        _llm = _create_llm(config)
    return _llm


def invalidate() -> None:
    """
    설정 변경 시 호출. 다음 get_embedder/get_llm 에서 재생성.
    SearchService 모듈 싱글톤도 함께 리셋.
    """
    global _embedder, _llm
    with _lock:
        _embedder = None
        _llm = None
    try:
        from app.ui.search import reset_search_service
        reset_search_service()
    except Exception:
        pass
    logger.info("[ServiceFactory] Search 캐시 무효화 — 다음 검색에서 재초기화")


def _create_embedder(config) -> Optional[object]:
    from pathlib import Path
    from app.infrastructure.embedding.openai_provider import get_embedding_provider

    is_local = config.embedding_provider == "local"
    if not is_local and not config.llm_api_key:
        logger.info("[ServiceFactory] 임베딩 미설정 — 키워드 검색만 사용 가능")
        return None

    local_dir = config.local_model_dir or str(Path(config.db_path).parent / "models")
    model_name = config.local_model_name if is_local else config.embedding_model
    try:
        embedder = get_embedding_provider(
            config.embedding_provider, model_name, config.llm_api_key or "", local_dir
        )
        logger.info(f"[ServiceFactory] Search Embedder 초기화 완료 ({config.embedding_provider})")
        return embedder
    except Exception as e:
        logger.warning(f"[ServiceFactory] Search Embedder 초기화 실패: {e}")
        return None


def _create_llm(config) -> Optional[object]:
    if not config.is_llm_configured:
        return None
    try:
        from app.infrastructure.llm.factory import create_llm_provider
        llm = create_llm_provider(config)
        logger.info(f"[ServiceFactory] Search LLM 초기화 완료 ({config.llm_provider})")
        return llm
    except Exception as e:
        logger.warning(f"[ServiceFactory] Search LLM 초기화 실패: {e}")
        return None
