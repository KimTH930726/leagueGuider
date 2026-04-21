import json
import time
from dataclasses import dataclass, field
from typing import Optional

from app.shared.config import AppConfig
from app.shared.exceptions import SearchError
from app.shared.logger import get_logger
from app.infrastructure.db.document_repository import DocumentRepository
from app.infrastructure.vector.chroma_store import ChromaStore
from app.domain.models import SearchResult

logger = get_logger()


@dataclass
class SearchQuery:
    text: str
    mode: str = "hybrid"           # vector | keyword | hybrid
    tech_stack: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    date_from: str | None = None
    date_to: str | None = None
    top_k: int = 10
    use_llm_rewrite: bool = False  # LLM 기반 쿼리 확장 (LLM 설정 필요, 레이턴시 추가)


class SearchService:
    def __init__(
        self,
        doc_repo: DocumentRepository,
        vector_store: ChromaStore,
        embedder,
        llm=None,
    ):
        self.doc_repo = doc_repo
        self.vector_store = vector_store
        self.embedder = embedder
        self._llm = llm  # query rewrite 전용 (optional)

    @classmethod
    def from_config(cls, config: AppConfig) -> "SearchService":
        # Embedder / LLM — ServiceFactory 싱글톤 사용 (재로딩 방지)
        from app.infrastructure.service_factory import get_embedder, get_llm
        embedder = get_embedder(config)
        llm = get_llm(config) if config.is_llm_configured else None

        return cls(
            doc_repo=DocumentRepository(config.db_path),
            vector_store=ChromaStore(config.chroma_path),
            embedder=embedder,
            llm=llm,
        )

    def search(self, query: SearchQuery) -> tuple[list[SearchResult], list[str]]:
        """
        Returns (results, expanded_terms).
        Thread-safe: no shared mutable state — caller receives all output via return value.
        """
        t0 = time.perf_counter()
        logger.info("[SEARCH] start mode=%s q=%r", query.mode, query.text[:60])
        try:
            # ── 1. Query Rewrite ──────────────────────────────────────────
            t = time.perf_counter()
            from app.application.query_rewriter import rewrite as do_rewrite
            llm_for_rewrite = self._llm if query.use_llm_rewrite else None
            rewrite_result = do_rewrite(query.text, llm=llm_for_rewrite)
            expanded_terms = rewrite_result.all_terms
            vector_query   = rewrite_result.vector_query
            expanded_out   = rewrite_result.expanded or []
            logger.info("[SEARCH] rewrite %.2fs expanded=%s", time.perf_counter() - t, expanded_out[:3])

            # ── 2. Keyword Search ─────────────────────────────────────────
            keyword_hits: list[int] = []
            expanded_keyword_hits: list[int] = []
            if query.mode in ("keyword", "hybrid"):
                t = time.perf_counter()
                keyword_hits = self._keyword_search(
                    query.text, limit=query.top_k * 3,
                    date_from=query.date_from, date_to=query.date_to,
                )
                expanded_only = [t2 for t2 in expanded_terms if t2 != query.text]
                if expanded_only:
                    expanded_keyword_hits = self._keyword_search_expanded(
                        expanded_only, limit=query.top_k * 2,
                        date_from=query.date_from, date_to=query.date_to,
                    )
                logger.info("[SEARCH] keyword %.2fs hits=%d exp=%d",
                            time.perf_counter() - t, len(keyword_hits), len(expanded_keyword_hits))

            # ── 3. Vector Search ──────────────────────────────────────────
            vector_hits: list[tuple[int, float]] = []
            if query.mode in ("vector", "hybrid") and self.embedder is not None:
                t = time.perf_counter()
                vector_hits = self._vector_search(vector_query, n_results=query.top_k * 3)
                logger.info("[SEARCH] vector %.2fs hits=%d", time.perf_counter() - t, len(vector_hits))

            # ── 4. RRF 병합 ───────────────────────────────────────────────
            t = time.perf_counter()
            if query.mode == "hybrid":
                ranked = self._rrf(keyword_hits, vector_hits, expanded_keyword_hits)
            elif query.mode == "vector":
                ranked = sorted(vector_hits, key=lambda x: -x[1])
            else:
                ranked = [(doc_id, 1.0) for doc_id in keyword_hits]
            logger.info("[SEARCH] rrf %.2fs ranked=%d", time.perf_counter() - t, len(ranked))

            top_ids   = [doc_id for doc_id, _ in ranked[: query.top_k * 2]]
            score_map = {doc_id: score for doc_id, score in ranked}

            if not top_ids:
                logger.info("[SEARCH] total %.2fs → 0 results", time.perf_counter() - t0)
                return [], expanded_out

            docs = self.doc_repo.get_with_metadata_by_ids(top_ids)
            docs = self._apply_filters(docs, query)

            if not docs:
                logger.info("[SEARCH] total %.2fs → 0 after filter", time.perf_counter() - t0)
                return [], expanded_out

            results = []
            for doc in docs:
                results.append(
                    SearchResult(
                        document_id=doc["id"],
                        confluence_page_id=doc["confluence_page_id"],
                        title=doc["title"],
                        url=doc.get("url", ""),
                        score=score_map.get(doc["id"], 0.0),
                        agent_name=doc.get("agent_name"),
                        one_line_summary=doc.get("one_line_summary"),
                        tech_stack=self._parse_json_list(doc.get("tech_stack_json")),
                        effects=self._parse_json_list(doc.get("effects_json")),
                        author=doc.get("author"),
                        updated_at=doc.get("updated_at"),
                    )
                )

            results.sort(key=lambda x: -x.score)

            # ── 5. Heuristic Rerank ───────────────────────────────────────
            if len(results) > 1:
                t = time.perf_counter()
                from app.application.reranker import rerank as do_rerank
                results = do_rerank(
                    query=query.text, results=results,
                    docs_meta=docs, top_n=min(20, len(results)),
                )
                logger.info("[SEARCH] rerank %.2fs", time.perf_counter() - t)

            final = results[: query.top_k]
            logger.info("[SEARCH] total %.2fs → %d results", time.perf_counter() - t0, len(final))
            return final, expanded_out

        except SearchError:
            raise
        except Exception as e:
            raise SearchError(f"검색 실패: {e}") from e

    # ── 내부 메서드 ───────────────────────────────────────────────────────

    def _keyword_search_expanded(
        self,
        terms: list[str],
        limit: int,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[int]:
        """
        확장 쿼리 전체를 순서대로 검색 후 union.
        원문(첫 번째 term)이 가장 높은 RRF 순위를 가지도록 앞에 배치.
        """
        seen: set[int] = set()
        result: list[int] = []
        for term in terms:
            ids = self._keyword_search(term, limit=limit, date_from=date_from, date_to=date_to)
            for doc_id in ids:
                if doc_id not in seen:
                    seen.add(doc_id)
                    result.append(doc_id)
            if len(result) >= limit:
                break
        return result[:limit]

    def _keyword_search(
        self,
        text: str,
        limit: int,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[int]:
        docs = self.doc_repo.search_by_keyword(
            text, limit=limit, date_from=date_from, date_to=date_to
        )
        return [d["id"] for d in docs]

    def _vector_search(self, text: str, n_results: int) -> list[tuple[int, float]]:
        embedding = self.embedder.embed_texts([text])[0]
        hits = self.vector_store.search_similar(embedding, n_results=n_results)
        if not hits:
            return []

        max_score = max(h["score"] for h in hits)
        # 희소 결과(5건 미만)는 threshold 완화 — 너무 많이 버리면 UX 저하
        if len(hits) < 5:
            threshold = 0.01
        else:
            threshold = max(0.05, min(0.20, max_score * 0.50))

        best: dict[int, float] = {}
        for hit in hits:
            doc_id = hit["document_id"]
            score  = hit["score"]
            if doc_id and score >= threshold and score > best.get(doc_id, -1):
                best[doc_id] = score
        return list(best.items())

    def _rrf(
        self,
        keyword_ids: list[int],
        vector_ids: list[tuple[int, float]],
        expanded_ids: list[int] | None = None,
        k: int = 60,
    ) -> list[tuple[int, float]]:
        scores: dict[int, float] = {}
        for rank, doc_id in enumerate(keyword_ids):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1 / (k + rank + 1)
        if expanded_ids:
            for rank, doc_id in enumerate(expanded_ids):
                # 확장어 hit은 절반 가중치 — recall 기여는 하되 precision 보호
                scores[doc_id] = scores.get(doc_id, 0.0) + 0.5 / (k + rank + 1)
        for rank, (doc_id, _) in enumerate(sorted(vector_ids, key=lambda x: -x[1])):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1 / (k + rank + 1)
        return sorted(scores.items(), key=lambda x: -x[1])

    def _apply_filters(self, docs: list[dict], query: SearchQuery) -> list[dict]:
        result = []
        for doc in docs:
            if query.tech_stack:
                ts = self._parse_json_list(doc.get("tech_stack_json"))
                if not any(t in ts for t in query.tech_stack):
                    continue
            if query.effects:
                eff = self._parse_json_list(doc.get("effects_json"))
                if not any(e in eff for e in query.effects):
                    continue
            if query.date_from and doc.get("updated_at", "") < query.date_from:
                continue
            if query.date_to and doc.get("updated_at", "") > query.date_to:
                continue
            result.append(doc)
        return result

    @staticmethod
    def _parse_json_list(value) -> list[str]:
        if not value:
            return []
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
