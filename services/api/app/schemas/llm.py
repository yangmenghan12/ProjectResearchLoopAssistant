import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


TASK_TYPE_PATTERN = r"^(analyzer|verifier|report)$"
ENV_VAR_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def normalize_api_key_env_name(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not ENV_VAR_NAME_PATTERN.fullmatch(stripped):
        raise ValueError("api_key_env_name must be an environment variable name")
    return stripped


class LLMProfileRead(BaseModel):
    id: str
    name: str
    task_type: str
    provider: str
    base_url: str
    model: str
    api_key_env_name: str | None
    api_key_required: bool
    max_tokens: int
    temperature: float
    timeout_seconds: int
    daily_budget_cents: int | None
    enabled: bool
    configured: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LLMProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    task_type: str = Field(pattern=TASK_TYPE_PATTERN)
    provider: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=160)
    api_key_env_name: str | None = Field(default=None, max_length=120)
    api_key_required: bool = True
    max_tokens: int = Field(default=1200, ge=64, le=16000)
    temperature: float = Field(default=0.2, ge=0, le=2)
    timeout_seconds: int = Field(default=30, ge=1, le=180)
    daily_budget_cents: int | None = Field(default=None, ge=0)
    enabled: bool = True

    @field_validator("api_key_env_name")
    @classmethod
    def validate_api_key_env_name(cls, value: str | None) -> str | None:
        return normalize_api_key_env_name(value)


class LLMProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    provider: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    api_key_env_name: str | None = Field(default=None, max_length=120)
    api_key_required: bool | None = None
    max_tokens: int | None = Field(default=None, ge=64, le=16000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    timeout_seconds: int | None = Field(default=None, ge=1, le=180)
    daily_budget_cents: int | None = Field(default=None, ge=0)
    enabled: bool | None = None

    @field_validator("api_key_env_name")
    @classmethod
    def validate_api_key_env_name(cls, value: str | None) -> str | None:
        return normalize_api_key_env_name(value)


class LLMCallRead(BaseModel):
    id: str
    project_id: str | None
    run_id: str | None
    report_id: str | None
    task_type: str
    provider: str
    model: str
    prompt_hash: str
    input_refs_json: list[str]
    output_ref: str | None
    usage_json: dict[str, Any]
    latency_ms: int | None
    status: str
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
