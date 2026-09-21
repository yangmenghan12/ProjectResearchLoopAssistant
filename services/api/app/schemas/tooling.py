from datetime import datetime

from pydantic import BaseModel


class ToolRegistrationRead(BaseModel):
    id: str
    name: str
    description: str
    permission_level: str
    input_schema_json: dict
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ToolCallRead(BaseModel):
    id: str
    project_id: str | None
    run_id: str | None
    turn_id: str | None
    tool_name: str
    permission_level: str
    status: str
    input_summary_json: dict
    output_summary_json: dict
    error_message: str | None
    latency_ms: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
