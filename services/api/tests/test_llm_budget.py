import json
from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import LLMCall, LLMProfile, utc_now
from app.db.session import get_session
from app.llm.budget import estimate_usage_cents
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


def configured_profile(*, daily_budget_cents: int | None) -> LLMProfile:
    return LLMProfile(
        name="Budget verifier",
        task_type="verifier",
        provider="qwen",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus",
        api_key_required=False,
        max_tokens=800,
        temperature=0.1,
        timeout_seconds=30,
        daily_budget_cents=daily_budget_cents,
        enabled=True,
    )


def test_estimate_usage_cents_handles_malformed_usage():
    assert estimate_usage_cents({"total_tokens": "unknown"}) == 0
    assert estimate_usage_cents({"total_tokens": -1}) == 0
    assert estimate_usage_cents(None) == 0
    assert estimate_usage_cents("bad") == 0
    assert estimate_usage_cents({"total_tokens": "1200"}) == 2


def test_daily_budget_zero_blocks_provider_and_records_budget_exceeded_call():
    provider_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal provider_called
        provider_called = True
        return httpx.Response(200, json={"choices": [{"message": {"content": "unexpected"}}]}, request=request)

    with create_test_session() as session:
        session.add(configured_profile(daily_budget_cents=0))
        session.flush()

        result = LLMRouter(session, transport=httpx.MockTransport(handler)).chat(
            task_type="verifier",
            messages=[{"role": "user", "content": "ping"}],
        )

        calls = list(session.scalars(select(LLMCall)).all())

    assert result["status"] == "budget_exceeded"
    assert result["text"] == ""
    assert result["error_message"] == "LLM budget exceeded"
    assert provider_called is False
    assert len(calls) == 1
    assert calls[0].status == "budget_exceeded"
    assert calls[0].error_message == "LLM budget exceeded"
    assert calls[0].usage_json == {"budget": {"used_cents": 0, "requested_cents": 1, "limit_cents": 0}}


def test_required_budget_exceeded_commits_ledger_before_raise():
    TestingSessionLocal = create_test_session_factory()

    with TestingSessionLocal() as session:
        session.add(configured_profile(daily_budget_cents=0))
        session.commit()

        with pytest.raises(ValueError, match="LLM budget exceeded"):
            LLMRouter(session).chat(
                task_type="verifier",
                messages=[{"role": "user", "content": "ping"}],
                required=True,
            )

    with TestingSessionLocal() as session:
        calls = list(session.scalars(select(LLMCall)).all())

    assert len(calls) == 1
    assert calls[0].status == "budget_exceeded"
    assert calls[0].error_message == "LLM budget exceeded"


def test_budget_none_allows_provider_call():
    captured_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "allowed"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
            request=request,
        )

    with create_test_session() as session:
        session.add(configured_profile(daily_budget_cents=None))
        session.flush()

        result = LLMRouter(session, transport=httpx.MockTransport(handler)).chat(
            task_type="verifier",
            messages=[{"role": "user", "content": "ping"}],
        )

    assert result["status"] == "succeeded"
    assert result["text"] == "allowed"
    assert result["usage"]["total_tokens"] == 5
    assert captured_payloads[0]["model"] == "qwen-plus"


def test_malformed_historical_usage_does_not_block_allowed_budget_call():
    provider_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal provider_called
        provider_called = True
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "allowed after malformed history"}}],
                "usage": {"total_tokens": 1000},
            },
            request=request,
        )

    with create_test_session() as session:
        profile = configured_profile(daily_budget_cents=1)
        session.add(profile)
        session.flush()
        record_llm_call(
            session,
            profile_id=profile.id,
            task_type="verifier",
            provider=profile.provider,
            model=profile.model,
            messages=[{"role": "user", "content": "previous"}],
            usage={"total_tokens": "unknown"},
            status="succeeded",
        )

        result = LLMRouter(session, transport=httpx.MockTransport(handler)).chat(
            task_type="verifier",
            messages=[{"role": "user", "content": "ping"}],
        )

    assert result["status"] == "succeeded"
    assert result["text"] == "allowed after malformed history"
    assert provider_called is True


def test_succeeded_calls_in_last_24_hours_count_toward_budget_and_equal_budget_blocks():
    provider_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal provider_called
        provider_called = True
        return httpx.Response(200, json={"choices": [{"message": {"content": "unexpected"}}]}, request=request)

    with create_test_session() as session:
        profile = configured_profile(daily_budget_cents=1)
        session.add(profile)
        session.flush()
        record_llm_call(
            session,
            profile_id=profile.id,
            task_type="verifier",
            provider=profile.provider,
            model=profile.model,
            messages=[{"role": "user", "content": "previous"}],
            usage={"total_tokens": 1},
            status="succeeded",
        )
        old_call = record_llm_call(
            session,
            profile_id=profile.id,
            task_type="verifier",
            provider=profile.provider,
            model=profile.model,
            messages=[{"role": "user", "content": "old"}],
            usage={"total_tokens": 2000},
            status="succeeded",
        )
        old_call.created_at = utc_now() - timedelta(hours=25)
        session.flush()
        record_llm_call(
            session,
            profile_id=profile.id,
            task_type="verifier",
            provider=profile.provider,
            model=profile.model,
            messages=[{"role": "user", "content": "failed"}],
            usage={"total_tokens": 2000},
            status="failed",
        )

        result = LLMRouter(session, transport=httpx.MockTransport(handler)).chat(
            task_type="verifier",
            messages=[{"role": "user", "content": "ping"}],
        )

        budget_call = session.scalars(
            select(LLMCall).where(LLMCall.status == "budget_exceeded")
        ).one()

    assert result["status"] == "budget_exceeded"
    assert provider_called is False
    assert budget_call.usage_json == {"budget": {"used_cents": 1, "requested_cents": 1, "limit_cents": 1}}


def test_current_request_estimate_blocks_call_that_would_exceed_remaining_budget():
    provider_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal provider_called
        provider_called = True
        return httpx.Response(200, json={"choices": [{"message": {"content": "unexpected"}}]}, request=request)

    with create_test_session() as session:
        profile = configured_profile(daily_budget_cents=1)
        profile.max_tokens = 2000
        session.add(profile)
        session.flush()

        result = LLMRouter(session, transport=httpx.MockTransport(handler)).chat(
            task_type="verifier",
            messages=[{"role": "user", "content": "ping"}],
        )

        budget_call = session.scalars(
            select(LLMCall).where(LLMCall.status == "budget_exceeded")
        ).one()

    assert result["status"] == "budget_exceeded"
    assert provider_called is False
    budget = budget_call.usage_json["budget"]
    assert budget["used_cents"] == 0
    assert budget["limit_cents"] == 1
    assert budget["requested_cents"] > budget["limit_cents"]
