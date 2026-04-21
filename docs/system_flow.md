# LeagueGuider — 시스템 흐름도

## 1. 전체 데이터 흐름

```
Confluence
    │  REST API (HTML)
    ▼
ConfluenceClient          ← Server / Cloud 양쪽 지원
    │  raw HTML
    ▼
HTMLParser (BeautifulSoup)
    │  plain text
    ▼
SyncService
    ├─ content_hash 비교 ──────── 변경 없음 → skip
    │
    ├─ DocumentRepository (SQLite)
    │       documents, document_chunks, documents_fts
    │
    ├─ Embedder (OpenAI / Local)
    │       └─ ChromaStore (ChromaDB)
    │               document_chunks 벡터 인덱스
    │
    └─ LLMExtractor
            └─ DocumentMetadata (SQLite)
                    agent_name, one_line_summary, problem,
                    solution, tech_stack, effects, category
```

---

## 2. 동기화 흐름 (Sync)

```
앱 시작
    │
    ├─ Full Sync (수동 트리거)
    │       Confluence 전체 페이지 트리 수집
    │       → 모든 페이지 upsert
    │       → 삭제된 페이지 is_deleted=1 마킹
    │
    └─ Incremental Sync (앱 시작 시 백그라운드 자동 실행)
            Confluence 원격 메타 vs. DB 로컬 메타 비교
            │
            ├─ 신규 페이지   → fetch + parse + upsert + embed + extract
            ├─ 변경 페이지   → content_hash 비교 후 변경 시 동일 처리
            └─ 삭제 페이지   → is_deleted=1

각 페이지 처리 상세:
    HTML 수신
        → BeautifulSoup 파싱 → plain text
        → content_hash(MD5) 계산
        → 해시 동일하면 skip (DB 업데이트 없음)
        → documents 테이블 upsert
        → chunk_text() 로 청킹 (헤딩 인식 우선, 문단 폴백)
        → 각 chunk → 임베딩 → ChromaDB upsert
        → LLM 메타데이터 추출 (설정 시)
            ├─ LLM 거부 응답(민감정보 등) 감지 → category="추출불가" 저장, 재시도 없음
            ├─ 파싱 실패·요약 비어있음 → 최대 2회 재시도 (1초 간격)
            ├─ 재시도 후에도 요약 빈 경우 → 문서 제목을 one_line_summary로 보완
            └─ fallback metadata(problem/category/tech 2개 이상 빈 필드)면 재추출 강제
               (단, category="추출불가"인 문서는 재추출 제외)
```

---

## 3. 검색 파이프라인

```
사용자 입력 (query text)
    │
    ▼ [UI] 검색 버튼 클릭
    │   _is_searching = True → 버튼·입력 비활성화 → st.rerun()
    │   BackgroundThread 시작 (_run_search_thread)
    │   @st.fragment(run_every=2)로 완료 감지 → st.rerun()
    │
    ▼ [Service] BackgroundThread 안에서 실행
    │   _search_lock.acquire(blocking=False)  ← 중복 실행 방어
    │
Query Rewriter
    ├─ Rule-based 동의어 확장 (항상 실행, 레이턴시 없음)
    │       원문 + 확장어 최대 5개
    │
    └─ LLM 확장 (선택, +1~2초)
            JSON 배열로 추가 검색어 반환

    ↓ all_terms = [원문, 확장어...]
    ↓ vector_query = "원문 확장어1 확장어2"

┌─────────────────────────────────────────────────────┐
│                   병렬 검색                          │
│                                                     │
│  Keyword Search (FTS5)      Vector Search (Chroma)  │
│  ┌────────────────────┐    ┌─────────────────────┐  │
│  │ 원문 → keyword_hits│    │ vector_query 임베딩  │  │
│  │   weight = 1.0     │    │   → cosine 검색      │  │
│  │                    │    │   동적 threshold     │  │
│  │ 확장어 → exp_hits  │    │   (<5건: 0.01        │  │
│  │   weight = 0.5     │    │    >=5건: max×0.5)   │  │
│  └────────────────────┘    └─────────────────────┘  │
└─────────────────────────────────────────────────────┘
    │                               │
    └───────────── RRF 병합 ────────┘
            score = Σ 1/(60 + rank + 1)
            확장어 hit은 0.5× 가중치

    ↓ top_k×2 candidates

metadata 필터 (tech_stack, effects, date)

    ↓

Heuristic Reranker
    점수 부스트:
    - 제목/에이전트명 포함: +0.40
    - 한줄요약 포함:       +0.20
    - 문제 필드 포함:      +0.15
    - 기술스택 매칭:       +0.15
    - 카테고리 매칭:       +0.10
    max boost: 1.20× (원점수 최대 2.2배)
    match_reason 생성

    ↓ top_k 결과 반환 (SearchResult[])
```

---

## 4. 리포트 생성 흐름

