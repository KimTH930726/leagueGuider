"""
app_settings 테이블 CRUD.

- id=1 단일 행으로 운영 (앱 전체 설정)
- 민감 정보(auth_token, api_key 등)는 OS 키체인(keyring)에 저장
  SQLite에는 항상 빈값 유지 → DB 파일 직접 열람으로 키 탈취 불가
- config.json은 초기 부트스트랩용. 실제 운영은 이 테이블이 진실 원천.
"""
from typing import Optional
from app.infrastructure.db.connection import db_session
from app.shared.secret_store import save_secret, load_secret, delete_secret

# AppConfig 필드 중 DB에 저장할 항목 (db_path, chroma_path 제외)
_FIELDS = [
    "confluence_base_url",
    "root_page_id",
    "confluence_type",
    "auth_type",
    "auth_username",
    "auth_token",
    "embedding_provider",
    "embedding_model",
    "local_model_name",
    "local_model_dir",
    "llm_provider",
    "llm_model",
    "llm_api_key",
    "inhouse_llm_url",
    "inhouse_llm_api_key",
    "inhouse_llm_usecase_id",
    "inhouse_llm_project_id",
    "inhouse_llm_agent_code",
    "inhouse_llm_timeout",
    "last_sync_at",
    "sync_threshold_new",
    "sync_threshold_updated",
    "extract_metadata",
]

# 민감 정보 필드 목록 (config.json 저장 제외 대상)
SENSITIVE_FIELDS = {"auth_token", "llm_api_key", "inhouse_llm_api_key"}


class SettingsRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get(self) -> Optional[dict]:
        """
        저장된 설정 반환. 없으면 None.
        민감 필드는 OS 키체인에서 오버레이.
        기존 평문 값이 DB에 남아있으면 자동으로 키체인으로 마이그레이션.
        """
        with db_session(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM app_settings WHERE id = 1"
            ).fetchone()
        if row is None:
            return None

        result = dict(row)
        migrate_fields: list[str] = []

        for field in SENSITIVE_FIELDS:
            db_val = result.get(field) or ""
            if db_val:
                # 평문이 DB에 있음 → 키체인으로 마이그레이션
                if save_secret(field, db_val):
                    migrate_fields.append(field)
                result[field] = db_val  # 마이그레이션 후에도 이번 반환값은 유지

        if migrate_fields:
            # DB에서 평문 제거
            self._clear_sensitive_in_db(migrate_fields)

        # 키체인에서 민감 필드 로드 (마이그레이션 완료 후에는 항상 여기서 읽음)
        if not migrate_fields:
            for field in SENSITIVE_FIELDS:
                result[field] = load_secret(field)

        return result

    def _clear_sensitive_in_db(self, fields: list[str]) -> None:
        """DB의 민감 필드를 빈값으로 초기화 (키체인 마이그레이션 후 호출)."""
        set_clause = ", ".join(f"{f} = ''" for f in fields)
        with db_session(self.db_path) as conn:
            conn.execute(
                f"UPDATE app_settings SET {set_clause} WHERE id = 1"
            )

    def save(self, settings: dict) -> None:
        """
        settings dict의 값을 DB에 upsert.
        민감 필드는 OS 키체인에 저장하고 DB에는 빈값 기록.
        제공된 키만 업데이트 (partial update 지원).
        """
        # 민감 필드는 키체인에 저장, DB용 딕셔너리에서 빈값 처리
        db_settings = dict(settings)
        for field in SENSITIVE_FIELDS:
            if field in settings:
                val = settings[field] or ""
                if val:
                    save_secret(field, val)
                else:
                    delete_secret(field)
                db_settings[field] = ""  # DB에는 항상 빈값

        with db_session(self.db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM app_settings WHERE id = 1"
            ).fetchone()
            if exists is None:
                # 최초 INSERT
                cols = ", ".join(_FIELDS)
                placeholders = ", ".join(f":{f}" for f in _FIELDS)
                row = {f: db_settings.get(f, _default(f)) for f in _FIELDS}
                conn.execute(
                    f"INSERT INTO app_settings (id, {cols}) VALUES (1, {placeholders})",
                    row,
                )
            else:
                # 제공된 필드만 UPDATE
                fields_to_update = [f for f in _FIELDS if f in db_settings]
                if not fields_to_update:
                    return
                set_clause = ", ".join(f"{f} = :{f}" for f in fields_to_update)
                conn.execute(
                    f"UPDATE app_settings SET {set_clause} WHERE id = 1",
                    {f: db_settings[f] for f in fields_to_update},
                )

    def update_last_sync(self, dt_iso: str) -> None:
        self.save({"last_sync_at": dt_iso})

    def get_credentials(self) -> dict:
        """자격증명 필드만 OS 키체인에서 반환."""
        return {f: load_secret(f) for f in SENSITIVE_FIELDS}

    def save_credentials(self, creds: dict) -> None:
        """자격증명만 부분 업데이트 (키체인 저장)."""
        filtered = {k: v for k, v in creds.items() if k in SENSITIVE_FIELDS}
        self.save(filtered)

    def clear_credential(self, field: str) -> None:
        """특정 자격증명 키체인에서 삭제."""
        if field in SENSITIVE_FIELDS:
            delete_secret(field)


def _default(field: str):
    """필드별 기본값."""
    defaults = {
        "confluence_base_url": "",
        "root_page_id": "",
        "confluence_type": "server",
        "auth_type": "token",
        "auth_username": "",
        "auth_token": "",
        "embedding_provider": "openai",
        "embedding_model": "text-embedding-3-small",
        "llm_provider": "openai",
        "llm_model": "gpt-4o-mini",
        "llm_api_key": "",
        "inhouse_llm_url": "",
        "inhouse_llm_api_key": "",
        "inhouse_llm_usecase_id": "",
        "inhouse_llm_project_id": "",
        "inhouse_llm_agent_code": "playground",
        "inhouse_llm_timeout": 120,
        "last_sync_at": None,
        "sync_threshold_new": 5,
        "sync_threshold_updated": 10,
        "extract_metadata": 1,
    }
    return defaults.get(field, "")
