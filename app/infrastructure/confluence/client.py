from dataclasses import dataclass
from typing import Optional
import httpx
from app.shared.exceptions import ConfluenceError
from app.shared.logger import get_logger

logger = get_logger()


@dataclass
class RemotePageMeta:
    page_id: str
    title: str
    url: str
    author: str
    created_at: str
    updated_at: str
    version: int
    parent_page_id: Optional[str] = None


@dataclass
class RemotePageContent:
    page_id: str
    raw_body: str  # HTML (storage format)


class ConfluenceClient:
    """
    Confluence REST API 클라이언트.

    confluence_type:
      "server"  — 온프레미스 / Data Center
                  API: {base_url}/rest/api/...
                  PAT: Authorization: Bearer <token>  (auth_username 불필요)
                  Basic: Authorization: Basic base64(username:password)

      "cloud"   — atlassian.net
                  API: {base_url}/wiki/rest/api/...
                  Basic: Authorization: Basic base64(email:api_token)
    """

    def __init__(
        self,
        base_url: str,
        auth_token: str,
        auth_username: str = "",
        confluence_type: str = "server",
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.confluence_type = confluence_type

        # API 경로 prefix
        if confluence_type == "cloud":
            self._api_root = f"{self.base_url}/wiki/rest/api"
        else:
            self._api_root = f"{self.base_url}/rest/api"

        # 인증 헤더 설정
        if auth_username:
            # Basic Auth (Cloud: email+api_token / Server: username+password)
            auth = (auth_username, auth_token)
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
        else:
            # PAT (Personal Access Token) — Server/DC 전용
            auth = None
            headers = {
                "Authorization": f"Bearer {auth_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

        self._client = httpx.Client(auth=auth, headers=headers, timeout=timeout)

    def _page_url(self, webui: str) -> str:
        """
        Cloud:  webui = "/pages/12345/Title"  → base_url + /wiki + webui
        Server: webui = "/pages/viewpage.action?pageId=12345" → base_url + webui
        """
        if self.confluence_type == "cloud":
            return f"{self.base_url}/wiki{webui}"
        return f"{self.base_url}{webui}"

    def test_connection(self) -> bool:
        try:
            resp = self._client.get(f"{self._api_root}/space", params={"limit": 1})
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"Confluence 연결 테스트 실패: {e}")
            return False

    def get_descendant_pages_meta(self, root_page_id: str) -> list[RemotePageMeta]:
        """root_page_id 하위 전체 페이지 메타 재귀 수집."""
        results: list[RemotePageMeta] = []
        self._collect_children(root_page_id, results)
        logger.info(f"Confluence 메타 수집 완료: {len(results)}건")
        return results

    def _collect_children(self, page_id: str, results: list[RemotePageMeta]) -> None:
        start = 0
        limit = 50
        while True:
            try:
                resp = self._client.get(
                    f"{self._api_root}/content/{page_id}/child/page",
                    params={
                        "expand": "version,history,ancestors",
                        "start": start,
                        "limit": limit,
                    },
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise ConfluenceError(
                    f"페이지 목록 조회 실패 (pageId={page_id}, status={e.response.status_code}): {e}"
                ) from e
            except httpx.RequestError as e:
                raise ConfluenceError(f"Confluence 연결 오류: {e}") from e

            data = resp.json()
            pages = data.get("results", [])

            for page in pages:
                try:
                    webui = page.get("_links", {}).get("webui", "")
                    meta = RemotePageMeta(
                        page_id=page["id"],
                        title=page["title"],
                        url=self._page_url(webui),
                        author=page["history"]["createdBy"]["displayName"],
                        created_at=page["history"]["createdDate"],
                        updated_at=page["version"]["when"],
                        version=page["version"]["number"],
                        parent_page_id=page_id,
                    )
                    results.append(meta)
                    self._collect_children(page["id"], results)  # 재귀
                except (KeyError, TypeError) as e:
                    logger.warning(f"페이지 메타 파싱 오류 (id={page.get('id')}): {e}")

            if len(pages) < limit:
                break
            start += limit

    def get_page_content(self, page_id: str) -> RemotePageContent:
        try:
            resp = self._client.get(
                f"{self._api_root}/content/{page_id}",
                params={"expand": "body.storage"},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ConfluenceError(
                f"페이지 본문 조회 실패 (pageId={page_id}, status={e.response.status_code}): {e}"
            ) from e
        except httpx.RequestError as e:
            raise ConfluenceError(f"Confluence 연결 오류: {e}") from e

        data = resp.json()
        raw_body = data.get("body", {}).get("storage", {}).get("value", "")
        return RemotePageContent(page_id=page_id, raw_body=raw_body)

    def close(self) -> None:
        self._client.close()
