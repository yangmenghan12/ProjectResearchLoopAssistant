from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceBundleInputType = Literal[
    "github_repo",
    "local_directory",
    "local_file",
    "web_page",
    "markdown_file",
    "pdf_file",
]


class SourceBundleItemCreate(BaseModel):
    input_type: SourceBundleInputType
    raw_value: str = Field(min_length=1)


class SourceBundleCreate(BaseModel):
    name: str = Field(default="初始项目资料包", min_length=1, max_length=160)
    items: list[SourceBundleItemCreate] = Field(min_length=1, max_length=20)


class SourceBundleItemRead(BaseModel):
    id: str
    bundle_id: str
    project_id: str
    source_id: str | None
    input_type: str
    raw_value: str
    normalized_uri: str | None
    status: str
    error_message: str | None
    metadata_json: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SourceBundleRead(BaseModel):
    id: str
    project_id: str
    name: str
    status: str
    metadata_json: dict
    created_at: datetime
    updated_at: datetime
    items: list[SourceBundleItemRead]

    model_config = {"from_attributes": True}
