import hashlib
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from app.shared.config import AppConfig
from app.shared.exceptions import SyncError
from app.shared.logger import get_logger
from app.infrastructure.confluence.client import ConfluenceClient
from app.infrastructure.confluence.parser import HTMLParser
from app.infrastructure.db.connection import db_session
from app.infrastructure.db.document_repository import DocumentRepository
from app.infrastructure.db.chunk_repository import ChunkRepository
from app.infrastructure.db.sync_history_repository import SyncHistoryRepository
from app.infrastructure.vector.chroma_store import ChromaStore
from app.infrastructure.llm.factory import create_llm_provider
from app.infrastructure.llm.extractor import MetadataExtractor, should_extract
from app.shared.text_utils import chunk_text as _chunk_text

logger = get_logger()

ProgressCallback = Callable[[str], None]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SyncService:
    def __init__(
        self,
        confluence: ConfluenceClient,
        doc_repo: DocumentRepository,
        chunk_repo: ChunkRepository,
        sync_repo: SyncHistoryRepository,
        vector_store: ChromaStore,
        embedder,
        root_page_id: str,
        extractor: Optional[MetadataExtractor] = None,
    ):
        self.confluence = confluence
        self.doc_repo = doc_repo
        self.chunk_repo = chunk_repo
        self.sync_repo = sync_repo
        self.vector_store = vector_store
        self.embedder = embedder
        self.root_page_id = root_page_id
        self.extractor = extractor
        self.parser = HTMLParser()

    @classmethod
    def from_config(cls, config: AppConfig) -> "SyncService":
        if not config.is_confluence_configured:
            raise SyncError("Confluence 설정이 완료되지 않았습니다.")

        confluence = ConfluenceClient(
            base_url=config.confluence_base_url,
            auth_token=config.auth_token,
            auth_username=config.auth_username,
            confluence_type=config.confluence_type,
        )

        # Sync는 Search와 동시 실행되므로 임베더/LLM을 별도 인스턴스로 생성.
        # (service_factory 싱글톤 공유 시 동시 encode() → CPU 경합 → search 90초 타임아웃)
        from pathlib import Path as _Path
        from app.infrastructure.embedding.openai_provider import get_embedding_provider
        _is_local = config.embedding_provider == "local"
        _local_dir = config.local_model_dir or str(_Path(config.db_path).parent / "models")
        _model_name = config.local_model_name if _is_local else config.embedding_model
        try:
            embedder = get_embedding_provider(
                config.embedding_provider, _model_name, config.llm_api_key or "", _local_dir
            )
        except Exception as e:
            logger.warning(f"Sync 임베더 초기화 실패 (키워드 전용 모드): {e}")
            embedder = None

        # LLM 메타데이터 추출기: LLM 설정 + extract_metadata 플래그 모두 활성화 시에만
        extractor = None
        if config.is_llm_configured and config.extract_metadata:
            try:
                from app.infrastructure.llm.factory import create_llm_provider
                llm = create_llm_provider(config)
                extractor = MetadataExtractor(llm)
                logger.info("메타데이터 자동 추출 활성화됨")
            except Exception as e:
                logger.warning(f"MetadataExtractor 초기화 실패 (추출 비활성화): {e}")

        return cls(
            confluence=confluence,
            doc_repo=DocumentRepository(config.db_path),
            chunk_repo=ChunkRepository(config.db_path),
            sync_repo=SyncHistoryRepository(config.db_path),
            vector_store=ChromaStore(config.chroma_path),
            embedder=embedder,
            root_page_id=config.root_page_id,
            extractor=extractor,
        )

    def run_full(self, progress: Optional[ProgressCallback] = None) -> dict:
        """최초 1회 전체 동기화"""
        return self._run(sync_type="full", force_all=True, progress=progress)

    def run_incremental(self, progress: Optional[ProgressCallback] = None) -> dict:
        """증분 동기화 — 변경 문서만 처리"""
        return self._run(sync_type="incremental", force_all=False, progress=progress)

    def _run(
        self,
        sync_type: str,
        force_all: bool,
        progress: Optional[ProgressCallback],
    ) -> dict:
        def notify(msg: str):
            logger.info(msg)
            if progress:
                progress(msg)

        sync_id = None
        result = {"new_count": 0, "updated_count": 0, "deleted_count": 0, "message": ""}

        try:
            notify("Confluence 페이지 메타데이터 조회 중...")
            remote_pages = self.confluence.get_descendant_pages_meta(self.root_page_id)
            local_meta = self.doc_repo.get_all_meta()  # {page_id: row_dict}

            remote_map = {p.page_id: p for p in remote_pages}
            remote_ids = set(remote_map.keys())
            local_ids = set(local_meta.keys())

            new_ids = remote_ids - local_ids
            deleted_ids = local_ids - remote_ids

            if force_all:
                to_process = remote_ids
            else:
                changed_ids = {
                    pid
                    for pid in remote_ids & local_ids
                    if remote_map[pid].version != local_meta[pid]["version"]
                    or remote_map[pid].updated_at != local_meta[pid]["updated_at"]
                }
                to_process = new_ids | changed_ids

            total = len(to_process)
            notify(
                f"처리 대상: 신규 {len(new_ids)}건 / "
                f"변경 {len(to_process) - len(new_ids)}건 / "
                f"삭제 {len(deleted_ids)}건"
            )

            # 변경 없으면 이력 저장 스킵
            if not to_process and not deleted_ids:
                notify("변경 없음 — 이력 저장 생략")
                return result

            for idx, page_id in enumerate(to_process, 1):
                page = remote_map[page_id]
                notify(f"[{idx}/{total}] {page.title}")

                content = self.confluence.get_page_content(page_id)
                cleaned = self.parser.to_text(content.raw_body)
                new_hash = _content_hash(cleaned)

                # content_hash 동일 → 실제 변경 없음 → 임베딩/추출 스킵
                if (
                    page_id in local_meta
                    and local_meta[page_id].get("content_hash") == new_hash
                ):
                    continue

                doc_id = self.doc_repo.upsert({
                    "confluence_page_id": page_id,
                    "parent_page_id": page.parent_page_id,
                    "title": page.title,
                    "url": page.url,
                    "author": page.author,
                    "created_at": page.created_at,
                    "updated_at": page.updated_at,
                    "version": page.version,
                    "raw_body": content.raw_body,
                    "cleaned_body": cleaned,
                    "content_hash": new_hash,
                })

                # 청킹 + 임베딩
                self._process_chunks_and_embeddings(doc_id, cleaned)

                # 메타데이터 추출 (LLM 설정 시)
                self._extract_and_save_metadata(
                    doc_id=doc_id,
                    title=page.title,
                    cleaned=cleaned,
                    new_hash=new_hash,
                    local_row=local_meta.get(page_id),
                )

                if page_id in new_ids:
                    result["new_count"] += 1
                else:
                    result["updated_count"] += 1

            # 삭제 처리
            for page_id in deleted_ids:
                doc_id = local_meta[page_id]["id"]
                self.doc_repo.mark_deleted(page_id)
                self.vector_store.delete_by_document_id(doc_id)
                result["deleted_count"] += 1

            # 실제 변경이 있을 때만 이력 저장
            sync_id = self.sync_repo.create(sync_type)
            notify("동기화 완료")
            self.sync_repo.finish(sync_id, "success", result)

        except Exception as e:
            result["message"] = str(e)
            if sync_id:
                self.sync_repo.finish(sync_id, "failed", result)
            raise SyncError(str(e)) from e

        return result

    def _process_chunks_and_embeddings(self, doc_id: int, cleaned_body: str) -> None:
        chunks = _chunk_text(cleaned_body)
        if not chunks:
            return

        self.chunk_repo.delete_by_document_id(doc_id)
        self.vector_store.delete_by_document_id(doc_id)
        self.chunk_repo.replace_chunks(doc_id, chunks)

        if self.embedder is None:
            return  # API Key 없음 — 청크 저장만, 벡터 인덱스 스킵

        texts = [c["chunk_text"] for c in chunks]
        embeddings = self.embedder.embed_texts(texts)
        self.vector_store.upsert(
            ids=[f"{doc_id}_{i}" for i in range(len(chunks))],
            embeddings=embeddings,
            documents=texts,
            metadatas=[{"document_id": doc_id, "chunk_index": i} for i in range(len(chunks))],
        )

    def _extract_and_save_metadata(
        self,
        doc_id: int,
        title: str,
        cleaned: str,
        new_hash: str,
        local_row: Optional[dict],
    ) -> None:
        """메타데이터 추출 후 upsert. 실패해도 sync는 계속."""
        if self.extractor is None:
            # LLM 없음 → 최초 문서에만 fallback 메타 저장
            if self.doc_repo.get_metadata(doc_id) is None:
                fb = {
                    "agent_name": title,
                    "one_line_summary": "",
                    "problem": None,
                    "solution": None,
                    "tech_stack_json": "[]",
                    "effects_json": "[]",
                    "keywords_json": "[]",
                    "stage": None,
                    "category": "기타",
                }
                self.doc_repo.upsert_metadata(doc_id, {"document_id": doc_id, **fb})
            return

        # content_hash 변경 없으면 재추출 불필요
        existing = self.doc_repo.get_metadata(doc_id)
        if not should_extract(doc_id, new_hash, local_row or {}, existing):
            logger.debug(f"[추출 스킵] content_hash 동일: {title!r}")
            return

        try:
            meta = self.extractor.extract(title, cleaned)
            self.doc_repo.upsert_metadata(doc_id, {"document_id": doc_id, **meta})
        except Exception as e:
            logger.warning(f"[추출 후처리 실패] {title!r}: {e}")

    def reextract_metadata(self, progress: Optional[ProgressCallback] = None) -> dict:
        """
        fallback 메타데이터(기술스택·요약 없음)를 가진 문서만 LLM 재추출.
        content_hash 변경 없이도 강제 재추출 — incremental sync가 스킵한 문서 복구용.
        """
        if self.extractor is None:
            raise SyncError("LLM이 설정되지 않았습니다. LLM 설정 후 다시 시도하세요.")

        def notify(msg: str):
            logger.info(msg)
            if progress:
                progress(msg)

        with db_session(self.doc_repo.db_path) as conn:
            rows = conn.execute(
                """SELECT d.id, d.title, d.cleaned_body
                   FROM documents d
                   JOIN document_metadata dm ON d.id = dm.document_id
                   WHERE d.is_deleted = 0
                     AND (
                       dm.tech_stack_json IN ('[]', '', 'null') OR dm.tech_stack_json IS NULL
                       OR dm.problem IS NULL OR dm.problem = ''
                       OR dm.category IS NULL OR dm.category IN ('기타', '추출불가')
                     )"""
            ).fetchall()

        total = len(rows)
        notify(f"재추출 대상: {total}건")
        done = 0
        failed = 0

        for idx, row in enumerate(rows, 1):
            notify(f"[{idx}/{total}] {row['title']}")
            try:
                meta = self.extractor.extract(row["title"], row["cleaned_body"] or "")
                self.doc_repo.upsert_metadata(row["id"], {"document_id": row["id"], **meta})
                done += 1
            except Exception as e:
                logger.warning(f"[재추출 실패] {row['title']!r}: {e}")
                failed += 1

        notify(f"재추출 완료: 성공 {done}건 / 실패 {failed}건")
        return {"total": total, "done": done, "failed": failed}

    def reextract_new_or_changed(self, progress: Optional[ProgressCallback] = None) -> dict:
        """
        신규 또는 수정된 문서만 메타데이터 재추출.
        - 메타데이터가 없는 문서 (신규)
        - Confluence updated_at > meta_extracted_at (수정 후 미추출)
        """
        if self.extractor is None:
            raise SyncError("LLM이 설정되지 않았습니다. LLM 설정 후 다시 시도하세요.")

        def notify(msg: str):
            logger.info(msg)
            if progress:
                progress(msg)

        with db_session(self.doc_repo.db_path) as conn:
            rows = conn.execute(
                """SELECT d.id, d.title, d.cleaned_body
                   FROM documents d
                   LEFT JOIN document_metadata dm ON d.id = dm.document_id
                   WHERE d.is_deleted = 0
                     AND (
                       dm.document_id IS NULL
                       OR dm.meta_extracted_at IS NULL
                       OR d.updated_at > dm.meta_extracted_at
                       OR dm.category = '추출불가'
                     )"""
            ).fetchall()

        total = len(rows)
        notify(f"신규/수정 대상: {total}건")
        done = 0
        failed = 0

        for idx, row in enumerate(rows, 1):
            notify(f"[{idx}/{total}] {row['title']}")
            try:
                meta = self.extractor.extract(row["title"], row["cleaned_body"] or "")
                self.doc_repo.upsert_metadata(row["id"], {"document_id": row["id"], **meta})
                done += 1
            except Exception as e:
                logger.warning(f"[재추출 실패] {row['title']!r}: {e}")
                failed += 1

        notify(f"완료: 성공 {done}건 / 실패 {failed}건")
        return {"total": total, "done": done, "failed": failed}

    def rebuild_index(self, progress: Optional[ProgressCallback] = None) -> dict:
        """전체 재색인 (관리자 전용)"""
        if self.embedder is None:
            raise SyncError(
                "임베딩 모델이 초기화되지 않았습니다. "
                "설정 탭 → LLM에서 임베딩 Provider와 API Key를 확인하세요."
            )

        def notify(msg: str):
            logger.info(msg)
            if progress:
                progress(msg)

        notify("전체 재색인 시작...")
        self.vector_store.delete_all()

        with db_session(self.doc_repo.db_path) as conn:
            rows = conn.execute(
                "SELECT id, cleaned_body FROM documents WHERE is_deleted = 0"
            ).fetchall()

        total = len(rows)
        for idx, row in enumerate(rows, 1):
            notify(f"재색인 [{idx}/{total}]")
            self._process_chunks_and_embeddings(row["id"], row["cleaned_body"] or "")

        notify(f"재색인 완료: {total}건")
        return {"reindexed": total}
