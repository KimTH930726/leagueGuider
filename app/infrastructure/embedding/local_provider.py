"""
로컬 sentence-transformers 임베딩 Provider.

- 인터넷/API Key 불필요
- 모델은 data/models/ 에 캐시 (최초 1회 다운로드)
- EXE 배포 시 data/models/ 를 함께 배포하면 오프라인 동작

권장 모델:
  paraphrase-multilingual-mpnet-base-v2   (768d, 다국어, ~420MB)
  jhgan/ko-sroberta-multitask             (768d, 한국어 특화, ~480MB)
  intfloat/multilingual-e5-small          (384d, 경량 다국어, ~120MB)

Thread-safety:
  - _init_lock: 동시 초기화 방지 (double-check locking)
  - _encode_lock: 동일 인스턴스 내 동시 encode() 직렬화
  - 인스턴스가 분리돼 있으면 락 경합 없음 (Sync/Search 각자 인스턴스 사용)
"""
import threading
from pathlib import Path
from app.infrastructure.embedding.base import EmbeddingProviderBase
from app.shared.exceptions import EmbeddingError
from app.shared.logger import get_logger

logger = get_logger()

DEFAULT_MODEL = "paraphrase-multilingual-mpnet-base-v2"


class LocalEmbeddingProvider(EmbeddingProviderBase):
    def __init__(self, model_name: str = DEFAULT_MODEL, model_dir: str = ""):
        self._model_name = model_name
        self._model_dir = model_dir or ""
        self._model = None
        self._init_lock = threading.Lock()   # 모델 초기화 직렬화
        self._encode_lock = threading.Lock() # encode() 직렬화 (동일 인스턴스 내)

    def _load(self) -> None:
        """thread-safe lazy 초기화 (double-check locking)."""
        if self._model is not None:
            return
        with self._init_lock:
            if self._model is not None:  # 재진입 체크
                return
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise EmbeddingError(
                    "sentence-transformers 패키지가 없습니다.\n"
                    "pip install sentence-transformers"
                ) from e

            logger.info(f"로컬 임베딩 모델 로딩: {self._model_name}")
            kwargs: dict = {}
            if self._model_dir:
                Path(self._model_dir).mkdir(parents=True, exist_ok=True)
                kwargs["cache_folder"] = self._model_dir

            try:
                self._model = SentenceTransformer(self._model_name, **kwargs)
                dim = self._model.get_embedding_dimension()
                logger.info(f"모델 로드 완료: {self._model_name} (dim={dim})")
            except Exception as e:
                raise EmbeddingError(f"모델 로드 실패 ({self._model_name}): {e}") from e

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self._load()
        with self._encode_lock:
            try:
                vecs = self._model.encode(
                    texts,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                    batch_size=32,
                )
                return [v.tolist() for v in vecs]
            except Exception as e:
                raise EmbeddingError(f"로컬 임베딩 실패: {e}") from e

    @property
    def dimension(self) -> int:
        self._load()
        return self._model.get_embedding_dimension()


def download_model(model_name: str, model_dir: str) -> None:
    """모델 사전 다운로드 (setup.bat에서 호출). 이미 있으면 스킵."""
    try:
        from sentence_transformers import SentenceTransformer
        logger.info(f"모델 다운로드: {model_name} → {model_dir}")
        Path(model_dir).mkdir(parents=True, exist_ok=True)
        SentenceTransformer(model_name, cache_folder=model_dir)
        logger.info("모델 다운로드 완료")
    except Exception as e:
        raise EmbeddingError(f"모델 다운로드 실패: {e}") from e
