# LeagueGuider — 아키텍처

## 1. 레이어드 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│                     UI Layer                            │
│  dashboard.py  search.py  report.py  settings.py        │
│  _helpers.py  (render_progress_bar, render_pager 공통 위젯) │
│  (Streamlit tabs — 상태는 session_state로 관리)          │
└───────────────────────┬─────────────────────────────────┘
                        │ 의존
┌───────────────────────▼─────────────────────────────────┐
│                 Application Layer                        │
│  SyncService   SearchService   ReportService             │
│  DashboardService  QueryRewriter  Reranker               │
└───────────────────────┬─────────────────────────────────┘
                        │ 의존
┌───────────────────────▼─────────────────────────────────┐
│               Infrastructure Layer                       │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  Confluence  │  │     DB       │  │   Embedding   │  │
│  │  client.py   │  │  SQLite      │  │  OpenAI /     │  │
│  │  parser.py   │  │  FTS5        │  │  Local(ST)    │  │
│  └──────────────┘  └──────────────┘  └───────────────┘  │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐                     │
│  │    Vector    │  │     LLM      │                     │
│  │  ChromaDB    │  │  OpenAI /    │                     │
│  │  cosine sim  │  │  InHouse     │                     │
│  └──────────────┘  └──────────────┘                     │
└─────────────────────────────────────────────────────────┘
                        │ 의존
┌───────────────────────▼─────────────────────────────────┐
│                  Domain Layer                            │
│  Document  DocumentChunk  DocumentMetadata               │
│  SearchResult  Report  DashboardStats  SyncSummary       │
│  (순수 dataclass — 외부 의존성 없음)                     │
└─────────────────────────────────────────────────────────┘
```

---

## 2. 데이터 저장소

### SQLite 스키마

```
documents
├── id (PK)
├── confluence_page_id (UNIQUE)
├── parent_page_id
├── title, url, author
├── created_at, updated_at, version
├── raw_body, cleaned_body
├── content_hash          ← 변경 감지용 MD5
└── is_deleted

document_chunks
├── id (PK)
├── document_id (FK → documents)
├── chunk_index
├── chunk_text
└── token_count

document_metadata
├── document_id (FK, UNIQUE)
├── agent_name, one_line_summary
├── problem, solution
├── tech_stack_json       ← JSON 배열
├── effects_json          ← JSON 배열
├── keywords_json         ← JSON 배열
├── stage, category       ← category="추출불가": 자동 sync에서 hash 미변경 시 재추출 제외 (수동 정비는 항상 재시도)
└── meta_extracted_at     ← 마지막 추출 시각 (신규/수정건 정비 기준)

documents_fts             ← FTS5 가상 테이블
├── title
└── cleaned_body
    (documents와 INSERT/UPDATE/DELETE 트리거로 동기화)

reports
├── id (PK)
├── report_type           ← "weekly" | "monthly"
├── period_key (UNIQUE)   ← "2025-04:leadership" 형태
├── period_start, period_end
├── based_on_document_count
├── summary_text
├── highlights_json
└── created_at, updated_at

app_settings              ← 단일 행 (id=1)
└── 모든 설정 컬럼 (민감 필드는 항상 빈값 — 실제 값은 OS 키체인에 저장)

sync_history
├── sync_type, started_at, finished_at
├── status
└── new_count, updated_count, deleted_count
```

### ChromaDB

```
Collection: "league_documents"
Metric: cosine similarity
Document ID: "{document_id}_{chunk_index}"
Metadata: { document_id, chunk_index }
```

---

## 3. 공급자 추상화 (Provider Pattern)

```
EmbeddingProviderBase (ABC)
    ├── OpenAIEmbeddingProvider    → text-embedding-3-small
    └── LocalEmbeddingProvider     → sentence-transformers (오프라인)

LLMProviderBase (ABC)
    ├── OpenAILLMProvider          → GPT-4o-mini
    └── InHouseLLMProvider         → DevX MCP API (사내망)

LLMFactory.create_llm_provider(config) → 공급자 선택
```

공급자를 추가할 때 base 클래스만 구현하면 나머지 레이어 변경 불필요.

---

## 4. 설정 관리

```
우선순위 (낮음 → 높음)
AppConfig 기본값
    → config/config.json (비민감 설정, bootstrap용)
        → SQLite app_settings (모든 설정 + 자격증명) ← 최종 진실
