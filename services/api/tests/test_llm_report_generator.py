import json
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import (
    AnalysisRun,
    CodebaseMap,
    EvidenceItem,
    Finding,
    LLMReportArtifact,
    LoopTurn,
    Project,
    QualityScore,
    Recommendation,
    ReportSnapshot,
    Risk,
    Source,
    SourceSnapshot,
)
from app.llm.router import LLMRouter
from app.reports.llm_generator import validate_llm_report_sections
from app.services import reports as reports_service
from app.services.reports import create_intelligent_report_snapshot


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def create_reportable_project(session: Session) -> tuple[Project, AnalysisRun, EvidenceItem]:
    project = Project(name="LLM report project", topic="Evidence-bounded reports", analysis_goal="Create a report")
    session.add(project)
    session.flush()

    source = Source(
        project_id=project.id,
        source_type="local_directory",
        uri="repo",
        normalized_uri="repo",
        status="collected",
    )
    session.add(source)
    session.flush()
    session.add(
        SourceSnapshot(
            project_id=project.id,
            source_id=source.id,
            source_type="local_directory",
            status="collected",
            title="repo",
            content_excerpt="A collected codebase.",
        )
    )
    session.add(
        CodebaseMap(
            project_id=project.id,
            source_id=source.id,
            root_label="repo",
            tech_stack_json=["python"],
            dependency_files_json=["pyproject.toml"],
            config_files_json=[],
            test_files_json=["tests/test_app.py"],
            entrypoint_files_json=["app.py"],
            readme_excerpt="# Repo",
            file_tree_json=["app.py", "tests/test_app.py"],
            skipped_items_json=[],
            metadata_json={},
        )
    )

    run = AnalysisRun(project_id=project.id, status="ready_for_review", max_turns=6)
    session.add(run)
    session.flush()
    turn = LoopTurn(
        run_id=run.id,
        project_id=project.id,
        turn_index=1,
        node_name="persist_state",
        action="Persist package",
        observation="Package ready",
        status="completed",
        metadata_json={},
    )
    session.add(turn)
    session.flush()
    evidence = EvidenceItem(
        project_id=project.id,
        run_id=run.id,
        source_id=source.id,
        title="Entrypoint evidence",
        summary="The app.py entrypoint exists.",
        evidence_type="codebase_map",
        reference="app.py",
        confidence=90,
        metadata_json={},
    )
    session.add(evidence)
    session.flush()
    session.add_all(
        [
            Finding(
                project_id=project.id,
                run_id=run.id,
                title="Entrypoint exists",
                summary="The codebase has an app.py entrypoint.",
                category="codebase",
                impact_level="medium",
                confidence=85,
                evidence_refs_json=[evidence.id],
            ),
            Risk(
                project_id=project.id,
                run_id=run.id,
                title="Limited scope",
                summary="Only one entrypoint was inspected.",
                severity="medium",
                mitigation="Review additional modules before release.",
                evidence_refs_json=[evidence.id],
            ),
            Recommendation(
                project_id=project.id,
                run_id=run.id,
                summary="Continue review with module-level checks.",
                rationale="The entrypoint evidence is present.",
                confidence=80,
                next_steps_json=["Inspect modules"],
            ),
            QualityScore(
                project_id=project.id,
                run_id=run.id,
                turn_id=turn.id,
                overall_score=90,
                structure_completeness=90,
                evidence_coverage=90,
                codebase_coverage=90,
                risk_transparency=90,
                question_resolution=90,
                recommendation_confidence=90,
                score_delta=10,
                reasons_json=["Package is complete"],
                next_actions_json=["Generate report"],
            ),
        ]
    )
    session.flush()
    return project, run, evidence


def latest_artifact(session: Session) -> LLMReportArtifact:
    artifact = session.scalar(select(LLMReportArtifact).order_by(LLMReportArtifact.created_at.desc()))
    assert artifact is not None
    return artifact


def test_validator_rejects_unknown_evidence_ref():
    result = validate_llm_report_sections(
        {
            "executive_summary": "summary",
            "sections": [{"heading": "Finding", "body": "Body", "evidence_refs": ["missing"]}],
        },
        allowed_evidence_refs={"evidence-1"},
    )

    assert result.accepted is False
    assert any("unknown evidence ref" in message for message in result.messages)


def test_validator_rejects_missing_or_blank_executive_summary():
    for payload in (
        {"sections": [{"heading": "Finding", "body": "Body", "evidence_refs": ["evidence-1"]}]},
        {"executive_summary": "   ", "sections": [{"heading": "Finding", "body": "Body", "evidence_refs": ["evidence-1"]}]},
    ):
        result = validate_llm_report_sections(payload, allowed_evidence_refs={"evidence-1"})

        assert result.accepted is False
        assert "executive_summary must be a non-empty string" in result.messages


