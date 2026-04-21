from pathlib import Path
import chromadb
from chromadb.config import Settings
from app.shared.exceptions import VectorStoreError
from app.shared.logger import get_logger

logger = get_logger()

COLLECTION_NAME = "league_documents"


class ChromaStore:
    def __init__(self, chroma_path: str):
        Path(chroma_path).mkdir(parents=True, exist_ok=True)
        try:
            self._client = chromadb.PersistentClient(
                path=chroma_path,
                settings=Settings(anonymized_telemetry=False),
            )
            self._col = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(f"ChromaDB 로드 완료: {chroma_path} / {self._col.count()}개 벡터")
        except Exception as e:
            raise VectorStoreError(f"ChromaDB 초기화 실패: {e}") from e

    def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        if not ids:
            return
        try:
            self._col.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )
        except Exception as e:
            raise VectorStoreError(f"Chroma upsert 실패: {e}") from e

    def search_similar(
        self, embedding: list[float], n_results: int = 10
    ) -> list[dict]:
        """[{document_id, chunk_index, text, score}, ...]"""
        try:
            count = self._col.count()
            if count == 0:
                return []
            n = min(n_results, count)
            result = self._col.query(
                query_embeddings=[embedding],
                n_results=n,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            raise VectorStoreError(f"Chroma 검색 실패: {e}") from e

        hits = []
        for doc, meta, dist in zip(
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
        ):
            hits.append(
                {
                    "document_id": meta.get("document_id"),
                    "chunk_index": meta.get("chunk_index"),
                    "text": doc,
                    "score": 1 - dist,  # cosine distance → similarity
                }
            )
        return hits

    def delete_by_document_id(self, document_id: int) -> None:
        try:
            existing = self._col.get(where={"document_id": document_id})
            if existing["ids"]:
                self._col.delete(ids=existing["ids"])
        except Exception as e:
            logger.warning(f"Chroma 삭제 오류 (doc_id={document_id}): {e}")

    def delete_all(self) -> None:
        try:
            self._client.delete_collection(COLLECTION_NAME)
            self._col = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info("Chroma 컬렉션 전체 초기화 완료")
        except Exception as e:
            raise VectorStoreError(f"Chroma 전체 삭제 실패: {e}") from e

    def count(self) -> int:
        return self._col.count()