```

| 저장 위치 | 저장 항목 |
|-----------|-----------|
| config.json | Confluence URL, 임베딩 공급자, 동기화 임계값 등 |
| SQLite | 위 항목 전체 (민감 필드는 빈값) |
| OS 키체인 (keyring) | auth_token, llm_api_key, inhouse_llm_api_key — Windows: Credential Manager / macOS: Keychain |

**민감정보 마이그레이션**: 기존에 SQLite 평문으로 저장된 값이 있으면 `get()` 호출 시 자동으로 키체인으로 이전 후 DB 컬럼 클리어.

---

## 5. 청킹 전략

```
chunk_text(text, chunk_size=800, overlap=150)
    │
    ├─ _split_by_headings() 호출
    │       헤딩 감지 패턴:
    │       - 숫자 헤딩: "^\d+(?:-\d+)?\.\s+\S"  (예: 1. 개요, 4-1. PAD)
    │       - 마크다운:  "^#{1,4}\s+"              (예: ## 기술스택)
    │
    ├─ 헤딩 2개 이상 감지 → 섹션 기반 청킹
    │       각 섹션을 의미 단위로 유지
    │       chunk_size 초과 섹션은 문단 기반 재분할
    │
    └─ 헤딩 미감지 → 문단(\\n\\n) 기반 청킹 폴백
            마지막 문단 단위 overlap 적용
            최종 chunk는 길이 무관 무조건 포함
```

---

## 6. 검색 스코어링 상세

### RRF (Reciprocal Rank Fusion)

```
keyword_score(doc) += 1.0 × 1/(60 + rank + 1)   ← 원문 키워드
keyword_score(doc) += 0.5 × 1/(60 + rank + 1)   ← 확장어 키워드
vector_score(doc)  += 1.0 × 1/(60 + rank + 1)   ← 벡터 검색

final_rrf_score = keyword_score + vector_score
```

### Heuristic Rerank

```
boost = 0.0
for token in query_tokens:
    if token in title or agent_name:  boost += 0.40
    if token in one_line_summary:     boost += 0.20
    if token in problem:              boost += 0.15
    if token in tech_stack:           boost += 0.15
    if token in category:             boost += 0.10

final_score = rrf_score × (1.0 + min(boost, 1.20))
```

### 벡터 동적 Threshold

```
if len(hits) < 5:
    threshold = 0.01          ← 희소 결과 보호
else:
    threshold = max(0.05, min(0.20, max_score × 0.50))
```

---

## 7. 리포트 period_key 설계

```
같은 테이블(reports)에 두 관점 저장:

period_key = "2025-04:leadership"    ← 리더십 관점
period_key = "2025-04:practitioner"  ← 실무자 관점

UNIQUE 제약 → 동일 기간·관점 중복 방지
조회: WHERE period_key LIKE '%:leadership'
노출: removesuffix(":leadership") → "2025-04"
```

---

## 8. 비동기 UI 패턴 (백그라운드 스레드)

검색·리포트 생성처럼 수 초가 걸리는 작업은 백그라운드 스레드로 실행해
탭 전환 중에도 작업이 중단되지 않는다.

```
UI 클릭
  → session_state["_is_searching"] = True
  → threading.Thread(target=_run_search_thread, daemon=True).start()
  → st.rerun()  ← 버튼 비활성화 상태로 재렌더

BackgroundThread
  → 실행 완료 후 session_state["_search_results"] = results
  → session_state["_is_searching"] = False
  → session_state["_search_just_done"] = True

@st.fragment(run_every=2)  ← 2초마다 폴링
  → _search_just_done 감지 → st.rerun() → 결과 표시
```

**동시 실행 방어 (2중 잠금)**

| 레이어 | 방어 수단 |
|--------|-----------|
| UI | `disabled=is_searching` — 버튼/입력 비활성화 |
| Service | `_search_lock.acquire(blocking=False)` — 즉시 거부 |

**자동 현행화 싱크 워처 (main.py)**

```
앱 시작 → _start_bg_sync()
    → _sync_running = True, _sync_start_time = monotonic()
    → daemon thread 시작

@st.fragment(run_every=3)  ← st.title() 이후에 렌더 (최상단 노출 방지)
    → _sync_running == True AND elapsed < 600s → st.caption 표시
    → _sync_running == True AND elapsed >= 600s → 타임아웃 강제 초기화
    → _sync_running == False + result 있음 → st.toast + st.rerun()
    → _sync_running == False + result 없음 → 아무것도 렌더하지 않음
```

---

## 9. 그레이스풀 디그레이데이션

| 설정 상태 | 동작 |
|-----------|------|
| 임베딩 미설정 | 키워드(FTS5) 검색만 동작 |
| LLM 미설정 | 동기화·검색 정상 동작, 메타데이터 추출·리포트 비활성 |
| Fallback metadata | 재동기화 시 LLM 재추출 강제 (content_hash 무관) |
| LLM 추출 실패 | 빈 메타데이터 또는 "추출불가"로 저장, 동기화 중단 없음 |
| category="추출불가" | 자동 sync: content_hash 동일 시 재추출 스킵; 수동 정비: 항상 재시도 |
| 벡터 검색 결과 없음 | 키워드 결과만으로 RRF 처리 |
