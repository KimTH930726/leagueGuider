import json
from typing import Optional
from app.infrastructure.db.connection import db_session
from app.shared.text_utils import now_kst_str as _now_kst


class ReportRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def save(self, report: dict) -> int:
        now = _now_kst()
        with db_session(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO reports
                       (report_type, period_key, period_start, period_end,
                        based_on_document_count, summary_text, highlights_json,
                        created_at, updated_at)
                   VALUES
                       (:report_type, :period_key, :period_start, :period_end,
                        :based_on_document_count, :summary_text, :highlights_json,
                        :created_at, :updated_at)
                   ON CONFLICT(period_key) DO UPDATE SET
                       summary_text            = excluded.summary_text,
                       highlights_json         = excluded.highlights_json,
                       based_on_document_count = excluded.based_on_document_count,
                       updated_at              = excluded.updated_at""",
                {
                    **report,
                    "created_at": report.get("created_at", now),
                    "updated_at": now,
                    "highlights_json": json.dumps(
                        report.get("highlights_json", {}), ensure_ascii=False
                    ) if isinstance(report.get("highlights_json"), dict) else report.get("highlights_json", "{}"),
                },
            )
            if cur.lastrowid:
                return cur.lastrowid
            row = conn.execute(
                "SELECT id FROM reports WHERE period_key = ?", (report["period_key"],)
            ).fetchone()
            return row["id"]

    def get_by_type(self, report_type: str, perspective: str = "leadership") -> list[dict]:
        """perspective 접미사로 필터링. 복합 period_key = '{key}:{perspective}'"""
        suffix = f":{perspective}"
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM reports
                   WHERE report_type = ? AND period_key LIKE ?
                   ORDER BY period_key DESC""",
                (report_type, f"%{suffix}"),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["highlights_json"] = json.loads(d.get("highlights_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                d["highlights_json"] = {}
            # 원본 period_key 노출 (접미사 제거)
            d["_period_key"] = d["period_key"].removesuffix(suffix)
            result.append(d)
        return result

    def get_by_period_key(self, db_key: str) -> Optional[dict]:
        """db_key = '{period_key}:{perspective}' 복합 키로 조회"""
        with db_session(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM reports WHERE period_key = ?", (db_key,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["highlights_json"] = json.loads(d.get("highlights_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["highlights_json"] = {}
        return d
