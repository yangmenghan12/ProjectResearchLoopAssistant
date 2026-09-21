from collections.abc import Iterator
from contextlib import contextmanager
from importlib import import_module

import pytest
from sqlalchemy import Float, create_engine, text
from sqlalchemy.orm import Session

from app.db.models import AnalysisRun, LLMAnalysisArtifact, LLMCall, LLMProfile, Project
from tests.test_runs_api import build_client


_client = build_client()


@contextmanager
def create_test_session() -> Iterator[Session]:
    override_session = next(iter(_client.app.dependency_overrides.values()))
    session_iter = override_session()
    session = next(session_iter)
    try:
        yield session
    finally:
        session_iter.close()


def test_llm_profiles_calls_and_artifacts_persist():
    assert isinstance(LLMProfile.__table__.c.temperature.type, Float)

    with create_test_session() as session:
        project = Project(name="V3", topic="V3", analysis_goal="Assess V3")
        session.add(project)
        session.flush()
        run = AnalysisRun(project_id=project.id, status="ready_for_review", thread_id="run:v3")
        session.add(run)
        session.flush()
        profile = LLMProfile(
            name="Analyzer Default",
            task_type="analyzer",
            provider="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            api_key_env_name="LLM_API_KEY",
            api_key_required=True,
            max_tokens=1200,
            temperature=0.2,
            timeout_seconds=30,
            daily_budget_cents=500,
            enabled=True,
        )
        session.add(profile)
        session.flush()
        call = LLMCall(
            project_id=project.id,
            run_id=run.id,
            report_id=None,
            profile_id=profile.id,
            task_type="analyzer",
            provider="deepseek",
            model="deepseek-chat",
            prompt_hash="sha256:test",
            input_refs_json=["source:1"],
            output_ref="llm-artifact:test",
            usage_json={"total_tokens": 42},
            latency_ms=123,
            status="succeeded",
            error_message=None,
        )
        session.add(call)
        session.flush()
        artifact = LLMAnalysisArtifact(
            project_id=project.id,
            run_id=run.id,
            call_id=call.id,
            artifact_type="analyzer_output",
            content_json={"findings": []},
            validation_result_json={"passed": True},
            accepted=True,
        )
        session.add(artifact)
        session.commit()
        profile_id = profile.id
        call_id = call.id
        artifact_id = artifact.id

    with create_test_session() as session:
        saved_profile = session.get(LLMProfile, profile_id)
        saved_call = session.get(LLMCall, call_id)
        saved_artifact = session.get(LLMAnalysisArtifact, artifact_id)
        assert saved_profile.task_type == "analyzer"
        assert saved_profile.temperature == 0.2
        assert saved_profile.api_key_env_name == "LLM_API_KEY"
        assert bool(saved_profile.api_key_required) is True
        assert bool(saved_profile.enabled) is True
        assert saved_call.profile_id == saved_profile.id
        assert saved_call.input_refs_json == ["source:1"]
        assert saved_call.usage_json["total_tokens"] == 42
        assert saved_artifact.content_json == {"findings": []}
        assert saved_artifact.validation_result_json == {"passed": True}
        assert bool(saved_artifact.accepted) is True


def test_llm_temperature_normalization_migration_repairs_scaled_values():
    migration = import_module(
        "app.db.migrations.versions.20260701_0008_normalize_llm_profile_temperature"
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE llm_profiles (id TEXT PRIMARY KEY, temperature FLOAT NOT NULL)"))
        connection.execute(
            text(
                "INSERT INTO llm_profiles (id, temperature) VALUES "
                "('scaled', 20), ('normal', 0.2), ('max_valid', 2.0)"
            )
        )

        connection.execute(text(migration.NORMALIZE_SCALED_TEMPERATURE_SQL))
        rows = dict(connection.execute(text("SELECT id, temperature FROM llm_profiles")).all())

    assert rows["scaled"] == pytest.approx(0.2)
    assert rows["normal"] == pytest.approx(0.2)
    assert rows["max_valid"] == pytest.approx(2.0)
