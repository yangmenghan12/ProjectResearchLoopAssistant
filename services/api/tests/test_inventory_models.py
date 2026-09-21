from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import CodebaseMap, Project, Source, SourceSnapshot
from tests.test_models import assert_utc_timestamp


def test_source_snapshot_and_codebase_map_persist_with_project_source():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        project = Project(name="M2", topic="Scan codebase")
        session.add(project)
        session.flush()

        source = Source(
            project_id=project.id,
            source_type="local_directory",
            uri="D:/example/repo",
            normalized_uri="D:/example/repo",
        )
        session.add(source)
        session.flush()

        snapshot = SourceSnapshot(
            project_id=project.id,
            source_id=source.id,
            source_type=source.source_type,
            status="collected",
            title="Example repo",
            content_excerpt="README excerpt",
            metadata_json={"file_count": 3},
            skipped_items_json=[{"path": ".env", "reason": "sensitive"}],
        )
        codebase_map = CodebaseMap(
            project_id=project.id,
            source_id=source.id,
            root_label="example-repo",
            tech_stack_json=["python"],
            dependency_files_json=["pyproject.toml"],
            config_files_json=[".gitignore"],
            test_files_json=["tests/test_app.py"],
            entrypoint_files_json=["app/main.py"],
            readme_excerpt="# Example",
            file_tree_json=[{"path": "app/main.py", "kind": "file"}],
            skipped_items_json=[{"path": ".env", "reason": "sensitive"}],
            metadata_json={"total_files": 4},
        )
        session.add_all([snapshot, codebase_map])
        session.commit()

    with Session(engine) as session:
        saved_project = session.scalar(select(Project).where(Project.name == "M2"))
        assert saved_project is not None
        assert len(saved_project.source_snapshots) == 1
        assert len(saved_project.codebase_maps) == 1

        saved_snapshot = saved_project.source_snapshots[0]
        assert saved_snapshot.status == "collected"
        assert saved_snapshot.metadata_json["file_count"] == 3
        assert saved_snapshot.skipped_items_json[0]["reason"] == "sensitive"
        assert_utc_timestamp(saved_snapshot.created_at)
        assert_utc_timestamp(saved_snapshot.updated_at)

        saved_map = saved_project.codebase_maps[0]
        assert saved_map.tech_stack_json == ["python"]
        assert saved_map.dependency_files_json == ["pyproject.toml"]
        assert saved_map.file_tree_json[0]["path"] == "app/main.py"
        assert_utc_timestamp(saved_map.created_at)
        assert_utc_timestamp(saved_map.updated_at)
