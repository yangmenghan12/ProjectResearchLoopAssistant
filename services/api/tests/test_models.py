from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import AuditEvent, Project, Source


def assert_utc_timestamp(value):
    assert value is not None
    assert value.tzinfo is not None
    assert value.utcoffset().total_seconds() == 0


def test_project_source_and_audit_models_persist_together():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        project = Project(
            name="Loop research",
            topic="Analyze loop engineering",
            analysis_goal="Decide whether to build a local runtime",
        )
        session.add(project)
        session.flush()

        source = Source(
            project_id=project.id,
            source_type="web_url",
            uri="https://example.com/article",
            normalized_uri="https://example.com/article",
        )
        event = AuditEvent(
            project_id=project.id,
            event_type="project.created",
            message="Project created",
            actor_id="local-user",
        )
        session.add_all([source, event])
        session.commit()

    with Session(engine) as session:
        saved_project = session.scalar(select(Project).where(Project.name == "Loop research"))
        assert saved_project is not None
        assert saved_project.status == "draft"
        assert_utc_timestamp(saved_project.created_at)
        assert_utc_timestamp(saved_project.updated_at)
        assert len(saved_project.sources) == 1
        assert saved_project.sources[0].source_type == "web_url"
        assert_utc_timestamp(saved_project.sources[0].created_at)
        assert_utc_timestamp(saved_project.sources[0].updated_at)
        assert len(saved_project.audit_events) == 1
        assert_utc_timestamp(saved_project.audit_events[0].created_at)
        assert_utc_timestamp(saved_project.audit_events[0].updated_at)
