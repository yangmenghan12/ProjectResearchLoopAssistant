from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ReportCreate(BaseModel):
    run_id: str | None = None


class ReportReviewCreate(BaseModel):
    decision: Literal["approved", "rejected", "needs_more"]
    comment: str | None = Field(default=None, max_length=4000)


class ReportSnapshotRead(BaseModel):
    id: str
    project_id: str
    run_id: str
    status: str
    title: str
    markdown_content: str
    verifier_result_json: dict
    review_decision: str | None
    review_comment: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReportMarkdownRead(BaseModel):
    markdown: str


class ReportDiffRead(BaseModel):
    base_report_id: str
    target_report_id: str
    diff: dict
