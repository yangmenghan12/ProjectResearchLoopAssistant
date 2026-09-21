from sqlalchemy import create_engine, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import AnalysisRun, Project, ReportSnapshot, utc_now
from tests.test_models import assert_utc_timestamp


def test_report_snapshot_persists_review_state():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        project = Project(
            name="M4 report",
            topic="Review package output",
            analysis_goal="Decide whether the package is usable.",
        )
        session.add(project)
        session.flush()
        run = AnalysisRun(project_id=project.id, status="ready_for_review")
        session.add(run)
        session.flush()
        report = ReportSnapshot(
            project_id=project.id,
            run_id=run.id,
            title="M4 report package",
            markdown_content="# M4 report package",
            verifier_result_json={
                "passed": True,
                "risk_level": "low",
                "checks": [{"name": "structure", "passed": True}],
            },
        )
        session.add(report)
        session.commit()

    with Session(engine) as session:
        saved_report = session.scalar(select(ReportSnapshot))
        assert saved_report is not None
        assert saved_report.status == "draft"
        assert saved_report.verifier_result_json["passed"] is True
        assert saved_report.project.name == "M4 report"
        assert saved_report.run.status == "ready_for_review"

        saved_report.review_decision = "approved"
        saved_report.review_comment = "Looks usable."
        saved_report.reviewed_at = utc_now()
        session.commit()

    with Session(engine) as session:
        saved_project = session.scalar(select(Project))
        saved_run = session.scalar(select(AnalysisRun))
        saved_report = session.scalar(select(ReportSnapshot))
        assert saved_project is not None
        assert saved_run is not None
        assert saved_report is not None
        assert len(saved_project.report_snapshots) == 1
        assert len(saved_run.report_snapshots) == 1
        assert saved_report.review_decision == "approved"
        assert saved_report.review_comment == "Looks usable."
        assert_utc_timestamp(saved_report.reviewed_at)
        assert_utc_timestamp(saved_report.created_at)
        assert_utc_timestamp(saved_report.updated_at)


def test_report_markdown_content_uses_mysql_longtext():
    column_type = ReportSnapshot.__table__.c.markdown_content.type

    assert column_type.compile(dialect=mysql.dialect()).upper() == "LONGTEXT"
