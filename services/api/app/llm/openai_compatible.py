from urllib.parse import urljoin

import httpx


class OpenAICompatibleClient:
    """OpenAI-compatible chat adapter，支持 DeepSeek、Qwen、vLLM 等兼容端点。"""

    def __init__(
        self,
        provider: str,
        api_key: str | None,
        base_url: str,
        model: str,
        api_key_required: bool = True,
        timeout_seconds: float = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_required = api_key_required
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def status(self) -> dict:
        """返回可展示的 provider 状态，永远不包含 API key。"""

        return {
            "provider": self.provider,
            "configured": self.is_configured(),
            "api_key_required": self.api_key_required,
            "base_url": self.base_url,
            "model": self.model,
        }

    def is_configured(self) -> bool:
        has_endpoint = bool(self.base_url and self.model)
        has_required_key = bool(self.api_key) or not self.api_key_required
        return has_endpoint and has_required_key

    def chat(
        self,
        messages: list[dict],
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> dict:
        if not self.is_configured():
            raise ValueError("LLM provider is not configured")

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            response = client.post(
                urljoin(f"{self.base_url}/", "chat/completions"),
                headers=headers,
                json=payload,
            )
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") or []
        text = ""
        if choices:
            text = choices[0].get("message", {}).get("content") or ""
        return {
            "provider": self.provider,
            "model": self.model,
            "text": text,
            "usage": body.get("usage", {}),
        }
