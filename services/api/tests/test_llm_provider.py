import json

import httpx
from fastapi.testclient import TestClient

from app.llm.openai_compatible import OpenAICompatibleClient
from app.main import create_app


def test_openai_compatible_status_does_not_expose_api_key():
    client = OpenAICompatibleClient(
        provider="qwen",
        api_key=None,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus",
    )

    status = client.status()

    assert status == {
        "provider": "qwen",
        "configured": False,
        "api_key_required": True,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    }
    assert "api_key" not in status


def test_openai_compatible_chat_normalizes_chat_completions_url_and_parses_response():
    captured_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        captured_payloads.append(payload)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "provider summary"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            },
            request=request,
        )

    client = OpenAICompatibleClient(
        provider="qwen",
        api_key="test-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1/",
        model="qwen-plus",
        transport=httpx.MockTransport(handler),
    )

    result = client.chat(
        messages=[{"role": "user", "content": "Summarize source inventory."}],
        max_tokens=128,
        temperature=0.2,
    )

    assert captured_payloads[0]["model"] == "qwen-plus"
    assert captured_payloads[0]["stream"] is False
    assert "thinking" not in captured_payloads[0]
    assert result["provider"] == "qwen"
    assert result["text"] == "provider summary"
    assert result["usage"]["total_tokens"] == 13


def test_openai_compatible_vllm_can_run_without_api_key_when_allowed():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://127.0.0.1:8001/v1/chat/completions"
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "local ok"}}], "usage": {}},
            request=request,
        )

    client = OpenAICompatibleClient(
        provider="vllm",
        api_key=None,
        api_key_required=False,
        base_url="http://127.0.0.1:8001/v1",
        model="local-model",
        transport=httpx.MockTransport(handler),
    )

    assert client.status()["configured"] is True
    assert client.chat(messages=[{"role": "user", "content": "ping"}])["text"] == "local ok"


def test_llm_provider_route_reports_safe_status():
    response = TestClient(create_app()).get("/llm/provider")

    assert response.status_code == 200
    body = response.json()
    assert "provider" in body
    assert "base_url" in body
    assert "model" in body
    assert "api_key" not in body
