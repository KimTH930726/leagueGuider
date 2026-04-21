import re
from bs4 import BeautifulSoup


class HTMLParser:
    """Confluence HTML body → plain text 변환"""

    def to_text(self, html: str) -> str:
        if not html:
            return ""
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        # 코드 블록은 내용 유지 (```로 감싸기)
        for code_block in soup.find_all(["code", "pre"]):
            code_block.replace_with(f"\n```\n{code_block.get_text()}\n```\n")

        # 불필요한 태그 제거
        for tag in soup.find_all(["script", "style", "ac:structured-macro"]):
            tag.decompose()

        text = soup.get_text(separator="\n")
        text = self._clean(text)
        return text

    def _clean(self, text: str) -> str:
        # 연속 공백 제거
        text = re.sub(r"[ \t]+", " ", text)
        # 3줄 이상 연속 빈 줄 → 2줄로
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
