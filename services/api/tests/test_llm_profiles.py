from collections.abc import Generator
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import LLMProfile
from app.db.session import get_session
from app.llm.profiles import get_enabled_profile, safe_profile_dict
from app.main import create_app
from app.schemas.llm import LLMProfileUpdate


def build_client_and_session() -> tuple[TestClient, sessionmaker[Session]]:
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
    return TestClient(app), TestingSessionLocal


def test_safe_profile_dict_never_exposes_api_key(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "secret-value")
    profile = LLMProfile(
        name="Verifier",
        task_type="verifier",
        provider="qwen",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus",
        api_key_env_name="QWEN_API_KEY",
        api_key_required=True,
        max_tokens=800,
        temperature=0.1,
        timeout_seconds=30,
        daily_budget_cents=None,
        enabled=True,
    )

    safe = safe_profile_dict(profile)

    assert safe["api_key_env_name"] == "QWEN_API_KEY"
    assert safe["configured"] is True
    assert "api_key" not in safe
    assert "secret-value" not in safe.values()


def test_get_enabled_profile_prefers_enabled_database_profile_for_task_type():
    _, TestingSessionLocal = build_client_and_session()
    with TestingSessionLocal() as session:
        disabled_newer = LLMProfile(
            name="Disabled verifier",
            task_type="verifier",
            provider="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            api_key_required=False,
            max_tokens=800,
            temperature=0.2,
            timeout_seconds=30,
            enabled=False,
            updated_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        qwen_verifier = LLMProfile(
            name="Qwen verifier",
            task_type="verifier",
            provider="qwen",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen-plus",
            api_key_env_name="QWEN_API_KEY",
            api_key_required=True,
            max_tokens=800,
            temperature=0.1,
            timeout_seconds=30,
            enabled=True,
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        analyzer_profile = LLMProfile(
            name="Analyzer",
            task_type="analyzer",
            provider="openai-compatible",
            base_url="https://example.test/v1",
            model="analyzer-model",
            api_key_required=False,
            max_tokens=1200,
            temperature=0.2,
            timeout_seconds=30,
            enabled=True,
        )
        session.add_all([disabled_newer, qwen_verifier, analyzer_profile])
        session.flush()

        profile = get_enabled_profile(session, "verifier")

        assert profile is not None
        assert profile.provider == "qwen"
        assert profile.task_type == "verifier"
        assert profile.temperature == 0.1


def test_list_llm_profiles_returns_safe_list():
    client, TestingSessionLocal = build_client_and_session()
    with TestingSessionLocal() as session:
        session.add(
            LLMProfile(
                name="Verifier",
                task_type="verifier",
                provider="qwen",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen-plus",
                api_key_env_name="QWEN_API_KEY",
                api_key_required=True,
                max_tokens=800,
                temperature=0.1,
                timeout_seconds=30,
                enabled=True,
            )
        )
        session.commit()

    response = client.get("/llm/profiles")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert body
    assert all("api_key" not in item for item in body)


def test_profile_update_schema_does_not_allow_task_type_patch():
    assert "task_type" not in LLMProfileUpdate.model_fields


def test_create_profile_rejects_invalid_api_key_env_name():
    client, _ = build_client_and_session()

    response = client.post(
        "/llm/profiles",
        json={
            "name": "Suspicious",
            "task_type": "verifier",
            "provider": "qwen",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-plus",
            "api_key_env_name": "not-a-valid-env-var",
        },
    )

    assert response.status_code == 422


def test_create_profile_trims_valid_api_key_env_name():
    client, _ = build_client_and_session()

    response = client.post(
        "/llm/profiles",
        json={
            "name": "Verifier",
            "task_type": "verifier",
            "provider": "qwen",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-plus",
            "api_key_env_name": "  QWEN_API_KEY  ",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["api_key_env_name"] == "QWEN_API_KEY"
    assert "api_key" not in body


def test_update_profile_rejects_invalid_api_key_env_name():
    client, TestingSessionLocal = build_client_and_session()
    with TestingSessionLocal() as session:
        profile = LLMProfile(
            name="Verifier",
            task_type="verifier",
            provider="qwen",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen-plus",
            api_key_env_name="QWEN_API_KEY",
            api_key_required=True,
            max_tokens=800,
            temperature=0.1,
            timeout_seconds=30,
            enabled=True,
        )
        session.add(profile)
        session.commit()
        profile_id = profile.id

    response = client.patch(
        f"/llm/profiles/{profile_id}",
        json={"api_key_env_name": "not-a-valid-env-var"},
    )

    assert response.status_code == 422