def test_validator_rejects_non_string_summary_heading_and_body():
    payloads = (
        {
            "executive_summary": {"text": "summary"},
            "sections": [{"heading": "Finding", "body": "Body", "evidence_refs": ["evidence-1"]}],
            "message": "executive_summary must be a non-empty string",
        },
        {
            "executive_summary": "summary",
            "sections": [{"heading": ["Finding"], "body": "Body", "evidence_refs": ["evidence-1"]}],
            "message": "sections[0].heading must be a non-empty string",
        },
        {
            "executive_summary": "summary",
            "sections": [{"heading": "Finding", "body": 123, "evidence_refs": ["evidence-1"]}],
            "message": "sections[0].body must be a non-empty string",
        },
    )

    for item in payloads:
        payload = {key: value for key, value in item.items() if key != "message"}
        result = validate_llm_report_sections(payload, allowed_evidence_refs={"evidence-1"})

        assert result.accepted is False
        assert item["message"] in result.messages


def test_validator_rejects_missing_or_non_list_sections():
    for payload in (
        {"executive_summary": "summary"},
        {"executive_summary": "summary", "sections": "not-a-list"},
    ):
        result = validate_llm_report_sections(payload, allowed_evidence_refs={"evidence-1"})

        assert result.accepted is False
        assert "sections must be a list" in result.messages


def test_validator_rejects_section_missing_or_blank_heading_and_body():
    result = validate_llm_report_sections(
        {
            "executive_summary": "summary",
            "sections": [
                {"body": "Body", "evidence_refs": ["evidence-1"]},
                {"heading": "   ", "body": "Body", "evidence_refs": ["evidence-1"]},
                {"heading": "Finding", "evidence_refs": ["evidence-1"]},
                {"heading": "Finding", "body": "   ", "evidence_refs": ["evidence-1"]},
            ],
        },
        allowed_evidence_refs={"evidence-1"},
    )

    assert result.accepted is False
    assert result.messages.count("sections[0].heading must be a non-empty string") == 1
    assert result.messages.count("sections[1].heading must be a non-empty string") == 1
    assert result.messages.count("sections[2].body must be a non-empty string") == 1
    assert result.messages.count("sections[3].body must be a non-empty string") == 1


def test_validator_rejects_missing_non_list_empty_or_blank_evidence_refs():
    result = validate_llm_report_sections(
        {
            "executive_summary": "summary",
            "sections": [
                {"heading": "Finding", "body": "Body"},
                {"heading": "Finding", "body": "Body", "evidence_refs": "evidence-1"},
                {"heading": "Finding", "body": "Body", "evidence_refs": []},
                {"heading": "Finding", "body": "Body", "evidence_refs": ["   "]},
            ],
        },
        allowed_evidence_refs={"evidence-1"},
    )

    assert result.accepted is False
    assert "sections[0].evidence_refs must be a list" in result.messages
    assert "sections[1].evidence_refs must be a list" in result.messages
    assert result.messages.count("sections[0].evidence_refs must include at least one known evidence ref") == 1
    assert result.messages.count("sections[1].evidence_refs must include at least one known evidence ref") == 1
    assert result.messages.count("sections[2].evidence_refs must include at least one known evidence ref") == 1
    assert result.messages.count("sections[3].evidence_refs[0] must be a non-empty string") == 1
    assert result.messages.count("sections[3].evidence_refs must include at least one known evidence ref") == 1


def test_validator_accepts_evidence_bounded_payload():
    result = validate_llm_report_sections(
        {
            "executive_summary": "  summary  ",
            "sections": [{"heading": "  Finding  ", "body": "  Body  ", "evidence_refs": [" evidence-1 "]}],
        },
        allowed_evidence_refs={"evidence-1"},
    )

    assert result.accepted is True
    assert result.normalized == {
        "executive_summary": "summary",
        "sections": [{"heading": "Finding", "body": "Body", "evidence_refs": ["evidence-1"]}],
    }


def test_intelligent_report_skipped_llm_returns_deterministic_report_and_writes_artifact(monkeypatch):
    def fake_chat(self, **kwargs):
        return {"status": "skipped", "call_id": "call-skipped", "text": "", "usage": {}, "error_message": "no profile"}

    monkeypatch.setattr(LLMRouter, "chat", fake_chat)
    with session_scope() as session:
        project, _, _ = create_reportable_project(session)

        report = create_intelligent_report_snapshot(session, project)
        artifact = latest_artifact(session)

        assert "## 智能报告增强" not in report.markdown_content
        assert artifact.status == "skipped"
        assert artifact.call_id == "call-skipped"
        assert artifact.validation_result_json["accepted"] is False


