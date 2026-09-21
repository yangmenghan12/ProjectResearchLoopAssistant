from pathlib import Path

from sqlalchemy import func, select

from app.db.models import AuditEvent, Source, SourceSnapshot
from tests.test_projects_sources_api import build_client_with_session


def create_project(client, name: str = "Source bundle project") -> dict:
    response = client.post(
        "/projects",
        json={
            "name": name,
            "topic": "Validate source bundle intake",
            "analysis_goal": "Register source inputs before collection.",
            "max_turns": 6,
        },
    )
    assert response.status_code == 201
    return response.json()


def count_sources(SessionLocal) -> int:
    with SessionLocal() as session:
        return int(session.scalar(select(func.count()).select_from(Source)) or 0)


def test_collect_source_bundle_collects_markdown_file_and_updates_statuses(tmp_path: Path):
    client, SessionLocal = build_client_with_session()
    project = create_project(client)
    markdown_file = tmp_path / "notes.md"
    markdown_file.write_text("# Notes\nUseful bundle source.", encoding="utf-8")

    create_response = client.post(
        f"/projects/{project['id']}/source-bundles",
        json={"items": [{"input_type": "markdown_file", "raw_value": str(markdown_file)}]},
    )
    assert create_response.status_code == 201
    bundle_id = create_response.json()["id"]

    collect_response = client.post(
        f"/projects/{project['id']}/source-bundles/{bundle_id}/collect"
    )

    assert collect_response.status_code == 200
    bundle = collect_response.json()
    assert bundle["status"] == "collected"
    assert len(bundle["items"]) == 1
    assert bundle["items"][0]["status"] == "collected"

    with SessionLocal() as session:
        source = session.scalar(select(Source).where(Source.project_id == project["id"]))
        snapshot = session.scalar(
            select(SourceSnapshot).where(SourceSnapshot.project_id == project["id"])
        )

    assert source is not None
    assert source.status == "collected"
    assert snapshot is not None
    assert snapshot.title == "notes.md"
    assert snapshot.content_excerpt.startswith("# Notes")
    assert snapshot.metadata_json["path"] == str(markdown_file.resolve())


def test_collect_source_bundle_updates_only_valid_source_items(tmp_path: Path):
    client, SessionLocal = build_client_with_session()
    project = create_project(client)
    valid_file = tmp_path / "valid.md"
    valid_file.write_text("# Valid", encoding="utf-8")

    create_response = client.post(
        f"/projects/{project['id']}/source-bundles",
        json={
            "items": [
                {"input_type": "markdown_file", "raw_value": str(valid_file)},
                {"input_type": "markdown_file", "raw_value": str(tmp_path / "missing.md")},
            ]
        },
    )
    assert create_response.status_code == 201
    bundle_id = create_response.json()["id"]

    collect_response = client.post(
        f"/projects/{project['id']}/source-bundles/{bundle_id}/collect"
    )

    assert collect_response.status_code == 200
    bundle = collect_response.json()
    assert bundle["status"] == "partial"
    statuses_by_raw_value = {item["raw_value"]: item["status"] for item in bundle["items"]}
    assert statuses_by_raw_value[str(valid_file)] == "collected"
    assert statuses_by_raw_value[str(tmp_path / "missing.md")] == "failed"

    with SessionLocal() as session:
        snapshot_count = session.scalar(select(func.count()).select_from(SourceSnapshot))

    assert snapshot_count == 1


