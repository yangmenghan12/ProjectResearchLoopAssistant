from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import (
    AnalysisRun,
    EvidenceItem,
    Finding,
    LoopTurn,
    Project,
    QualityScore,
    Question,
    Recommendation,
    Risk,
    Source,
    SourceSnapshot,
)
from tests.test_models import assert_utc_timestamp


def test_loop_artifacts_persist_with_run_and_turn():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        project = Project(name="M3", topic="Run loop", analysis_goal="Assess project")
        session.add(project)
        session.flush()
        source = Source(
            project_id=project.id,
            source_type="local_file",
            uri="notes.md",
            normalized_uri="notes.md",
            status="collected",
        )
        session.add(source)
        session.flush()
        session.add(
            SourceSnapshot(
                project_id=project.id,
                source_id=source.id,
                source_type="local_file",
                status="collected",
                title="notes.md",
                content_excerpt="Useful note",
            )
        )
        run = AnalysisRun(project_id=project.id, status="running", max_turns=6)
        session.add(run)
        session.flush()
        turn = LoopTurn(
            run_id=run.id,
            project_id=project.id,
            turn_index=1,
            node_name="analyze_sources",
            action="Analyze collected sources",
            observation="Created first package draft",
            status="completed",
            metadata_json={"node_order": 3},
        )
        session.add(turn)
        session.flush()
        evidence = EvidenceItem(
            project_id=project.id,
            run_id=run.id,
            source_id=source.id,
            title="Source inventory",
            summary="One collected source",
            evidence_type="source_snapshot",
            reference="notes.md",
            confidence=90,
            metadata_json={"source_type": "local_file"},
        )
        session.add(evidence)
        session.flush()
        session.add_all(
            [
                Finding(
                    project_id=project.id,
                    run_id=run.id,
                    title="Collected source exists",
                    summary="The project has usable source evidence.",
                    category="source_inventory",
                    impact_level="medium",
                    confidence=85,
                    evidence_refs_json=[evidence.id],
                ),
                Risk(
                    project_id=project.id,
                    run_id=run.id,
                    title="Limited evidence",
                    summary="Only one source was collected.",
                    severity="medium",
                    mitigation="Add another source.",
                    evidence_refs_json=[evidence.id],
                ),
                Question(
                    project_id=project.id,
                    run_id=run.id,
                    prompt="What outcome matters most?",
                    reason="Goal specificity affects recommendation.",
                    impact="high",
                    status="open",
                ),
                Recommendation(
                    project_id=project.id,
                    run_id=run.id,
                    summary="Continue with caution.",
                    rationale="Evidence exists but scope is thin.",
                    confidence=70,
                    next_steps_json=["Add sources", "Re-run verifier"],
                ),
                QualityScore(
                    project_id=project.id,
                    run_id=run.id,
                    turn_id=turn.id,
                    overall_score=72,
                    structure_completeness=80,
                    evidence_coverage=70,
                    codebase_coverage=60,
                    risk_transparency=80,
                    question_resolution=50,
                    recommendation_confidence=70,
                    score_delta=12,
                    reasons_json=["Initial package exists"],
                    next_actions_json=["Answer open question"],
                ),
            ]
        )
        session.commit()

    with Session(engine) as session:
        saved_run = session.scalar(select(AnalysisRun))
        assert saved_run is not None
        assert saved_run.status == "running"
        assert len(saved_run.turns) == 1
        assert len(saved_run.evidence_items) == 1
        assert len(saved_run.findings) == 1
        assert len(saved_run.risks) == 1
        assert len(saved_run.questions) == 1
        assert len(saved_run.recommendations) == 1
        assert len(saved_run.quality_scores) == 1
        assert saved_run.quality_scores[0].overall_score == 72
        assert saved_run.questions[0].status == "open"
        assert_utc_timestamp(saved_run.created_at)
        assert_utc_timestamp(saved_run.updated_at)
