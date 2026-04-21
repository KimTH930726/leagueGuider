from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Document:
    id: int
    confluence_page_id: str
    parent_page_id: Optional[str]
    title: str
    url: str
    author: str
    created_at: str
    updated_at: str
    version: int
    content_hash: str
    is_deleted: bool = False
    cleaned_body: Optional[str] = None


@dataclass
class DocumentChunk:
    id: int
    document_id: int
    chunk_index: int
    chunk_text: str
    token_count: int


@dataclass
class DocumentMetadata:
    document_id: int
    agent_name: Optional[str] = None
    one_line_summary: Optional[str] = None
    problem: Optional[str] = None
    solution: Optional[str] = None
    tech_stack: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    stage: Optional[str] = None
    category: Optional[str] = None


@dataclass
class Report:
    id: int
    report_type: str          # weekly / monthly
    period_key: str           # 2025-W03 / 2025-01
    period_start: str
    period_end: str
    based_on_document_count: int
    summary_text: str
    highlights: dict          # 파싱된 highlights_json
    created_at: str
    updated_at: Optional[str] = None


@dataclass
class SyncSummary:
    sync_type: str            # full / incremental
    started_at: str
    finished_at: Optional[str]
    status: str               # running / success / failed
    new_count: int = 0
    updated_count: int = 0
    deleted_count: int = 0
    message: str = ""


@dataclass
class SearchResult:
    document_id: int
    confluence_page_id: str
    title: str
    url: str
    score: float
    agent_name: Optional[str] = None
    one_line_summary: Optional[str] = None
    tech_stack: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    matched_chunk: Optional[str] = None
    author: Optional[str] = None
    updated_at: Optional[str] = None
    match_reason: Optional[str] = None  # rerank이 생성한 매칭 근거


@dataclass
class DashboardStats:
    total_documents: int
    week_new: int
    week_updated: int
    month_new: int
    month_updated: int
    last_sync_at: Optional[str]
    prev_week_new: int = 0                                                # 전주 신규 (delta 계산용)
    top_keywords: list[tuple[str, int]] = field(default_factory=list)
    top_tech_stacks: list[tuple[str, int]] = field(default_factory=list)
    monthly_trend: list[tuple[str, int]] = field(default_factory=list)   # (YYYY-MM, count)
    top_categories: list[tuple[str, int]] = field(default_factory=list)
    top_authors: list[tuple[str, int]] = field(default_factory=list)
    top_effects: list[tuple[str, int]] = field(default_factory=list)
    recent_documents: list[dict] = field(default_factory=list)
    category_trend: list[dict] = field(default_factory=list)             # {category, total, this_week, last_week, delta}
    top_agents: list[dict] = field(default_factory=list)                 # 메타 풍부도 기반 추천 에이전트 Top 3
    data_quality: dict = field(default_factory=dict)                     # {total, with_metadata, pct_meta, pct_tech, pct_cat}
