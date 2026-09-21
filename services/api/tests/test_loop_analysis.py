from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.context.builder import build_analysis_context
from app.db.base import Base
from app.db.models import CodebaseMap, Project, Source, SourceSnapshot
from app.engine.analyzer import analyze_context


def build_project_with_inventory(analysis_goal: str | None = "Assess adoption"):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(name="Analyzer", topic="Analyze code", analysis_goal=analysis_goal)
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
            content_excerpt="# Repo",
            skipped_items_json=[{"path": ".env", "reason": "sensitive_path"}],
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
            test_files_json=[],
            entrypoint_files_json=["app.py"],
            readme_excerpt="# Repo",
            file_tree_json=[{"path": "app.py", "kind": "file"}],
            skipped_items_json=[{"path": ".env", "reason": "sensitive_path"}],
            metadata_json={"total_files": 2},
        )
    )
    session.commit()
    return session, project.id


def test_build_analysis_context_reads_m2_inventory():
    session, project_id = build_project_with_inventory()

    context = build_analysis_context(session, project_id)

    assert context["project"]["name"] == "Analyzer"
    assert len(context["snapshots"]) == 1
    assert len(context["codebase_maps"]) == 1
    assert context["codebase_maps"][0]["tech_stack"] == ["python"]
    session.close()


def test_analyze_context_generates_artifacts_from_inventory():
    session, project_id = build_project_with_inventory(analysis_goal=None)
    context = build_analysis_context(session, project_id)

    result = analyze_context(context)

    assert any(item["evidence_type"] == "source_snapshot" for item in result["evidence"])
    assert any(item["evidence_type"] == "codebase_map" for item in result["evidence"])
    assert any("python" in finding["summary"].lower() for finding in result["findings"])
    assert any(risk["title"] == "未识别到测试文件" for risk in result["risks"])
    assert any(question["impact"] == "high" for question in result["questions"])
    assert result["recommendations"][0]["summary"]
    session.close()
