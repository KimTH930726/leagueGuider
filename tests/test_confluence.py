"""
Confluence 연결 및 하위 페이지 조회 단독 테스트 스크립트.

실행:
  cd D:\personalPJT\leagueGuider
  .venv\Scripts\activate
  python tests/test_confluence.py

환경변수 또는 직접 값 입력:
  CONFL_URL   : Confluence base URL
  CONFL_PAT   : Personal Access Token
  CONFL_PAGE  : Root Page ID
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── 여기에 직접 입력 (또는 환경변수로 전달) ─────────────────────────
BASE_URL   = os.getenv("CONFL_URL",  "https://confl.sinc.co.kr")
PAT        = os.getenv("CONFL_PAT",  "")           # ← PAT 직접 입력
ROOT_PAGE  = os.getenv("CONFL_PAGE", "495526926")
CONFL_TYPE = "server"
# ───────────────────────────────────────────────────────────────────

def main():
    if not PAT:
        print("[ERROR] PAT가 비어있습니다. 스크립트 상단 PAT= 에 값을 입력하세요.")
        sys.exit(1)

    from app.infrastructure.confluence.client import ConfluenceClient
    from app.infrastructure.confluence.parser import HTMLParser

    client = ConfluenceClient(
        base_url=BASE_URL,
        auth_token=PAT,
        auth_username="",          # PAT는 username 불필요
        confluence_type=CONFL_TYPE,
    )

    print(f"\n{'='*60}")
    print(f"대상: {BASE_URL}")
    print(f"Root Page ID: {ROOT_PAGE}")
    print(f"{'='*60}\n")

    # 1. 연결 테스트
    print("[1] Confluence 연결 테스트...")
    ok = client.test_connection()
    if not ok:
        print("  ✗ 연결 실패. PAT와 URL을 확인하세요.")
        sys.exit(1)
    print("  ✓ 연결 성공\n")

    # 2. 하위 페이지 메타 수집
    print("[2] 하위 페이지 메타데이터 수집 중...")
    pages = client.get_descendant_pages_meta(ROOT_PAGE)
    print(f"  ✓ 수집 완료: {len(pages)}건\n")

    if not pages:
        print("  [경고] 하위 페이지가 없거나 접근 권한이 없습니다.")
        client.close()
        return

    # 3. 페이지 목록 출력
    print(f"{'ID':<15} {'버전':<5} {'수정일':<25} {'제목'}")
    print("-" * 80)
    for p in pages[:20]:   # 최대 20개만 출력
        updated = p.updated_at[:19] if p.updated_at else "-"
        print(f"{p.page_id:<15} {p.version:<5} {updated:<25} {p.title}")
    if len(pages) > 20:
        print(f"  ... 외 {len(pages) - 20}건")

    # 4. 첫 번째 페이지 본문 조회 + 파싱
    print(f"\n[3] 첫 번째 페이지 본문 조회: {pages[0].title}")
    content = client.get_page_content(pages[0].page_id)
    parser = HTMLParser()
    text = parser.to_text(content.raw_body)
    print(f"  ✓ HTML 길이: {len(content.raw_body):,}자 → 파싱 후: {len(text):,}자")
    print(f"\n  --- 본문 미리보기 (500자) ---")
    print(text[:500])
    print("  ---")

    print(f"\n{'='*60}")
    print("테스트 완료.")
    print(f"  총 {len(pages)}개 페이지 수집 가능 확인")
    print(f"  본문 파싱 정상 동작 확인")
    print(f"{'='*60}\n")

    client.close()


if __name__ == "__main__":
    main()
