import sqlite3
from app.infrastructure.db.connection import db_session
from app.shared.logger import get_logger

logger = get_logger()

_DDL = """
CREATE TABLE IF NOT EXISTS documents (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    confluence_page_id  TEXT    NOT NULL UNIQUE,
    parent_page_id      TEXT,
    title               TEXT    NOT NULL,
    url                 TEXT,
    author              TEXT,
    created_at          TEXT,
    updated_at          TEXT,
    version             INTEGER NOT NULL DEFAULT 0,
    raw_body            TEXT,
    cleaned_body        TEXT,
    content_hash        TEXT,
    is_deleted          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    chunk_text      TEXT    NOT NULL,
    token_count     INTEGER DEFAULT 0,
    UNIQUE(document_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS document_metadata (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id         INTEGER NOT NULL UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
    agent_name          TEXT,
    one_line_summary    TEXT,
    problem             TEXT,
    solution            TEXT,
    tech_stack_json     TEXT DEFAULT '[]',
    effects_json        TEXT DEFAULT '[]',
    keywords_json       TEXT DEFAULT '[]',
    stage               TEXT,
    category            TEXT,
    meta_extracted_at   TEXT
);

CREATE TABLE IF NOT EXISTS sync_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_type       TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL DEFAULT 'running',
    new_count       INTEGER DEFAULT 0,
    updated_count   INTEGER DEFAULT 0,
    deleted_count   INTEGER DEFAULT 0,
    message         TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type                 TEXT NOT NULL,
    period_key                  TEXT NOT NULL UNIQUE,
    period_start                TEXT NOT NULL,
    period_end                  TEXT NOT NULL,
    based_on_document_count     INTEGER DEFAULT 0,
    summary_text                TEXT,
    highlights_json             TEXT DEFAULT '{}',
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    id                      INTEGER PRIMARY KEY DEFAULT 1,

    -- Confluence
    confluence_base_url     TEXT DEFAULT '',
    root_page_id            TEXT DEFAULT '',
    confluence_type         TEXT DEFAULT 'server',
    auth_type               TEXT DEFAULT 'token',
    auth_username           TEXT DEFAULT '',
    auth_token              TEXT DEFAULT '',

    -- 임베딩
    embedding_provider      TEXT DEFAULT 'openai',
    embedding_model         TEXT DEFAULT 'text-embedding-3-small',
    local_model_name        TEXT DEFAULT 'paraphrase-multilingual-mpnet-base-v2',
    local_model_dir         TEXT DEFAULT '',

    -- LLM 공통
    llm_provider            TEXT DEFAULT 'openai',
    llm_model               TEXT DEFAULT 'gpt-4o-mini',
    llm_api_key             TEXT DEFAULT '',

    -- InHouse LLM
    inhouse_llm_url         TEXT DEFAULT '',
    inhouse_llm_api_key     TEXT DEFAULT '',
    inhouse_llm_usecase_id  TEXT DEFAULT '',
    inhouse_llm_project_id  TEXT DEFAULT '',
    inhouse_llm_agent_code  TEXT DEFAULT 'playground',
    inhouse_llm_timeout     INTEGER DEFAULT 120,

    -- 동기화
    last_sync_at            TEXT,
    sync_threshold_new      INTEGER DEFAULT 5,
    sync_threshold_updated  INTEGER DEFAULT 10,
    extract_metadata        INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_documents_updated_at   ON documents(updated_at);
CREATE INDEX IF NOT EXISTS idx_documents_is_deleted   ON documents(is_deleted);
CREATE INDEX IF NOT EXISTS idx_sync_history_started   ON sync_history(started_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts
    USING fts5(title, cleaned_body, content='documents', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS documents_fts_insert
    AFTER INSERT ON documents BEGIN
        INSERT INTO documents_fts(rowid, title, cleaned_body)
        VALUES (new.id, new.title, new.cleaned_body);
    END;

CREATE TRIGGER IF NOT EXISTS documents_fts_update
    AFTER UPDATE ON documents BEGIN
        INSERT INTO documents_fts(documents_fts, rowid, title, cleaned_body)
        VALUES ('delete', old.id, old.title, old.cleaned_body);
        INSERT INTO documents_fts(rowid, title, cleaned_body)
        VALUES (new.id, new.title, new.cleaned_body);
    END;

CREATE TRIGGER IF NOT EXISTS documents_fts_delete
    AFTER DELETE ON documents BEGIN
        INSERT INTO documents_fts(documents_fts, rowid, title, cleaned_body)
        VALUES ('delete', old.id, old.title, old.cleaned_body);
    END;
"""

# 기존 DB에 없을 수 있는 컬럼 — ALTER TABLE로 추가 (이미 있으면 무시)
_ALTER_COLUMNS: list[tuple[str, str]] = [
    ("app_settings", "ALTER TABLE app_settings ADD COLUMN confluence_type TEXT DEFAULT 'server'"),
    ("app_settings", "ALTER TABLE app_settings ADD COLUMN inhouse_llm_url TEXT DEFAULT ''"),
    ("app_settings", "ALTER TABLE app_settings ADD COLUMN inhouse_llm_api_key TEXT DEFAULT ''"),
    ("app_settings", "ALTER TABLE app_settings ADD COLUMN inhouse_llm_usecase_id TEXT DEFAULT ''"),
    ("app_settings", "ALTER TABLE app_settings ADD COLUMN inhouse_llm_project_id TEXT DEFAULT ''"),
    ("app_settings", "ALTER TABLE app_settings ADD COLUMN inhouse_llm_agent_code TEXT DEFAULT 'playground'"),
    ("app_settings", "ALTER TABLE app_settings ADD COLUMN inhouse_llm_timeout INTEGER DEFAULT 120"),
    ("app_settings", "ALTER TABLE app_settings ADD COLUMN extract_metadata INTEGER DEFAULT 1"),
    ("app_settings", "ALTER TABLE app_settings ADD COLUMN local_model_name TEXT DEFAULT 'paraphrase-multilingual-mpnet-base-v2'"),
    ("app_settings", "ALTER TABLE app_settings ADD COLUMN local_model_dir TEXT DEFAULT ''"),
    # document_metadata: 추출 시각 추적 (신규/수정건 정비용)
    ("document_metadata", "ALTER TABLE document_metadata ADD COLUMN meta_extracted_at TEXT"),
]


def run_migrations(db_path: str) -> None:
    logger.info(f"DB 마이그레이션 실행: {db_path}")
    with db_session(db_path) as conn:
        conn.executescript(_DDL)
        # 기존 DB 스키마 업그레이드 (컬럼 추가)
        for _, stmt in _ALTER_COLUMNS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # 이미 존재하는 컬럼 — 무시
    logger.info("DB 마이그레이션 완료")