def test_collect_source_bundle_does_not_collect_other_bundles(tmp_path: Path):
    client, SessionLocal = build_client_with_session()
    project = create_project(client)
    first_file = tmp_path / "first.md"
    first_file.write_text("# First", encoding="utf-8")
    second_file = tmp_path / "second.md"
    second_file.write_text("# Second", encoding="utf-8")

    first_bundle = client.post(
        f"/projects/{project['id']}/source-bundles",
        json={"name": "First", "items": [{"input_type": "markdown_file", "raw_value": str(first_file)}]},
    ).json()
    second_bundle = client.post(
        f"/projects/{project['id']}/source-bundles",
        json={"name": "Second", "items": [{"input_type": "markdown_file", "raw_value": str(second_file)}]},
    ).json()

    response = client.post(
        f"/projects/{project['id']}/source-bundles/{first_bundle['id']}/collect"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "collected"

    second_response = client.get(
        f"/projects/{project['id']}/source-bundles/{second_bundle['id']}"
    )
    assert second_response.status_code == 200
    assert second_response.json()["status"] == "ready"
    assert second_response.json()["items"][0]["status"] == "pending"

    with SessionLocal() as session:
        snapshots = list(
            session.scalars(
                select(SourceSnapshot).where(SourceSnapshot.project_id == project["id"])
            )
        )
    assert [snapshot.title for snapshot in snapshots] == ["first.md"]


def test_collect_source_bundle_is_idempotent_for_finished_items(tmp_path: Path):
    client, SessionLocal = build_client_with_session()
    project = create_project(client)
    markdown_file = tmp_path / "notes.md"
    markdown_file.write_text("# Notes", encoding="utf-8")
    bundle = client.post(
        f"/projects/{project['id']}/source-bundles",
        json={"items": [{"input_type": "markdown_file", "raw_value": str(markdown_file)}]},
    ).json()

    first_response = client.post(
        f"/projects/{project['id']}/source-bundles/{bundle['id']}/collect"
    )
    second_response = client.post(
        f"/projects/{project['id']}/source-bundles/{bundle['id']}/collect"
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json()["status"] == "collected"

    with SessionLocal() as session:
        snapshot_count = session.scalar(select(func.count()).select_from(SourceSnapshot))
    assert snapshot_count == 1


def test_collect_source_bundle_returns_404_for_missing_project_or_bundle(tmp_path: Path):
    client, _ = build_client_with_session()
    project = create_project(client)
    local_file = tmp_path / "notes.md"
    local_file.write_text("# Notes", encoding="utf-8")
    create_response = client.post(
        f"/projects/{project['id']}/source-bundles",
        json={"items": [{"input_type": "markdown_file", "raw_value": str(local_file)}]},
    )
    assert create_response.status_code == 201
    bundle_id = create_response.json()["id"]

    missing_project_response = client.post(
        f"/projects/missing-project/source-bundles/{bundle_id}/collect"
    )
    missing_bundle_response = client.post(
        f"/projects/{project['id']}/source-bundles/missing-bundle/collect"
    )

    assert missing_project_response.status_code == 404
    assert missing_bundle_response.status_code == 404


def test_collect_source_bundle_collects_malformed_pdf_as_parse_error(tmp_path: Path):
    client, SessionLocal = build_client_with_session()
    project = create_project(client)
    pdf_file = tmp_path / "brief.pdf"
    pdf_file.write_bytes(b"%PDF-1.4\nnot a complete pdf")

    create_response = client.post(
        f"/projects/{project['id']}/source-bundles",
        json={"items": [{"input_type": "pdf_file", "raw_value": str(pdf_file)}]},
    )
    assert create_response.status_code == 201
    bundle_id = create_response.json()["id"]

    collect_response = client.post(
        f"/projects/{project['id']}/source-bundles/{bundle_id}/collect"
    )

    assert collect_response.status_code == 200
    bundle = collect_response.json()
    assert bundle["status"] == "collected"
    assert bundle["items"][0]["status"] == "collected"

    with SessionLocal() as session:
        snapshot = session.scalar(
            select(SourceSnapshot).where(SourceSnapshot.project_id == project["id"])
        )

    assert snapshot is not None
    assert snapshot.status == "collected"
    assert snapshot.title == "brief.pdf"
    assert snapshot.metadata_json["file_type"] == "pdf"
    assert snapshot.metadata_json["path"] == str(pdf_file.resolve())
    assert snapshot.metadata_json["size_bytes"] > 0
    assert "pdf_parse_error" in snapshot.metadata_json
    assert snapshot.skipped_items_json == []


def test_create_source_bundle_registers_ready_sources(tmp_path: Path):
    client, SessionLocal = build_client_with_session()
    project = create_project(client)
    local_dir = tmp_path / "workspace"
    local_dir.mkdir()
    markdown_file = tmp_path / "brief.md"
    markdown_file.write_text("# Brief", encoding="utf-8")

    response = client.post(
        f"/projects/{project['id']}/source-bundles",
        json={
            "items": [
                {"input_type": "local_directory", "raw_value": str(local_dir)},
                {"input_type": "markdown_file", "raw_value": str(markdown_file)},
            ]
        },
    )

    assert response.status_code == 201
    bundle = response.json()
    assert bundle["name"] == "初始项目资料包"
    assert bundle["status"] == "ready"
    assert len(bundle["items"]) == 2
    assert {item["status"] for item in bundle["items"]} == {"pending"}
    assert {item["input_type"] for item in bundle["items"]} == {"local_directory", "markdown_file"}
    assert all(item["source_id"] for item in bundle["items"])
    assert count_sources(SessionLocal) == 2


def test_create_source_bundle_keeps_failed_items_without_sources(tmp_path: Path):
    client, SessionLocal = build_client_with_session()
    project = create_project(client)
    local_file = tmp_path / "valid.txt"
    local_file.write_text("valid source", encoding="utf-8")
    missing_file = tmp_path / "missing.txt"

    response = client.post(
        f"/projects/{project['id']}/source-bundles",
        json={
            "name": "Mixed intake",
            "items": [
                {"input_type": "local_file", "raw_value": str(local_file)},
                {"input_type": "local_file", "raw_value": str(missing_file)},
            ],
        },
    )

    assert response.status_code == 201
    bundle = response.json()
    assert bundle["status"] == "partial"
    successful_items = [item for item in bundle["items"] if item["status"] == "pending"]
    failed_items = [item for item in bundle["items"] if item["status"] == "failed"]
    assert len(successful_items) == 1
    assert successful_items[0]["source_id"]
    assert len(failed_items) == 1
    assert failed_items[0]["source_id"] is None
    assert "Local path does not exist" in failed_items[0]["error_message"]
    assert count_sources(SessionLocal) == 1


def test_create_source_bundle_marks_all_failed_when_no_input_valid(tmp_path: Path):
    client, SessionLocal = build_client_with_session()
    project = create_project(client)

    response = client.post(
        f"/projects/{project['id']}/source-bundles",
        json={
            "items": [
                {"input_type": "local_file", "raw_value": str(tmp_path / "missing-a.md")},
                {"input_type": "pdf_file", "raw_value": str(tmp_path / "missing-b.pdf")},
            ],
        },
    )

    assert response.status_code == 201
    bundle = response.json()
    assert bundle["status"] == "failed"
    assert {item["status"] for item in bundle["items"]} == {"failed"}
    assert all(item["source_id"] is None for item in bundle["items"])
    assert count_sources(SessionLocal) == 0


def test_source_bundle_list_get_and_project_isolation(tmp_path: Path):
    client, _ = build_client_with_session()
    project = create_project(client, name="Bundle owner")
    other_project = create_project(client, name="Other bundle owner")
    local_file = tmp_path / "notes.md"
    local_file.write_text("# Notes", encoding="utf-8")

    create_response = client.post(
        f"/projects/{project['id']}/source-bundles",
        json={"items": [{"input_type": "markdown_file", "raw_value": str(local_file)}]},
    )
    assert create_response.status_code == 201
    bundle_id = create_response.json()["id"]

    list_response = client.get(f"/projects/{project['id']}/source-bundles")
    assert list_response.status_code == 200
    bundles = list_response.json()
    assert [bundle["id"] for bundle in bundles] == [bundle_id]
    assert len(bundles[0]["items"]) == 1

    get_response = client.get(f"/projects/{project['id']}/source-bundles/{bundle_id}")
    assert get_response.status_code == 200
    assert get_response.json()["items"][0]["input_type"] == "markdown_file"

    cross_project_response = client.get(
        f"/projects/{other_project['id']}/source-bundles/{bundle_id}"
    )
    assert cross_project_response.status_code == 404


def test_create_source_bundle_records_audit_event(tmp_path: Path):
    client, SessionLocal = build_client_with_session()
    project = create_project(client)
    local_file = tmp_path / "audit.md"
    local_file.write_text("# Audit", encoding="utf-8")

    response = client.post(
        f"/projects/{project['id']}/source-bundles",
        json={"items": [{"input_type": "markdown_file", "raw_value": str(local_file)}]},
    )
    assert response.status_code == 201
    bundle = response.json()

    with SessionLocal() as session:
        event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.project_id == project["id"],
                AuditEvent.event_type == "source_bundle.created",
            )
        )
        source = session.scalar(select(Source).where(Source.project_id == project["id"]))

    assert event is not None
    assert event.payload_json["bundle_id"] == bundle["id"]
    assert event.payload_json["status"] == "ready"
    assert event.payload_json["item_count"] == 1
    assert event.payload_json["source_count"] == 1
    assert event.payload_json["failed_count"] == 0
    assert event.payload_json["items"] == [
        {
            "input_type": "markdown_file",
            "status": "pending",
            "source_id": bundle["items"][0]["source_id"],
            "normalized_uri": bundle["items"][0]["normalized_uri"],
            "error_message": None,
        }
    ]
    assert source is not None
    assert source.metadata_json["bundle_id"] == bundle["id"]
    assert source.metadata_json["bundle_item_id"] == bundle["items"][0]["id"]


