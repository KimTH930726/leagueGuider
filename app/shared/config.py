"""
설정 로드 우선순위:
  1. AppConfig 기본값 (코드)
  2. config/config.json (비민감 설정, 초기 부트스트랩용)
  3. SQLite app_settings (모든 설정 + 민감 자격증명) ← 최종 우선

저장:
  - save_config_to_json(): 비민감 설정만 config.json에 저장 (자격증명 제외)
  - save_config_to_db():   모든 설정을 SQLite에 저장 (자격증명 포함)
  - save_config():         두 곳 모두 저장 (일반 호환용)
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.shared.exceptions import ConfigError

BASE_DIR = Path(__file__).parent.parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = BASE_DIR / "config" / "config.json"
CONFIG_EXAMPLE_PATH = BASE_DIR / "config" / "config.example.json"


@dataclass
class AppConfig:
    # ── Confluence ────────────────────────────────────────────────────
    confluence_base_url: str = ""
    root_page_id: str = ""
    confluence_type: str = "server"   # "server" | "cloud"
    auth_type: str = "token"
    auth_username: str = ""
    auth_token: str = ""              # 민감 — DB 전용

    # ── 임베딩 ────────────────────────────────────────────────────────
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    local_model_name: str = "paraphrase-multilingual-mpnet-base-v2"
    local_model_dir: str = ""   # 비어있으면 data/models/ 자동설정

    # ── LLM 공통 ──────────────────────────────────────────────────────
    llm_provider: str = "openai"      # "openai" | "inhouse"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""             # 민감 — DB 전용

    # ── InHouse LLM ───────────────────────────────────────────────────
    inhouse_llm_url: str = "https://devx-mcp-api.shinsegae-inc.com/api/v1/mcp-command/chat"
    inhouse_llm_api_key: str = ""     # 민감 — DB 전용
    inhouse_llm_usecase_id: str = ""
    inhouse_llm_project_id: str = ""
    inhouse_llm_agent_code: str = "playground"
    inhouse_llm_timeout: int = 120

    # ── 동기화 ────────────────────────────────────────────────────────
    sync_threshold_new: int = 5
    sync_threshold_updated: int = 10
    extract_metadata: bool = True

    # ── 런타임 경로 (저장 제외) ─────────────────────────────────────
    db_path: str = ""
    chroma_path: str = ""

    def __post_init__(self):
        if not self.db_path:
            self.db_path = str(DATA_DIR / "league_guider.db")
        if not self.chroma_path:
            self.chroma_path = str(DATA_DIR / "chroma")

    @property
    def is_confluence_configured(self) -> bool:
        return bool(self.confluence_base_url and self.root_page_id and self.auth_token)

    @property
    def is_llm_configured(self) -> bool:
        if self.llm_provider == "inhouse":
            return bool(self.inhouse_llm_url)
        return bool(self.llm_api_key)

    # config.json에 저장하지 않을 필드
    _SENSITIVE = {"auth_token", "llm_api_key", "inhouse_llm_api_key"}
    # 런타임 계산 필드 (저장 제외)
    _RUNTIME = {"db_path", "chroma_path"}

    def to_json_dict(self) -> dict:
        """config.json 저장용 — 민감 정보 제외."""
        return {
            k: v for k, v in self.__dict__.items()
            if k not in self._SENSITIVE and k not in self._RUNTIME
        }

    def to_db_dict(self) -> dict:
        """DB 저장용 — 민감 정보 포함, 런타임 필드 제외."""
        d = {k: v for k, v in self.__dict__.items() if k not in self._RUNTIME}
        # bool → int (SQLite)
        if "extract_metadata" in d:
            d["extract_metadata"] = 1 if d["extract_metadata"] else 0
        return d


_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """
    config.json → AppConfig 로드 (DB 오버레이 전 단계).
    init_app()에서 run_migrations() 후 overlay_db_settings()를 별도 호출해야 함.
    """
    global _config
    if _config is not None:
        return _config

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    source = CONFIG_PATH if CONFIG_PATH.exists() else (
        CONFIG_EXAMPLE_PATH if CONFIG_EXAMPLE_PATH.exists() else None
    )

    if source is None:
        _config = AppConfig()
        return _config

    try:
        with open(source, "r", encoding="utf-8") as f:
            data = json.load(f)
        valid_keys = AppConfig.__dataclass_fields__.keys()
        # _comment 같은 메타 필드 필터링
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        _config = AppConfig(**filtered)
    except Exception as e:
        raise ConfigError(f"설정 파일 로드 실패: {e}") from e

    return _config


def overlay_db_settings(config: AppConfig) -> None:
    """
    SQLite app_settings → AppConfig 오버레이.
    DB에 저장된 값이 있으면 config.json 값보다 우선.
    migrations 실행 후에 호출해야 함.
    """
    try:
        from app.infrastructure.db.settings_repository import SettingsRepository
        repo = SettingsRepository(config.db_path)
        row = repo.get()
        if not row:
            return

        valid_keys = set(AppConfig.__dataclass_fields__.keys()) - {"db_path", "chroma_path"}
        # 기본값으로 타입을 추론
        defaults = AppConfig()
        for key in valid_keys:
            db_val = row.get(key)
            if db_val is None:
                continue
            if isinstance(db_val, str) and db_val == "":
                continue
            # 기본값의 타입에 맞춰 변환
            default_val = getattr(defaults, key)
            try:
                if isinstance(default_val, bool):
                    setattr(config, key, bool(int(db_val)))
                elif isinstance(default_val, int):
                    setattr(config, key, int(db_val))
                else:
                    setattr(config, key, db_val)
            except (ValueError, TypeError):
                setattr(config, key, db_val)
    except Exception as e:
        # DB 오버레이 실패해도 config.json 값으로 동작 — 단, 로그는 남김
        try:
            from app.shared.logger import get_logger as _get_logger
            _get_logger().warning(f"DB 설정 오버레이 실패 (config.json 값으로 동작): {e}")
        except Exception:
            pass


def save_config_to_json(config: AppConfig) -> None:
    """비민감 설정만 config.json에 저장."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config.to_json_dict(), f, ensure_ascii=False, indent=2)


def save_config_to_db(config: AppConfig) -> None:
    """모든 설정(자격증명 포함)을 SQLite에 저장."""
    from app.infrastructure.db.settings_repository import SettingsRepository
    repo = SettingsRepository(config.db_path)
    repo.save(config.to_db_dict())


def save_config(config: AppConfig) -> None:
    """config.json + SQLite 양쪽 저장 (일반 호환용)."""
    global _config
    save_config_to_json(config)
    save_config_to_db(config)
    _config = config


def reload_config() -> AppConfig:
    global _config
    _config = None
    return get_config()
