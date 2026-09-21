from collections.abc import Generator
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Source
from app.db.session import get_session
from app.main import create_app


def build_client() -> tuple[TestClient, sessionmaker[Session]]:
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


def create_project(client: TestClient) -> dict:
    response = client.post(
        "/projects",
        json={
            "name": "M2 collection",
            "topic": "Collect source inventory",
            "max_turns": 6,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_collect_project_sources_creates_inventory_and_codebase_map(tmp_path: Path):
    client, SessionLocal = build_client()
    project = create_project(client)
    local_file = tmp_path / "brief.md"
    local_file.write_text("# Brief\nUseful source note.", encoding="utf-8")
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("# Repo\nCodebase notes.", encoding="utf-8")
    (repo_dir / "pyproject.toml").write_text("[project]\nname='repo'", encoding="utf-8")
    (repo_dir / "app.py").write_text("print('entry')", encoding="utf-8")

    for source_type, uri in [
        ("local_file", str(local_file)),
        ("local_directory", str(repo_dir)),
    ]:
        response = client.post(
            f"/projects/{project['id']}/sources",
            json={"source_type": source_type, "uri": uri},
        )
        assert response.status_code == 201

    collect_response = client.post(f"/projects/{project['id']}/collect")

    assert collect_response.status_code == 200
    inventory = collect_response.json()
    assert len(inventory["snapshots"]) == 2
    assert len(inventory["codebase_maps"]) == 1
    assert inventory["snapshots"][0]["status"] == "collected"
    assert inventory["codebase_maps"][0]["tech_stack_json"] == ["python"]
    assert inventory["codebase_maps"][0]["readme_excerpt"].startswith("# Repo")

    with SessionLocal() as session:
        source_statuses = list(session.scalars(select(Source.status).order_by(Source.created_at)))
    assert source_statuses == ["collected", "collected"]

    events_response = client.get(f"/projects/{project['id']}/events")
    assert events_response.status_code == 200
    event_types = [event["event_type"] for event in events_response.json()]
    assert "source.collection_started" in event_types
    assert event_types.count("source.collected") == 2


def test_get_project_inventory_returns_existing_snapshots(tmp_path: Path):
    client, _ = build_client()
    project = create_project(client)
    local_file = tmp_path / "brief.md"
    local_file.write_text("# Brief", encoding="utf-8")
    client.post(
        f"/projects/{project['id']}/sources",
        json={"source_type": "local_file", "uri": str(local_file)},
    )
    client.post(f"/projects/{project['id']}/collect")

    response = client.get(f"/projects/{project['id']}/inventory")

    assert response.status_code == 200
    inventory = response.json()
    assert len(inventory["snapshots"]) == 1
    assert inventory["snapshots"][0]["title"] == "brief.md"
    assert inventory["codebase_maps"] == []
