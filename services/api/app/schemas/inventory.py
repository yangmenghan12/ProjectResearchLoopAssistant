from datetime import datetime

from pydantic import BaseModel


class SourceSnapshotRead(BaseModel):
    id: str
    project_id: str
    source_id: str
    source_type: str
    status: str
    title: str | None
    content_excerpt: str | None
    content_path: str | None
    metadata_json: dict
    skipped_items_json: list
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CodebaseMapRead(BaseModel):
    id: str
    project_id: str
    source_id: str
    root_label: str
    tech_stack_json: list
    dependency_files_json: list
    config_files_json: list
    test_files_json: list
    entrypoint_files_json: list
    readme_excerpt: str | None
    file_tree_json: list
    skipped_items_json: list
    metadata_json: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectInventoryRead(BaseModel):
    project_id: str
    snapshots: list[SourceSnapshotRead]
    codebase_maps: list[CodebaseMapRead]
