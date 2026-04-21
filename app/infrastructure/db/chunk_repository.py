from app.infrastructure.db.connection import db_session


class ChunkRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def replace_chunks(self, document_id: int, chunks: list[dict]) -> list[int]:
        """기존 청크 삭제 후 재삽입. chunk dict: {chunk_text, token_count}"""
        ids = []
        with db_session(self.db_path) as conn:
            conn.execute(
                "DELETE FROM document_chunks WHERE document_id = ?", (document_id,)
            )
            for idx, chunk in enumerate(chunks):
                cur = conn.execute(
                    """INSERT INTO document_chunks (document_id, chunk_index, chunk_text, token_count)
                       VALUES (?, ?, ?, ?)""",
                    (document_id, idx, chunk["chunk_text"], chunk.get("token_count", 0)),
                )
                ids.append(cur.lastrowid)
        return ids

    def get_by_document_id(self, document_id: int) -> list[dict]:
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM document_chunks WHERE document_id = ?
                   ORDER BY chunk_index""",
                (document_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_by_document_id(self, document_id: int) -> None:
        with db_session(self.db_path) as conn:
            conn.execute(
                "DELETE FROM document_chunks WHERE document_id = ?", (document_id,)
            )
