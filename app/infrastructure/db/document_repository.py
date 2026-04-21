from typing import Optional
from app.infrastructure.db.connection import db_session
from app.shared.logger import get_logger

logger = get_logger()


class DocumentRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def upsert(self, doc: dict) -> int:
        """INSERT OR REPLACE 후 rowid 반환. FTS5는 수동 갱신."""
        with db_session(self.db_path) as conn:
            # 기존 행 확인 (FTS 수동 갱신용)
            existing = conn.execute(
                "SELECT id, title, cleaned_body FROM documents WHERE confluence_page_id = ?",
                (doc["confluence_page_id"],),
            ).fetchone()

            cur = conn.execute(
                """
                INSERT INTO documents
                    (confluence_page_id, parent_page_id, title, url, author,
                     created_at, updated_at, version, raw_body, cleaned_body,
                     content_hash, is_deleted)
                VALUES
                    (:confluence_page_id, :parent_page_id, :title, :url, :author,
                     :created_at, :updated_at, :version, :raw_body, :cleaned_body,
                     :content_hash, 0)
                ON CONFLICT(confluence_page_id) DO UPDATE SET
                    title         = excluded.title,
                    url           = excluded.url,
                    author        = excluded.author,
                    updated_at    = excluded.updated_at,
                    version       = excluded.version,
                    raw_body      = excluded.raw_body,
                    cleaned_body  = excluded.cleaned_body,
                    content_hash  = excluded.content_hash,
                    is_deleted    = 0
                """,
                doc,
            )
            doc_id = cur.lastrowid or (existing["id"] if existing else None)
            if not doc_id:
                row = conn.execute(
                    "SELECT id FROM documents WHERE confluence_page_id = ?",
                    (doc["confluence_page_id"],),
                ).fetchone()
                doc_id = row["id"] if row else None

            # FTS5 수동 갱신: ON CONFLICT DO UPDATE는 AFTER UPDATE 트리거를 발동하지 않음
            if existing:
                try:
                    conn.execute(
                        "INSERT INTO documents_fts(documents_fts, rowid, title, cleaned_body) "
                        "VALUES('delete', ?, ?, ?)",
                        (existing["id"], existing["title"] or "", existing["cleaned_body"] or ""),
                    )
                    conn.execute(
                        "INSERT INTO documents_fts(rowid, title, cleaned_body) VALUES(?, ?, ?)",
                        (doc_id, doc.get("title", ""), doc.get("cleaned_body", "")),
                    )
                except Exception as _fts_err:
                    logger.warning(f"FTS5 갱신 실패 (검색 품질 저하 가능): {_fts_err}")

            return doc_id

    def get_all_meta(self) -> dict[str, dict]:
        """{confluence_page_id: row_dict} — 증분 비교용"""
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                """SELECT id, confluence_page_id, version, updated_at, content_hash
                   FROM documents WHERE is_deleted = 0"""
            ).fetchall()
        return {r["confluence_page_id"]: dict(r) for r in rows}

    def get_by_page_id(self, page_id: str) -> Optional[dict]:
        with db_session(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE confluence_page_id = ?", (page_id,)
            ).fetchone()
        return dict(row) if row else None

    def mark_deleted(self, page_id: str) -> None:
        with db_session(self.db_path) as conn:
            conn.execute(
                "UPDATE documents SET is_deleted = 1 WHERE confluence_page_id = ?",
                (page_id,),
            )

    def count(self) -> int:
        with db_session(self.db_path) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM documents WHERE is_deleted = 0"
            ).fetchone()[0]

    def search_by_keyword(
        self,
        keyword: str,
        limit: int = 20,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        """FTS5 검색. 날짜 필터를 SQL 단계에서 적용해 RRF 전에 정확한 후보 확보."""
        date_clause = ""
        date_params: list = []
        if date_from:
            date_clause += " AND d.updated_at >= ?"
            date_params.append(date_from)
        if date_to:
            date_clause += " AND d.updated_at <= ?"
            date_params.append(date_to)

        with db_session(self.db_path) as conn:
            try:
                rows = conn.execute(
                    f"""SELECT d.* FROM documents d
                       JOIN documents_fts f ON d.id = f.rowid
                       WHERE documents_fts MATCH ?
                         AND d.is_deleted = 0
                         {date_clause}
                       LIMIT ?""",
                    (keyword, *date_params, limit),
                ).fetchall()
            except Exception:
                pattern = f"%{keyword}%"
                rows = conn.execute(
                    f"""SELECT * FROM documents
                       WHERE is_deleted = 0
                         AND (title LIKE ? OR cleaned_body LIKE ?)
                         {date_clause}
                       LIMIT ?""",
                    (pattern, pattern, *date_params, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_with_metadata_by_ids(self, doc_ids: list[int]) -> list[dict]:
        if not doc_ids:
            return []
        placeholders = ",".join("?" * len(doc_ids))
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                f"""SELECT d.*, dm.agent_name, dm.one_line_summary,
                           dm.tech_stack_json, dm.effects_json, dm.keywords_json
                    FROM documents d
                    LEFT JOIN document_metadata dm ON d.id = dm.document_id
                    WHERE d.id IN ({placeholders}) AND d.is_deleted = 0""",
                doc_ids,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_by_period(self, period_start: str, period_end: str) -> list[dict]:
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                """SELECT d.id, d.title, d.url, d.author, d.created_at, d.updated_at,
                          d.cleaned_body,
                          dm.agent_name, dm.one_line_summary, dm.problem, dm.solution,
                          dm.tech_stack_json, dm.effects_json, dm.keywords_json,
                          dm.category
                   FROM documents d
                   LEFT JOIN document_metadata dm ON d.id = dm.document_id
                   WHERE d.is_deleted = 0
                     AND d.updated_at BETWEEN ? AND ?
                   ORDER BY d.updated_at DESC""",
                (period_start, period_end),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_metadata(self, document_id: int) -> Optional[dict]:
        """document_metadata 행 반환. 없으면 None."""
        with db_session(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM document_metadata WHERE document_id = ?",
                (document_id,),
            ).fetchone()
        return dict(row) if row else None

    def upsert_metadata(self, document_id: int, meta: dict) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        with db_session(self.db_path) as conn:
            conn.execute(
                """INSERT INTO document_metadata
                       (document_id, agent_name, one_line_summary, problem, solution,
                        tech_stack_json, effects_json, keywords_json, stage, category,
                        meta_extracted_at)
                   VALUES
                       (:document_id, :agent_name, :one_line_summary, :problem, :solution,
                        :tech_stack_json, :effects_json, :keywords_json, :stage, :category,
                        :meta_extracted_at)
                   ON CONFLICT(document_id) DO UPDATE SET
                       agent_name        = excluded.agent_name,
                       one_line_summary  = excluded.one_line_summary,
                       problem           = excluded.problem,
                       solution          = excluded.solution,
                       tech_stack_json   = excluded.tech_stack_json,
                       effects_json      = excluded.effects_json,
                       keywords_json     = excluded.keywords_json,
                       stage             = excluded.stage,
                       category          = excluded.category,
                       meta_extracted_at = excluded.meta_extracted_at""",
                {"document_id": document_id, "meta_extracted_at": now, **meta},
            )
