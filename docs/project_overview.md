# LeagueGuider — 프로젝트 개요

## 한줄 소개

Confluence에 쌓인 사내 AI 에이전트 문서를 자동으로 수집·분류하고, 하이브리드 검색과 LLM 리포트로 빠르게 탐색할 수 있는 사내 AI 에이전트 포털.

---

## 배경 및 목적

사내에서 운영되는 AI 에이전트·자동화 사례들이 Confluence에 개별 페이지로 흩어져 있어 "어떤 에이전트가 있는지", "우리 팀 문제에 쓸 수 있는 사례가 있는지"를 빠르게 파악하기 어려운 문제가 있었다. LeagueGuider는 이 페이지들을 주기적으로 동기화하고, 키워드/벡터 검색·자동 메타데이터 추출·LLM 리포트 생성을 통해 지식을 쉽게 탐색할 수 있도록 한다.

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| **자동 동기화** | Confluence 지정 페이지 트리를 Full / Incremental로 수집. 콘텐츠 해시 기반 중복 방지 |
| **메타데이터 자동 추출** | LLM이 에이전트명·한줄요약·문제·해결방법·기술스택·기대효과·카테고리 추출 |
| **하이브리드 검색** | FTS5 키워드 + ChromaDB 벡터 검색 → RRF 병합 → 메타데이터 기반 Rerank |
| **쿼리 확장** | 도메인 동의어 사전(Rule-based) + 선택적 LLM 확장으로 검색 재현율 향상 |
| **대시보드** | 총 문서 수·주/월 신규·기술스택·카테고리·월별 트렌드 통계 시각화 |
| **리포트 생성** | 주간·월간을 리더십 관점 / 실무자 관점 두 가지로 LLM이 자동 생성 |
| **설정 UI** | Confluence·LLM·임베딩 공급자 설정 및 연결 테스트 |
| **데이터 정비** | 전체 정비 / 미추출건만 / 신규·수정건만 — 3가지 모드로 메타데이터 재추출 |
| **민감정보 암호화** | Confluence PAT·API 키를 OS 키체인(Windows Credential Manager / macOS Keychain)에 저장 |

---

## 기술 스택

| 영역 | 선택지 |
|------|--------|
| **UI** | Streamlit |
| **문서 수집** | Confluence REST API (Server / Cloud 지원) |
| **저장소** | SQLite (문서·메타데이터·리포트) + ChromaDB (벡터) |
| **키워드 검색** | SQLite FTS5 |
| **임베딩** | OpenAI `text-embedding-3-small` / Local `paraphrase-multilingual-mpnet-base-v2` |
| **LLM** | OpenAI GPT-4o-mini / 사내 DevX(InHouse) MCP API |
| **HTML 파싱** | BeautifulSoup4 + lxml |

---

## 비기능 요건

- **오프라인 지원**: 로컬 임베딩 모델로 API 키 없이 키워드+벡터 검색 가능
- **LLM 선택적 활성화**: LLM 없이도 동기화·검색 동작 (메타데이터 추출·리포트 제외)
- **민감정보 암호화**: 인증 토큰·API 키는 OS 키체인(keyring)에 암호화 저장. SQLite에는 빈값만 유지, JSON에 미기록
- **단일 프로세스**: Streamlit 단일 서버로 운영, 별도 백엔드 서버 불필요

---

## 프로젝트 구조

```
leagueGuider/
├── main.py                  # Streamlit 앱 진입점
├── requirements.txt
├── config/
│   └── config.json          # 비민감 설정 (bootstrap)
├── data/
│   ├── league_guider.db     # SQLite (문서·메타데이터·설정·리포트)
│   ├── chroma/              # ChromaDB 벡터 인덱스
│   └── models/              # 로컬 임베딩 모델 캐시
├── docs/                    # 프로젝트 문서
└── app/
    ├── shared/              # Config, 예외, 로거, 텍스트 유틸, secret_store(키체인)
    ├── domain/              # 도메인 모델 (dataclass)
    ├── infrastructure/      # Confluence·DB·임베딩·LLM·벡터 구현체
    ├── application/         # 서비스 레이어 (동기화·검색·리포트·대시보드)
    └── ui/                  # Streamlit 탭별 UI (_helpers: 공통 위젯)
```

---

## 설정 우선순위

```
기본값 (AppConfig) → config/config.json → SQLite app_settings (최종)
```

민감 정보(토큰, API 키)는 OS 키체인(keyring)에만 암호화 저장되며 DB·JSON에는 기록되지 않는다.
