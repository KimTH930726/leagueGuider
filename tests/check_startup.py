import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.shared.config import get_config
from app.infrastructure.db.migrations import run_migrations

config = get_config()
run_migrations(config.db_path)
print("config OK")
print("  confluence_base_url:", config.confluence_base_url)
print("  root_page_id:", config.root_page_id)
print("  embedding_provider:", config.embedding_provider)
print("  db_path:", config.db_path)

from app.infrastructure.vector.chroma_store import ChromaStore
store = ChromaStore(config.chroma_path)
print("  chromadb OK, vectors:", store.count())

print("\n[OK] 앱 기동 전 체크 통과")
