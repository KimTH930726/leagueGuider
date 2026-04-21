from typing import Optional
from app.infrastructure.db.connection import db_session
from app.shared.text_utils import now_kst_str


class SyncHistoryRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def create(self, sync_type: str) -> int:
        with db_session(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO sync_history (sync_type, started_at, status) VALUES (?, ?, 'running')",
                (sync_type, now_kst_str()),
            )
            return cur.lastrowid

    def finish(self, sync_id: int, status: str, result: dict) -> None:
        with db_session(self.db_path) as conn:
            conn.execute(
                """UPDATE sync_history
                   SET finished_at=?, status=?, new_count=?, updated_count=?,
                       deleted_count=?, message=?
                   WHERE id=?""",
                (
                    now_kst_str(),
                    status,
                    result.get("new_count", 0),
                    result.get("updated_count", 0),
                    result.get("deleted_count", 0),
                    result.get("message", ""),
                    sync_id,
                ),
            )

    def get_last(self) -> Optional[dict]:
        with db_session(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM sync_history ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def get_recent(self, limit: int = 10) -> list[dict]:
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM sync_history ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
