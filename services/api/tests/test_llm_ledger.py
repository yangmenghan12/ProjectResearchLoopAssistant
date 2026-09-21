import json
from collections.abc import Generator
from contextlib import contextmanager

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import LLMCall, LLMProfile
from app.db.session import get_session
from app.llm.ledger import record_llm_call
from app.llm.router import LLMRouter
from app.main import create_app


def create_test_session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    app = create_app()

    def override_session() -> Generator[Session, None, None]:
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    return TestingSessionLocal


@contextmanager
def create_test_session() -> Generator[Session, None, None]:
    TestingSessionLocal = create_test_session_factory()
    with TestingSessionLocal() as session:
        yield session


def test_llm_router_chat_records_successful_call():
    captured_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            },
            request=request,
        )

    with create_test_session() as session:
        session.add(
            LLMProfile(
                name="Verifier",
                task_type="verifier",
                provider="qwen",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen-plus",
                api_key_required=False,
                max_tokens=800,
                temperature=0.1,
                timeout_seconds=30,
                enabled=True,
            )
        )
        session.flush()
        router = LLMRouter(session, transport=httpx.MockTransport(handler))

        result = router.chat(
            task_type="verifier",
            messages=[{"role": "user", "content": "请返回 JSON。"}],
            input_refs=["report:draft"],
            required=True,
        )

        calls = list(session.scalars(select(LLMCall)).all())

    assert result["status"] == "succeeded"
    assert result["text"] == '{"ok": true}'
    assert result["usage"]["total_tokens"] == 8
    assert result["call_id"] == calls[0].id
    assert captured_payloads[0]["temperature"] == 0.1
    assert len(calls) == 1
    assert calls[0].status == "succeeded"
    assert calls[0].usage_json["total_tokens"] == 8
    assert calls[0].prompt_hash.startswith("sha256:")


def test_llm_router_records_skipped_call_for_unconfigured_profile(monkeypatch):
    monkeypatch.delenv("MISSING_TEST_LLM_KEY", raising=False)

    with create_test_session() as session:
        session.add(
            LLMProfile(
                name="Unconfigured verifier",
                task_type="verifier",
                provider="qwen",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen-plus",
                api_key_env_name="MISSING_TEST_LLM_KEY",
                api_key_required=True,
                max_tokens=800,
                temperature=0.1,
                timeout_seconds=30,
                enabled=True,
            )
        )
        session.flush()
        router = LLMRouter(session)

        result = router.chat(
            task_type="verifier",
            messages=[{"role": "user", "content": "ping"}],
        )

        calls = list(session.scalars(select(LLMCall)).all())

    assert result["status"] == "skipped"
    assert result["text"] == ""
    assert result["usage"] == {}
    assert len(calls) == 1
    assert calls[0].status == "skipped"
    assert calls[0].error_message == "LLM profile is not configured"


def test_record_llm_call_uses_plan_parameter_names():
    with create_test_session() as session:
        call = record_llm_call(
            session,
            task_type="verifier",
            provider="qwen",
            model="qwen-plus",
            messages=[{"role": "user", "content": "ping"}],
            input_refs=["report:draft"],
            usage={"total_tokens": 8},
            status="succeeded",
        )

        assert call.input_refs_json == ["report:draft"]
        assert call.usage_json == {"total_tokens": 8}


def test_settings_fallback_uses_settings_api_key_without_env_var(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    settings = Settings(
        llm_provider="qwen",
        llm_api_key="settings-secret",
        llm_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        llm_model="qwen-plus",
        llm_api_key_required=True,
    )
    monkeypatch.setattr("app.llm.router.get_settings", lambda: settings)
    authorization_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        authorization_headers.append(request.headers.get("authorization"))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "settings ok"}}], "usage": {}},
            request=request,
        )

    with create_test_session() as session:
        result = LLMRouter(session, transport=httpx.MockTransport(handler)).chat(
            task_type="verifier",
            messages=[{"role": "user", "content": "ping"}],
            required=True,
        )

    assert result["status"] == "succeeded"
    assert result["text"] == "settings ok"
    assert authorization_headers == ["Bearer settings-secret"]


def test_required_unconfigured_call_persists_skipped_ledger_before_raise(monkeypatch):
    monkeypatch.delenv("MISSING_TEST_LLM_KEY", raising=False)
    TestingSessionLocal = create_test_session_factory()

    with TestingSessionLocal() as session:
        session.add(
            LLMProfile(
                name="Unconfigured verifier",
                task_type="verifier",
                provider="qwen",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen-plus",
                api_key_env_name="MISSING_TEST_LLM_KEY",
                api_key_required=True,
                max_tokens=800,
                temperature=0.1,
                timeout_seconds=30,
                enabled=True,
            )
        )
        session.commit()

        with pytest.raises(ValueError, match="LLM profile is not configured"):
            LLMRouter(session).chat(
                task_type="verifier",
                messages=[{"role": "user", "content": "ping"}],
                required=True,
            )

    with TestingSessionLocal() as session:
        calls = list(session.scalars(select(LLMCall)).all())

    assert len(calls) == 1
    assert calls[0].status == "skipped"
    assert calls[0].error_message == "LLM profile is not configured"


def test_required_provider_error_persists_failed_ledger_before_raise():
    TestingSessionLocal = create_test_session_factory()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "provider down"}, request=request)

    with TestingSessionLocal() as session:
        session.add(
            LLMProfile(
                name="Verifier",
                task_type="verifier",
                provider="qwen",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen-plus",
                api_key_required=False,
                max_tokens=800,
                temperature=0.1,
                timeout_seconds=30,
                enabled=True,
            )
        )
        session.commit()

        with pytest.raises(httpx.HTTPStatusError):
            LLMRouter(session, transport=httpx.MockTransport(handler)).chat(
                task_type="verifier",
                messages=[{"role": "user", "content": "ping"}],
                required=True,
            )

    with TestingSessionLocal() as session:
        calls = list(session.scalars(select(LLMCall)).all())

    assert len(calls) == 1
    assert calls[0].status == "failed"
    assert "500 Internal Server Error" in calls[0].error_message
