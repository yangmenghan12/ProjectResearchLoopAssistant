from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import AuditEvent, Project
from app.db.session import get_session
from app.main import create_app
from app.services import audit as audit_service


def build_client_with_session() -> tuple[TestClient, sessionmaker[Session]]:
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


def build_client() -> TestClient:
    client, _ = build_client_with_session()
    return client


def test_project_and_source_workflow(tmp_path: Path):
    client = build_client()
    local_file = tmp_path / "brief.md"
    local_file.write_text("# Brief", encoding="utf-8")

    create_response = client.post(
        "/projects",
        json={
            "name": "LangGraph analysis",
            "topic": "Analyze whether LangGraph fits the loop runtime",
            "analysis_goal": "Produce a decision package",
            "max_turns": 6,
        },
    )
    assert create_response.status_code == 201
    project = create_response.json()
    assert project["status"] == "draft"

    source_response = client.post(
        f"/projects/{project['id']}/sources",
        json={
            "source_type": "local_file",
            "uri": str(local_file),
        },
    )
    assert source_response.status_code == 201
    source = source_response.json()
    assert source["source_type"] == "local_file"
    assert source["status"] == "pending"

    list_response = client.get(f"/projects/{project['id']}/sources")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    events_response = client.get(f"/projects/{project['id']}/events")
    assert events_response.status_code == 200
    event_types = [event["event_type"] for event in events_response.json()]
    assert event_types == ["project.created", "source.created"]


def test_invalid_source_returns_400():
    client = build_client()
    project = client.post(
        "/projects",
        json={
            "name": "Invalid source check",
            "topic": "Check validation",
            "max_turns": 6,
        },
    ).json()

    response = client.post(
        f"/projects/{project['id']}/sources",
        json={
            "source_type": "web_url",
            "uri": "not-a-url",
        },
    )

    assert response.status_code == 400
    assert "web_url must be" in response.json()["detail"]


def test_project_events_use_sequence_when_timestamps_tie():
    client, SessionLocal = build_client_with_session()
    same_time = datetime(2026, 7, 2, 10, 0, tzinfo=UTC)

    with SessionLocal() as session:
        project = Project(name="Event order", topic="Stable audit timeline")
        session.add(project)
        session.flush()
        session.add_all(
            [
                AuditEvent(
                    id="event-b",
                    project_id=project.id,
                    sequence=1,
                    event_type="project.created",
                    message="Project created",
                    actor_id="local-user",
                    created_at=same_time,
                ),
                AuditEvent(
                    id="event-a",
                    project_id=project.id,
                    sequence=2,
                    event_type="source.created",
                    message="Source created",
                    actor_id="local-user",
                    created_at=same_time,
                ),
            ]
        )
        session.commit()
        project_id = project.id

    response = client.get(f"/projects/{project_id}/events")

    assert response.status_code == 200
    assert [(event["event_type"], event["sequence"]) for event in response.json()] == [
        ("project.created", 1),
        ("source.created", 2),
    ]


def test_record_event_retries_when_project_sequence_conflicts(monkeypatch):
    _, SessionLocal = build_client_with_session()

    with SessionLocal() as session:
        project = Project(name="Retry audit sequence", topic="Concurrent audit writes")
        session.add(project)
        session.flush()
        session.add(
            AuditEvent(
                project_id=project.id,
                sequence=1,
                event_type="project.created",
                message="Project created",
            )
        )
        session.flush()

        proposed_sequences = iter([1, 2])
        monkeypatch.setattr(
            audit_service,
            "next_project_event_sequence",
            lambda session, project_id: next(proposed_sequences),
        )

        event = audit_service.record_event(
            session,
            event_type="source.created",
            message="Source created",
            project_id=project.id,
        )

        assert event.sequence == 2
