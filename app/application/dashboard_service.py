import json
from collections import Counter
from datetime import timedelta
from app.shared.text_utils import now_kst

from app.infrastructure.db.connection import db_session
from app.domain.models import DashboardStats
from app.shared.logger import get_logger

logger = get_logger()


class DashboardService:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get_stats(self) -> DashboardStats:
        now = now_kst()
        week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%dT00:00:00")
        prev_week_start = (now - timedelta(days=now.weekday() + 7)).strftime("%Y-%m-%dT00:00:00")
        month_start = now.strftime("%Y-%m-01T00:00:00")
        thirty_days_ago = (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")

        with db_session(self.db_path) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE is_deleted = 0"
            ).fetchone()[0]

            week_new = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE is_deleted=0 AND created_at >= ?",
                (week_start,),
            ).fetchone()[0]

            prev_week_new = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE is_deleted=0 "
                "AND created_at >= ? AND created_at < ?",
                (prev_week_start, week_start),
            ).fetchone()[0]

            week_updated = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE is_deleted=0 "
                "AND updated_at >= ? AND created_at < ?",
                (week_start, week_start),
            ).fetchone()[0]

            month_new = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE is_deleted=0 AND created_at >= ?",
                (month_start,),
            ).fetchone()[0]

            month_updated = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE is_deleted=0 "
                "AND updated_at >= ? AND created_at < ?",
                (month_start, month_start),
            ).fetchone()[0]

            top_keywords, top_tech, top_effects = self._aggregate_json_fields(conn, top_n=10)
            monthly_trend = self._monthly_trend(conn, months=12)
            top_categories = self._count_field(conn, "document_metadata", "category", top_n=10)
            top_authors = self._count_field(conn, "documents", "author", top_n=10,
                                            extra_where="is_deleted = 0")

            recent_rows = conn.execute(
                """SELECT d.title, d.url, d.author, d.created_at, dm.agent_name, dm.category
                   FROM documents d
                   LEFT JOIN document_metadata dm ON d.id = dm.document_id
                   WHERE d.is_deleted = 0
                   ORDER BY d.created_at DESC LIMIT 5"""
            ).fetchall()
            recent_documents = [dict(r) for r in recent_rows]

            last_sync_row = conn.execute(
                "SELECT finished_at FROM sync_history ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_sync_at = last_sync_row["finished_at"] if last_sync_row else None

            category_trend = self._category_trend(conn, week_start, prev_week_start)
            top_agents = self._top_agents(conn, recent_cutoff=thirty_days_ago, limit=3)
            data_quality = self._data_quality(conn)

        return DashboardStats(
            total_documents=total,
            week_new=week_new,
            week_updated=week_updated,
            month_new=month_new,
            month_updated=month_updated,
            last_sync_at=last_sync_at,
            prev_week_new=prev_week_new,
            top_keywords=top_keywords,
            top_tech_stacks=top_tech,
            top_effects=top_effects,
            monthly_trend=monthly_trend,
            top_categories=top_categories,
            top_authors=top_authors,
            recent_documents=recent_documents,
            category_trend=category_trend,
            top_agents=top_agents,
            data_quality=data_quality,
        )

    # ── 기존 집계 메서드 ──────────────────────────────────────────────

    def _monthly_trend(self, conn, months: int = 12) -> list[tuple[str, int]]:
        rows = conn.execute(
            "SELECT substr(created_at, 1, 7) as ym, COUNT(*) as cnt "
            "FROM documents WHERE is_deleted = 0 "
            "GROUP BY ym ORDER BY ym DESC LIMIT ?",
            (months,),
        ).fetchall()
        return [(r[0], r[1]) for r in reversed(rows)]

    def _count_field(
        self, conn, table: str, field: str, top_n: int = 10, extra_where: str = ""
    ) -> list[tuple[str, int]]:
        where = f"WHERE {field} IS NOT NULL AND {field} != ''"
        if extra_where:
            where += f" AND {extra_where}"
        rows = conn.execute(
            f"SELECT {field}, COUNT(*) as cnt FROM {table} {where} "
            f"GROUP BY {field} ORDER BY cnt DESC LIMIT ?",
            (top_n,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def _aggregate_json_fields(
        self, conn, top_n: int = 10
    ) -> tuple[list[tuple[str, int]], list[tuple[str, int]], list[tuple[str, int]]]:
        """keywords/tech_stack/effects 세 필드를 단일 쿼리로 집계."""
        rows = conn.execute(
            "SELECT keywords_json, tech_stack_json, effects_json FROM document_metadata"
        ).fetchall()
        kw_c: Counter = Counter()
        tech_c: Counter = Counter()
        eff_c: Counter = Counter()
        for row in rows:
            for raw, counter in ((row[0], kw_c), (row[1], tech_c), (row[2], eff_c)):
                if not raw:
                    continue
                try:
                    items = json.loads(raw)
                    if isinstance(items, list):
                        counter.update(items)
                except (json.JSONDecodeError, TypeError):
                    pass
        return kw_c.most_common(top_n), tech_c.most_common(top_n), eff_c.most_common(top_n)

    # ── 신규 집계 메서드 ──────────────────────────────────────────────

    def _category_trend(self, conn, week_start: str, prev_week_start: str) -> list[dict]:
        """카테고리별 전체/이번주/전주 건수 비교."""
        rows = conn.execute(
            """
            SELECT
                dm.category,
                COUNT(*) as total,
                SUM(CASE WHEN d.created_at >= ? THEN 1 ELSE 0 END) as this_week,
                SUM(CASE WHEN d.created_at >= ? AND d.created_at < ? THEN 1 ELSE 0 END) as last_week
            FROM documents d
            JOIN document_metadata dm ON d.id = dm.document_id
            WHERE d.is_deleted = 0 AND dm.category IS NOT NULL AND dm.category != ''
            GROUP BY dm.category
            ORDER BY total DESC
            LIMIT 10
            """,
            (week_start, prev_week_start, week_start),
        ).fetchall()
        return [
            {
                "category": r["category"],
                "total": r["total"],
                "this_week": r["this_week"],
                "last_week": r["last_week"],
                "delta": r["this_week"] - r["last_week"],
            }
            for r in rows
        ]

    def _top_agents(self, conn, recent_cutoff: str, limit: int = 3) -> list[dict]:
        """메타데이터 풍부도 + 최신성 기준 대표 에이전트."""
        rows = conn.execute(
            """
            SELECT
                d.title, d.url, d.author, d.created_at,
                dm.agent_name, dm.one_line_summary,
                dm.tech_stack_json, dm.effects_json, dm.category,
                (
                    CASE WHEN dm.one_line_summary IS NOT NULL AND dm.one_line_summary != '' THEN 3 ELSE 0 END +
                    CASE WHEN dm.problem IS NOT NULL AND dm.problem != '' THEN 2 ELSE 0 END +
                    CASE WHEN dm.solution IS NOT NULL AND dm.solution != '' THEN 2 ELSE 0 END +
                    CASE WHEN dm.tech_stack_json IS NOT NULL
                              AND dm.tech_stack_json NOT IN ('null', '[]', '') THEN 2 ELSE 0 END +
                    CASE WHEN dm.effects_json IS NOT NULL
                              AND dm.effects_json NOT IN ('null', '[]', '') THEN 1 ELSE 0 END +
                    CASE WHEN dm.category IS NOT NULL AND dm.category != '' THEN 1 ELSE 0 END +
                    CASE WHEN d.created_at >= ? THEN 2 ELSE 0 END
                ) as richness_score
            FROM documents d
            JOIN document_metadata dm ON d.id = dm.document_id
            WHERE d.is_deleted = 0
            ORDER BY richness_score DESC, d.created_at DESC
            LIMIT ?
            """,
            (recent_cutoff, limit),
        ).fetchall()

        result = []
        for r in rows:
            tech_stack: list[str] = []
            try:
                tech_stack = json.loads(r["tech_stack_json"] or "[]") or []
            except (json.JSONDecodeError, TypeError):
                pass
            effects: list[str] = []
            try:
                effects = json.loads(r["effects_json"] or "[]") or []
            except (json.JSONDecodeError, TypeError):
                pass
            result.append({
                "title": r["title"],
                "url": r["url"] or "",
                "author": r["author"] or "",
                "created_at": (r["created_at"] or "")[:10],
                "agent_name": r["agent_name"] or r["title"],
                "one_line_summary": r["one_line_summary"] or "",
                "tech_stack": tech_stack[:5],
                "effects": effects[:3],
                "category": r["category"] or "",
                "richness_score": r["richness_score"],
            })
        return result

    def _data_quality(self, conn) -> dict:
        """문서 메타데이터 추출 완성도 집계."""
        row = conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN dm.document_id IS NOT NULL THEN 1 ELSE 0 END) as with_metadata,
                SUM(CASE WHEN dm.agent_name IS NOT NULL AND dm.agent_name != '' THEN 1 ELSE 0 END) as with_name,
                SUM(CASE WHEN dm.tech_stack_json IS NOT NULL
                              AND dm.tech_stack_json NOT IN ('null', '[]', '') THEN 1 ELSE 0 END) as with_tech,
                SUM(CASE WHEN dm.category IS NOT NULL AND dm.category != '' THEN 1 ELSE 0 END) as with_category
            FROM documents d
            LEFT JOIN document_metadata dm ON d.id = dm.document_id
            WHERE d.is_deleted = 0
            """
        ).fetchone()
        total = row["total"] or 1
        return {
            "total": row["total"] or 0,
            "with_metadata": row["with_metadata"] or 0,
            "with_tech": row["with_tech"] or 0,
            "with_category": row["with_category"] or 0,
            "pct_meta": round((row["with_metadata"] or 0) / total * 100),
            "pct_tech": round((row["with_tech"] or 0) / total * 100),
            "pct_cat": round((row["with_category"] or 0) / total * 100),
        }