```
사용자 선택 (기간 + 관점)
    │
    ▼ [UI] 조회/재생성 버튼 클릭
    │   _is_reporting = True → 버튼·셀렉터 비활성화 → st.rerun()
    │   BackgroundThread 시작 (_run_report_thread)
    │   @st.fragment(run_every=2)로 완료 감지 → st.rerun()
    │   탭 전환 중에도 스레드는 계속 실행됨
    │
    ▼ [Service] BackgroundThread 안에서 실행
    │   _report_lock.acquire(blocking=False)  ← 중복 실행 방어
    │
ReportService.get_or_generate()
    │
    ├─ DB에 period_key:perspective 존재? → 기존 리포트 반환
    │
    └─ 없으면 generate()
            │
            ├─ DocumentRepository.get_by_period()
            │       해당 기간 문서 + 메타데이터 조회
            │
            ├─ 3기간 데이터 수집
            │   전전기 → 전기 → 이번 (각 get_by_period 호출)
            │   카테고리/기술스택 3기간 비교표 생성 (▲▲/▼▼/▲/▼/─ 추세)
            │
            ├─ 데이터 품질 집계 (단일 DB 세션)
            │   메타 추출률, 기술스택 보유률, 카테고리 분류율
            │   → LLM 프롬프트에 신뢰도 경고로 삽입
            │
            ├─ 전기 리포트 발췌 (연속성 컨텍스트)
            │   prev_period_key로 기존 리포트 조회
            │   앞 600자를 LLM 입력에 추가 → 전기 리스크 해소 여부 검토 가능
            │
            ├─ doc_richness_score() 기반 문서 선택
            │   score ≥ 3 (메타 풍부) 우선 → 나머지로 25건 채움
            │   할루시네이션 최소화 + 토큰 효율화
            │
            ├─ 프롬프트 구성 (관점별 분리)
            │   Leadership: 핵심 인사이트Top3·3기간 추이·리스크·실행 권고·성숙도
            │   Practitioner: 기술 인사이트Top3·구현패턴·재사용패턴·기술 리스크·실행 권고
            │
            └─ LLM 호출 → 마크다운 리포트 생성
                    reports 테이블 저장
                    period_key = "{period}:{perspective}"
```

---

## 5. 리포트 Confluence 링크 포함 흐름

```
generate() 내 doc_list 구성 시:
    for doc in docs_sorted[:25]:
        url = doc.get("url", "")
        heading = f"[{name}]({url})" if url else name  ← 마크다운 링크
        line += f"\n- URL: {url}"                       ← LLM 입력에 URL 명시

프롬프트 지침 (공통):
    6. 에이전트 언급 시 반드시 [에이전트명](URL) 링크 포함

LLM 호출 후 후처리 (_inject_missing_urls):
    url_map = {에이전트명: URL, ...}  ← selected_docs에서 구성
    _inject_missing_urls(llm_output, url_map)
        1. [이름]() 또는 [이름](실제_Confluence_URL) 등 플레이스홀더 패턴 탐지
        2. url_map으로 실제 URL 주입
        3. 헤딩 줄에 링크 없이 이름만 있는 경우도 보완

출력 형식 (섹션 4):
    ### 🤖/🔧 [에이전트명](Confluence_URL)
    | 링크 | [Confluence 페이지 바로가기](URL) |
```

---

## 6. 설정 초기화 흐름

```
앱 시작 (main.py)
    │
    ├─ config/config.json 로드 → AppConfig 생성
    │
    ├─ DB migrations 실행 (schema 보장)
    │
    ├─ SQLite app_settings 로드 → AppConfig에 overlay
    │       (DB 값이 JSON 값보다 우선)
    │       민감 필드(auth_token, llm_api_key 등)는 OS 키체인에서 로드
    │       └─ 기존 평문이 DB에 있으면 자동 마이그레이션 후 DB 클리어
    │
    ├─ 백그라운드 Incremental Sync 시작 (daemon thread)
    │
    └─ Streamlit 탭 렌더링
            대시보드 / 검색 / 리포트 / 설정
```

## 7. 데이터 정비 흐름 (설정 → 고급)

```
사용자 클릭
    │
    ├─ 전체 정비 (재추출 + 재색인)
    │       reextract_metadata() — 미추출/fallback 문서 재추출
    │       + rebuild_index()    — 전체 벡터 재색인
    │
    ├─ 미추출건만 정비
    │       reextract_metadata() 단독 실행
    │       대상: problem/category/tech 2개 이상 빈 문서 + category="추출불가" 포함
    │
    └─ 신규·수정건만 정비
            reextract_new_or_changed()
            대상: document_metadata 없는 문서 (신규)
                  OR doc.updated_at > meta_extracted_at (Confluence 수정 후 미추출)
                  OR category="추출불가" (항상 재시도)

LLM 거부 응답 처리:
    응답에 민감정보/보안/거부 패턴 감지
        → category = "추출불가" 저장
        → 재시도 없음 (content_hash 변경 시에만 다시 시도)
        → 경고 배너에 🚫 추출불가 N건 별도 표시
```

## 8. 자동 현행화 orphan 상태 복구

```
_sync_store (module-level dict): 진행 상태 공유 저장소  (Python 프로세스 재시작 시 초기화됨)

session_state["is_sync_running"] = True 유지 상태에서 프로세스 재시작 시:
    _sync_watcher 호출 → _sync_store.get(session_id) == None
    → is_sync_running = False 즉시 클리어 (orphan 해제)
    → st.rerun()
```
