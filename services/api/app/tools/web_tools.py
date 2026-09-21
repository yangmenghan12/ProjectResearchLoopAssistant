from html.parser import HTMLParser

import httpx


class ReadableHTMLParser(HTMLParser):
    """提取网页标题和正文文本，忽略 script/style 这类不可作为证据的内容。"""

    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self._in_title = False
        self._ignored_depth = 0
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "title":
            self._in_title = True
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            self.title = " ".join(part.strip() for part in self._title_parts if part.strip()) or None
        if tag in {"script", "style", "noscript"} and self._ignored_depth > 0:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if not stripped:
            return
        if self._in_title:
            self._title_parts.append(stripped)
            return
        if self._ignored_depth == 0:
            self._text_parts.append(stripped)

    @property
    def text(self) -> str:
        return " ".join(self._text_parts)


def fetch_web_url(
    url: str,
    timeout_seconds: float,
    excerpt_chars: int,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """读取公开网页并提取短摘要；不执行页面脚本。"""

    with httpx.Client(
        follow_redirects=True,
        timeout=timeout_seconds,
        transport=transport,
    ) as client:
        response = client.get(url)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    title: str | None = None
    if "html" in content_type.lower():
        parser = ReadableHTMLParser()
        parser.feed(response.text)
        title = parser.title
        text = parser.text
    else:
        text = response.text

    return {
        "status": "collected",
        "title": title,
        "content_excerpt": text[:excerpt_chars],
        "metadata": {
            "content_type": content_type,
            "final_url": str(response.url),
            "status_code": response.status_code,
            "excerpt_chars": min(len(text), excerpt_chars),
        },
        "skipped_items": [],
    }
