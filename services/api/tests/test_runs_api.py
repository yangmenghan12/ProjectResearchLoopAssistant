from collections.abc import Generator
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_session
from app.engine.runner import NODE_ORDER
from app.main import create_app
from app.workers.run_worker import run_one_job

EXPECTED_NODE_ORDER = [
    "load_state",
    "build_context",
    "analyze_sources",
    "llm_analyze_sources",
    "update_analysis",
    "verify_package",
    "llm_verify_package",
    "decide_next_state",
    "persist_state",
]


def build_client() -> TestClient:
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
    return TestClient(app)


def create_collected_project(client: TestClient, tmp_path: Path, analysis_goal: str | None = "Assess project") -> dict:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("# Repo", encoding="utf-8")
    (repo_dir / "pyproject.toml").write_text("[project]\nname='repo'", encoding="utf-8")
    (repo_dir / "app.py").write_text("print('entry')", encoding="utf-8")

    project_response = client.post(
        "/projects",
        json={
            "name": "Run project",
            "topic": "Analyze collected project",
            "analysis_goal": analysis_goal,
            "max_turns": 6,
        },
    )
    assert project_response.status_code == 201
    project = project_response.json()
    source_response = client.post(
        f"/projects/{project['id']}/sources",
        json={"source_type": "local_directory", "uri": str(repo_dir)},
    )
    assert source_response.status_code == 201
    collect_response = client.post(f"/projects/{project['id']}/collect")
    assert collect_response.status_code == 200
    profile_response = client.post(
        "/llm/profiles",
        json={
            "name": "Unconfigured test analyzer",
            "task_type": "analyzer",
            "provider": "test",
            "base_url": "https://example.invalid/v1",
            "model": "test-analyzer",
            "api_key_env_name": "LOOP_ENGINEERING_TEST_MISSING_LLM_API_KEY",
            "api_key_required": True,
        },
    )
    assert profile_response.status_code == 201
    verifier_profile_response = client.post(
        "/llm/profiles",
        json={
            "name": "Unconfigured test verifier",
            "task_type": "verifier",
            "provider": "test",
            "base_url": "https://example.invalid/v1",
            "model": "test-verifier",
            "api_key_env_name": "LOOP_ENGINEERING_TEST_MISSING_LLM_API_KEY",
            "api_key_required": True,
        },
    )
    assert verifier_profile_response.status_code == 201
    return project


def enqueue_and_process_run(client: TestClient, project_id: str) -> dict:
    enqueue_response = client.post(f"/projects/{project_id}/runs")
    assert enqueue_response.status_code == 202
    assert run_one_job() is True
    run_id = enqueue_response.json()["run"]["id"]
    package_response = client.get(f"/projects/{project_id}/runs/{run_id}")
    assert package_response.status_code == 200
    return package_response.json()


def test_create_collected_project_installs_unconfigured_llm_profiles(tmp_path: Path):
    client = build_client()

    create_collected_project(client, tmp_path)

    profiles_response = client.get("/llm/profiles")
    assert profiles_response.status_code == 200
    profiles_by_task = {profile["task_type"]: profile for profile in profiles_response.json()}
    assert profiles_by_task["analyzer"]["name"] == "Unconfigured test analyzer"
    assert profiles_by_task["verifier"]["name"] == "Unconfigured test verifier"
    assert profiles_by_task["analyzer"]["api_key_env_name"] == "LOOP_ENGINEERING_TEST_MISSING_LLM_API_KEY"
    assert profiles_by_task["verifier"]["api_key_env_name"] == "LOOP_ENGINEERING_TEST_MISSING_LLM_API_KEY"
    assert profiles_by_task["analyzer"]["configured"] is False
    assert profiles_by_task["verifier"]["configured"] is False


def test_start_run_creates_analysis_package(tmp_path: Path):
    client = build_client()
    project = create_collected_project(client, tmp_path)

    package = enqueue_and_process_run(client, project["id"])

    assert package["run"]["status"] == "ready_for_review_with_risks"
    assert NODE_ORDER == EXPECTED_NODE_ORDER
    assert len(package["turns"]) == len(EXPECTED_NODE_ORDER)
    assert [turn["turn_index"] for turn in package["turns"]] == list(
        range(1, len(EXPECTED_NODE_ORDER) + 1)
    )
    assert package["findings"]
    assert package["risks"]
    assert package["recommendations"]
    assert package["quality_scores"][0]["overall_score"] > 0
    calls_response = client.get(f"/llm/calls?run_id={package['run']['id']}")
    assert calls_response.status_code == 200
    calls_by_task = {call["task_type"]: call for call in calls_response.json()}
    assert set(calls_by_task) == {"analyzer", "verifier"}
    assert calls_by_task["analyzer"]["status"] == "skipped"
    assert calls_by_task["verifier"]["status"] == "skipped"


def test_get_and_list_runs_return_existing_analysis_package(tmp_path: Path):
    client = build_client()
    project = create_collected_project(client, tmp_path)
    package = enqueue_and_process_run(client, project["id"])

    list_response = client.get(f"/projects/{project['id']}/runs")
    get_response = client.get(f"/projects/{project['id']}/runs/{package['run']['id']}")

    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert get_response.status_code == 200
    assert get_response.json()["run"]["id"] == package["run"]["id"]


def test_missing_goal_pauses_and_resume_after_answer(tmp_path: Path):
    client = build_client()
    project = create_collected_project(client, tmp_path, analysis_goal=None)

    first_package = enqueue_and_process_run(client, project["id"])

    assert first_package["run"]["status"] == "paused_for_input"
    assert first_package["questions"][0]["status"] == "open"
    question_id = first_package["questions"][0]["id"]
    answer_response = client.post(
        f"/projects/{project['id']}/questions/{question_id}/answer",
        json={"answer_text": "Decide whether to adopt this project."},
    )
    assert answer_response.status_code == 200

    resume_response = client.post(
        f"/projects/{project['id']}/runs/{first_package['run']['id']}/resume"
    )

    assert resume_response.status_code == 200
    resumed = resume_response.json()
    assert resumed["run"]["status"] in {"ready_for_review", "ready_for_review_with_risks"}
    assert resumed["questions"][0]["status"] == "answered"