def test_intelligent_report_llm_exception_returns_committable_deterministic_report(monkeypatch):
    def fake_chat(self, **kwargs):
        raise RuntimeError("provider timeout")

    monkeypatch.setattr(LLMRouter, "chat", fake_chat)
    with session_scope() as session:
        project, _, _ = create_reportable_project(session)

        report = create_intelligent_report_snapshot(session, project)
        report_id = report.id
        session.commit()

        saved_report = session.get(ReportSnapshot, report_id)
        artifact = latest_artifact(session)

        assert saved_report is not None
        assert "## 智能报告增强" not in saved_report.markdown_content
        assert artifact.report_id == report_id
        assert artifact.status == "failed"
        assert artifact.validation_result_json["accepted"] is False
        assert any("provider timeout" in message for message in artifact.validation_result_json["messages"])


def test_intelligent_report_ignores_failed_failure_artifact_write(monkeypatch):
    def fake_chat(self, **kwargs):
        raise RuntimeError("provider timeout")

    def fake_add_artifact(*args, **kwargs):
        raise RuntimeError("artifact storage unavailable")

    monkeypatch.setattr(LLMRouter, "chat", fake_chat)
    monkeypatch.setattr(reports_service, "add_llm_report_artifact", fake_add_artifact)
    with session_scope() as session:
        project, _, _ = create_reportable_project(session)

        report = create_intelligent_report_snapshot(session, project)
        report_id = report.id
        session.commit()

        saved_report = session.get(ReportSnapshot, report_id)
        artifacts = list(session.scalars(select(LLMReportArtifact)).all())

        assert saved_report is not None
        assert "## 智能报告增强" not in saved_report.markdown_content
        assert artifacts == []


def test_intelligent_report_rejects_unknown_evidence_ref_and_keeps_deterministic_markdown(monkeypatch):
    payload = {
        "executive_summary": "Smart summary",
        "sections": [{"heading": "Invalid", "body": "Unknown ref.", "evidence_refs": ["missing"]}],
    }

    def fake_chat(self, **kwargs):
        return {"status": "succeeded", "call_id": "call-rejected", "text": json.dumps(payload), "usage": {}}

    monkeypatch.setattr(LLMRouter, "chat", fake_chat)
    with session_scope() as session:
        project, _, _ = create_reportable_project(session)

        report = create_intelligent_report_snapshot(session, project)
        artifact = latest_artifact(session)

        assert "## 智能报告增强" not in report.markdown_content
        assert artifact.status == "rejected"
        assert artifact.content_json == payload
        assert any("unknown evidence ref" in message for message in artifact.validation_result_json["messages"])


def test_intelligent_report_accepts_valid_json_appends_sections_and_records_call_id(monkeypatch):
    captured_kwargs: dict = {}

    def fake_chat(self, **kwargs):
        captured_kwargs.update(kwargs)
        evidence_id = kwargs["input_refs"][0]
        return {
            "status": "succeeded",
            "call_id": "call-accepted",
            "text": json.dumps(
                {
                    "executive_summary": "Smart summary",
                    "sections": [
                        {
                            "heading": "Evidence-grounded insight",
                            "body": "The entrypoint evidence supports the recommendation.",
                            "evidence_refs": [evidence_id],
                        }
                    ],
                }
            ),
            "usage": {"total_tokens": 12},
        }

    monkeypatch.setattr(LLMRouter, "chat", fake_chat)
    with session_scope() as session:
        project, run, evidence = create_reportable_project(session)

        report = create_intelligent_report_snapshot(session, project)
        artifact = latest_artifact(session)

        assert "## 智能报告增强" in report.markdown_content
        assert "### Evidence-grounded insight" in report.markdown_content
        assert f"`{evidence.id}`" in report.markdown_content
        assert report.run_id == run.id
        assert artifact.status == "accepted"
        assert artifact.call_id == "call-accepted"
        assert artifact.validation_result_json["accepted"] is True
        assert captured_kwargs["required"] is False
        assert captured_kwargs["task_type"] == "report"
        assert captured_kwargs["project_id"] == project.id
        assert captured_kwargs["run_id"] == run.id
        assert captured_kwargs["report_id"] == report.id
        assert captured_kwargs["input_refs"] == [evidence.id]
        assert captured_kwargs["output_ref"] == f"llm_report_artifact:{report.id}"
