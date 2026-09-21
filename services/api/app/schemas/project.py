from datetime import datetime

from pydantic import BaseModel, Field

from app.research.profiles import DEFAULT_RESEARCH_PROFILE


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    topic: str = Field(min_length=1)
    analysis_goal: str | None = None
    research_profile: str = Field(default=DEFAULT_RESEARCH_PROFILE, min_length=1, max_length=80)
    max_turns: int = Field(default=6, ge=1, le=20)


class ProjectRead(BaseModel):
    id: str
    name: str
    topic: str
    analysis_goal: str | None
    status: str
    research_profile: str
    max_turns: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SourceCreate(BaseModel):
    source_type: str = Field(min_length=1, max_length=40)
    uri: str = Field(min_length=1)


class SourceRead(BaseModel):
    id: str
    project_id: str
    source_type: str
    uri: str
    normalized_uri: str
    status: str
    metadata_json: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AuditEventRead(BaseModel):
    id: str
    project_id: str | None
    sequence: int
    event_type: str
    message: str
    actor_id: str
    payload_json: dict
    created_at: datetime

    model_config = {"from_attributes": True}
