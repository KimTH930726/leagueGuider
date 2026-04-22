# AI리그 로컬 탐색기 (LeagueGuider)

> 사내 AI 에이전트 포털 — Confluence에 흩어진 AI 에이전트 정보를 자동 수집·분류·검색·리포트로 한눈에

---

## 목차

1. [사용자 가이드 — 이런 게 됩니다](#1-사용자-가이드--이런-게-됩니다)
2. [기술 개요 — 어떻게 동작하나요](#2-기술-개요--어떻게-동작하나요)
3. [설치 및 사용법](#3-설치-및-사용법)

---

## 1. 사용자 가이드 — 이런 게 됩니다

### 🖥️ 거창한 설치 없이 바로 시작

설치파일(.exe) 실행 → API Key 등록 → 끝입니다.  
별도 서버, 도커, Python 설치 없이 **Windows PC 하나면 충분**합니다.  
로컬에서 돌아가는 프로그램이라 인터넷 연결만 되면 사내 어디서든 사용 가능합니다.

---

### 📊 대시보드 — 사내 AI 현황을 한눈에

대시보드를 열면 사내 AI 에이전트 전체 현황이 바로 보입니다.

- **전체 에이전트 수 / 이번 주 신규 / 이번 달 신규·수정** 건수를 숫자로 바로 확인
- **추천 에이전트 Top 3** — 메타데이터가 풍부하고 최근에 등록된 에이전트를 자동으로 선별. 기술스택·기대효과 배지가 카드에 바로 표시됨
- **카테고리 분포 차트** — 어떤 유형의 에이전트가 많은지 바 차트로 시각화
- **기술스택 Top 5 / 기대효과 Top 5** — 사내에서 가장 많이 쓰이는 기술과 기대효과를 한눈에
- **주요 키워드 Top 10 / 월별 등록 추이** — 에이전트 등록 트렌드를 월 단위로 추적
- **데이터 품질 지표** — 메타데이터 추출 완료 비율, 기술스택 보유율, 카테고리 분류율
- **전주 대비 카테고리 변화** — 이번 주 신규 등록이 전주 대비 얼마나 늘었는지 증감 표시
- **동기화 이력** — 언제 현행화가 됐는지, 몇 건 추가·수정·삭제됐는지 이력 확인
- **현행화하기 버튼** — 대시보드에서 바로 Confluence 변경분 동기화 실행 가능

---

### 🔍 검색 — 원하는 에이전트를 빠르게

자연어로 검색하면 Confluence에 등록된 AI 에이전트를 찾아줍니다.  
단순 키워드 매칭이 아니라 **의미 기반 검색**이라 유사한 표현도 찾아냅니다.

**검색 결과 카드에는:**
- 에이전트명 + Confluence 원문 링크
- 한 줄 요약 (LLM이 자동 추출)
- 기술스택 배지 (React, Python, LangChain 등)
- 기대효과 배지 (업무 자동화, 비용 절감 등)
- 작성자 / 최종 수정일

**검색 모드 3가지:**
- **하이브리드** (기본) — 키워드 + 벡터 검색을 합쳐서 더 정확한 결과
- **키워드** — 문서 본문에서 정확한 단어 매칭
- **벡터** — 의미 기반 유사도 검색 (비슷한 맥락의 문서 탐색)

**필터 옵션:**
- 기간 필터 (등록일 기준)
- 기술스택 / 기대효과 필터
- LLM 쿼리 확장 옵션 (ON 시 동의어·연관어 자동 확장)

---

### 📄 리포트 — 사내 AI 흐름을 주간·월간으로

AI 에이전트 현황을 **LLM이 자동으로 분석해 리포트**를 작성해줍니다.  
회의 전 5분 안에 AI 현황 브리핑 자료가 생깁니다.

**리포트 종류:**
- **주간 × 리더십 관점** — 이번 주 등록된 에이전트의 비즈니스 가치, 트렌드 요약
- **주간 × 실무자 관점** — 기술스택, 구현 방식, 적용 가능성 중심
- **월간 × 리더십 관점** — 한 달간 AI 도입 현황, 카테고리별 성과
- **월간 × 실무자 관점** — 기술 동향, 재사용 가능한 패턴 분석

**주요 기능:**
- 기간 셀렉트박스에 **[작성완료]** 표시 — 이미 작성된 기간을 한눈에 파악
- **3기간 추이 비교** — 전전기 → 전기 → 현재를 연속으로 분석해 트렌드 파악
- 기존 리포트 **즉시 조회** / **재생성** 분리 — 빠른 확인과 최신 재생성 모두 지원
- 리포트 목록에서 선택한 기간 자동 강조·펼침

---

### ⚙️ 설정 — 한 번만 하면 됩니다

**Confluence 연결:**
- Confluence URL + 개인 PAT(Personal Access Token) 등록
- 연결 테스트 버튼으로 즉시 검증

**LLM 연결:**
- OpenAI GPT-4o-mini 또는 사내 InHouse LLM 선택
- API Key는 **Windows Credential Manager(OS 키체인)에 암호화 저장** — 앱 재실행 시 자동 복원

**고급 — 데이터 정비:**
- **전체 정비 (재추출 + 재색인)** — 메타데이터 재추출 + 벡터 인덱스 전체 재구축
- **미추출·추출불가건 정비** — 품질이 낮거나 이전에 실패한 문서만 선별해서 LLM 재추출
- **신규·수정건만 정비** — Confluence에서 수정됐지만 메타데이터가 아직 갱신 안 된 문서만 처리
- 임베딩 모델 변경 시 재색인 필요 경고 자동 표시

---

### 🔄 현행화 — 자동 + 수동 모두 지원

- **앱 실행 시 자동 현행화** — 실행할 때마다 Confluence 변경분(신규·수정·삭제)을 백그라운드에서 자동 동기화
- **수동 현행화** — 대시보드의 "현행화하기" 버튼으로 즉시 실행
- content_hash 비교로 **실제로 내용이 바뀐 문서만** 처리 (불필요한 재처리 없음)
- 삭제된 Confluence 페이지는 소프트 삭제 처리 (검색에서 제외, 데이터는 보존)
- 동기화 중에도 검색·대시보드는 정상 사용 가능 (비동기 처리)

---

## 2. 기술 개요 — 어떻게 동작하나요

### 전체 아키텍처

```
Confluence
    │  REST API (PAT 인증)
    ▼
[수집 레이어]  ConfluenceClient
    │  HTML → 텍스트 파싱 (BeautifulSoup)
    ▼
[저장 레이어]
    ├── SQLite (문서 본문 + FTS5 키워드 인덱스 + 메타데이터 + 설정)
    └── ChromaDB (벡터 임베딩 인덱스, 로컬 파일)
    │
    ▼
[처리 레이어]
    ├── LLM 메타데이터 추출 (GPT-4o-mini / InHouse MCP API)
    └── 임베딩 생성 (OpenAI text-embedding-3-small / Local sentence-transformers)
    │
    ▼
[서비스 레이어]
    ├── SearchService  — 하이브리드 검색 + RRF + Rerank
    ├── ReportService  — 주간/월간 AI 리포트 생성
    └── SyncService    — 증분 동기화 오케스트레이션
    │
    ▼
[UI 레이어]  Streamlit (멀티탭, fragment 비동기 polling)
    │
    ▼
[배포]  PyInstaller onedir EXE + Windows Credential Manager
```

---

### 기술 스택

| 영역 | 기술 |
|---|---|
| UI | Streamlit (멀티탭, `@st.fragment` 비동기 polling) |
| 관계형 DB | SQLite + FTS5 가상 테이블 (전문 검색) |
| 벡터 DB | ChromaDB (로컬 HNSW 인덱스) |
| 임베딩 | OpenAI `text-embedding-3-small` / `sentence-transformers` (오프라인) |
| LLM | OpenAI GPT-4o-mini / 사내 InHouse MCP API |
| Confluence 수집 | httpx + BeautifulSoup |
| 암호화 | keyring (Windows Credential Manager / macOS Keychain) |
| 배포 | PyInstaller onedir EXE |

---

### Confluence 수집 흐름

```
1. get_descendant_pages_meta()
   → Confluence REST API로 루트 페이지 하위 전체 페이지 메타 일괄 조회
   → {page_id: title, version, updated_at, url, author}

2. 증분 비교
   → remote vs local (version / updated_at / content_hash) 3단 비교
   → 신규 / 변경 / 삭제 분류

3. get_page_content(page_id)
   → 변경된 페이지만 본문 조회 (불필요한 API 호출 최소화)
   → HTML body → BeautifulSoup으로 태그 제거 → 정제된 텍스트

4. content_hash (SHA-256) 비교
   → 버전은 올라갔지만 실제 내용 동일한 경우 처리 스킵
```

---

### 청킹 전략

800자 청크, 150자 오버랩 기준으로 **헤딩 인식 우선 청킹**을 사용합니다.

```
문서 구조 감지
    │
    ├── 마크다운/숫자 헤딩이 2개 이상
    │       → 섹션 단위 청킹 (헤딩 경계 우선 분할)
    │         800자 초과 섹션은 문단 기반으로 재분할
    │
    └── 헤딩 없는 문서
            → \n\n 문단 기반 청킹 (150자 overlap)
```

각 청크는 SQLite `document_chunks` 테이블에 저장되고, 동시에 ChromaDB에 벡터로 임베딩됩니다.

---

### LLM 메타데이터 자동 추출

동기화 시 LLM을 호출해 각 문서에서 아래 필드를 자동 추출합니다.

| 필드 | 설명 |
|---|---|
| `agent_name` | 에이전트 공식 명칭 |
| `one_line_summary` | 한 줄 요약 (없으면 문서 제목으로 자동 보완) |
| `problem` | 해결하는 문제 |
| `solution` | 해결 방법 |
| `tech_stack` | 사용 기술 목록 (JSON 배열) |
| `effects` | 기대효과 목록 (JSON 배열) |
| `keywords` | 핵심 키워드 목록 (JSON 배열) |
| `category` | 에이전트 분류 카테고리 |

**추출 로직:**
- JSON 파싱 실패 시 최대 2회 재시도
- LLM이 민감정보 등으로 거부 응답 시 `category="추출불가"` 저장
- 기술스택·문제·카테고리 중 2개 이상 비어있으면 재추출 대상으로 분류
- 수동 정비 시 `추출불가` 문서도 항상 재시도

---

### 하이브리드 검색 파이프라인

```
사용자 쿼리
    │
    ▼
[1] Query Rewrite
    ├── Rule-based 동의어 확장 (항상 적용)
    └── LLM 쿼리 확장 (옵션, ON 시 연관어 추가 생성)

    ▼
[2] 병렬 검색
    ├── SQLite FTS5 키워드 검색 (원문 쿼리)
    ├── SQLite FTS5 키워드 검색 (확장어)
    └── ChromaDB 벡터 검색 (코사인 유사도)
         → 동적 threshold: 결과 <5건이면 0.01, ≥5건이면 max_score × 0.5

    ▼
[3] RRF (Reciprocal Rank Fusion) 병합
    ├── 원문 키워드 결과: weight = 1.0
    ├── 확장어 키워드 결과: weight = 0.5
    └── 벡터 결과: weight = 1.0

    ▼
[4] Heuristic Rerank
    → title / summary / problem / tech_stack / category 매칭 가중치 적용
    → 최종 상위 10건 반환
```

---

### 데이터 정비 3종

| 정비 유형 | 대상 | 재색인 여부 |
|---|---|---|
| **전체 정비** | 품질 낮은 전체 문서 LLM 재추출 | ✅ 전체 벡터 재색인 |
| **미추출·추출불가건 정비** | tech_stack 없음 / problem 없음 / category 추출불가 문서 | ❌ 재색인 없음 |
| **신규·수정건만 정비** | 메타 없는 문서 + `updated_at > meta_extracted_at` 문서 | ❌ 재색인 없음 |

---

### AI 리포트 생성 로직

```
1. 기간 계산
   → 현재 기간 / 전기 / 전전기 3기간 문서 조회

2. 문서 선별
   → doc_richness_score 기반 상위 25건 선별
     (one_line_summary +3 / problem +2 / tech_stack +1 / effects +1 / keywords +1)
   → 할루시네이션 최소화를 위해 메타 풍부한 문서 우선

3. 컨텍스트 구성
   → 전기 리포트 앞 600자를 연속성 컨텍스트로 삽입
   → 3기간 카테고리 비교표 / 기술스택 추이표 자동 생성

4. LLM 호출
   → 리더십/실무자 관점별 전용 프롬프트

5. 후처리
   → <br> 태그 제거
   → 누락된 Confluence URL 자동 주입
   → SQLite 저장 (period_key 기준 upsert)
```

---

### 보안 설계

| 항목 | 방식 |
|---|---|
| PAT / API Key 저장 | OS 키체인 암호화 저장 (Windows Credential Manager) |
| SQLite 저장 | 민감정보 빈값 — 키체인에만 보관 |
| 평문 마이그레이션 | 구버전 DB 평문값 → 키체인 자동 이전 후 DB 클리어 |
| 네트워크 노출 | localhost 바인딩만 — 외부 접근 불가 |
| API 통신 | 전 구간 HTTPS |

---

### 동시성 처리

- **동기화 중 다중 세션 충돌 방지** — `_global_sync_lock` (프로세스 레벨 Lock)
- **검색 비동기 실행** — `ThreadPoolExecutor` + `@st.fragment(run_every=2)` polling
- **리포트 생성 비동기** — `ThreadPoolExecutor(max_workers=2)` + Future 완료 감지
- **Sync와 Search 임베더 분리** — 동시 `encode()` 호출 시 CPU 경합 방지

---

## 3. 설치 및 사용법

### 설치

1. 설치파일(`AI리그로컬탐색기_설치파일_vX.X.X.exe`) 실행
2. 다음 → 다음 → 완료
3. 바탕화면 **"AI리그 로컬 탐색기"** 아이콘 더블클릭
4. 터미널 창이 뜨고 잠시 후 브라우저가 자동으로 열림
   > ⚠️ **터미널 창을 닫으면 앱이 종료됩니다**

---

### 초기 설정

앱 첫 실행 후 **⚙️ 설정** 탭에서 아래 순서로 설정합니다.

#### 1단계 — Confluence 연결

| 항목 | 설명 |
|---|---|
| Confluence URL | `https://your-confluence.example.com` |
| Confluence 유형 | Server / Cloud 선택 |
| 인증 방식 | Token (PAT) 선택 |
| PAT | Confluence → 프로필 → Personal Access Token 발급 |

> **PAT 발급 방법**: Confluence 우상단 프로필 → 설정 → Personal Access Tokens → 토큰 생성

연결 테스트 버튼으로 성공 확인 후 저장.

#### 2단계 — LLM 연결

**OpenAI 사용 시:**
| 항목 | 값 |
|---|---|
| LLM Provider | openai |
| 모델 | gpt-4o-mini |
| API Key | OpenAI 대시보드에서 발급 |

**사내 InHouse LLM 사용 시:**
| 항목 | 값 |
|---|---|
| LLM Provider | inhouse |
| InHouse LLM URL | 사내 MCP API 엔드포인트 |
| API Key / Usecase ID / Project ID | 사내 발급값 입력 |

#### 3단계 — 임베딩 설정

| 옵션 | 설명 |
|---|---|
| OpenAI | `text-embedding-3-small` 사용, API Key 필요 |
| Local | `paraphrase-multilingual-mpnet-base-v2` 오프라인 사용, API Key 불필요 |

> ⚠️ 임베딩 모델 변경 시 **전체 재색인** 필요 (설정 저장 시 자동 안내)

---

### 첫 현행화

설정 완료 후 **대시보드 → 현행화하기** 클릭.  
Confluence 전체 페이지를 수집하고 LLM으로 메타데이터를 자동 추출합니다.  
문서 수에 따라 수분~수십 분 소요됩니다.

---

### 이후 사용

- **앱 실행 시마다 자동 현행화** — 변경된 문서만 처리하므로 빠름
- 검색 탭에서 자연어로 에이전트 검색
- 리포트 탭에서 주간/월간 리포트 생성·조회
- 메타데이터가 부족한 문서는 **설정 → 고급 → 데이터 정비**로 재추출

---

### 데이터 저장 위치

```
설치 경로\data\
    ├── league_guider.db       ← SQLite (문서, 메타데이터, 설정, 리포트)
    └── chroma\                ← ChromaDB 벡터 인덱스
```

> 앱 제거 시 `data\` 폴더는 보존됩니다. 재설치해도 기존 데이터 유지.

---

### 자주 묻는 질문

**Q. 브라우저를 닫으면 앱도 종료되나요?**  
A. 아닙니다. 터미널 창(검은 콘솔)을 닫아야 완전히 종료됩니다.

**Q. PC를 바꿨는데 설정을 다시 해야 하나요?**  
A. 네. API Key는 OS 키체인에 저장되므로 새 PC에서 다시 입력해야 합니다.

**Q. LLM 없이도 사용할 수 있나요?**  
A. 가능합니다. 문서 수집·검색·대시보드 기본 지표는 LLM 없이도 동작합니다.  
단, 메타데이터 추출(기술스택·카테고리 등)과 AI 리포트는 LLM 필요합니다.

**Q. 임베딩 모델을 바꿨는데 검색이 이상해요.**  
A. 설정 → 고급 → 전체 정비(재추출 + 재색인)를 실행하세요.

**Q. 포트 충돌이 나요.**  
A. 8501~8521 범위에서 자동으로 빈 포트를 찾아 실행합니다. 별도 설정 불필요합니다.