def test_create_source_bundle_rejects_unsupported_input_type():
    client, _ = build_client_with_session()
    project = create_project(client)

    response = client.post(
        f"/projects/{project['id']}/source-bundles",
        json={"items": [{"input_type": "web_url", "raw_value": "https://example.com"}]},
    )

    assert response.status_code == 422


def test_create_source_bundle_rejects_too_many_items(tmp_path: Path):
    client, _ = build_client_with_session()
    project = create_project(client)
    valid_file = tmp_path / "valid.md"
    valid_file.write_text("# Valid", encoding="utf-8")

    response = client.post(
        f"/projects/{project['id']}/source-bundles",
        json={
            "items": [
                {"input_type": "markdown_file", "raw_value": str(valid_file)}
                for _ in range(21)
            ]
        },
    )

    assert response.status_code == 422


def test_source_bundle_maps_web_page_and_github_repo_inputs():
    client, SessionLocal = build_client_with_session()
    project = create_project(client)

    response = client.post(
        f"/projects/{project['id']}/source-bundles",
        json={
            "items": [
                {"input_type": "web_page", "raw_value": "https://example.com/docs"},
                {"input_type": "github_repo", "raw_value": "https://github.com/openai/codex.git"},
            ]
        },
    )

    assert response.status_code == 201
    bundle = response.json()
    assert bundle["status"] == "ready"

    with SessionLocal() as session:
        sources = list(
            session.scalars(select(Source).where(Source.project_id == project["id"]))
        )

    assert {source.source_type for source in sources} == {"web_url", "github_repo"}
    assert {source.metadata_json["input_type"] for source in sources} == {
        "web_page",
        "github_repo",
    }
