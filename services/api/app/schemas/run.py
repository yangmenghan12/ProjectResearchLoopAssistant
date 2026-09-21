from datetime import datetime

from pydantic import BaseModel, Field


class AnalysisRunRead(BaseModel):
    id: str
    project_id: str
    status: str
    current_turn: int
    max_turns: int
    thread_id: str | None = None
    execution_mode: str = "sync"
    last_checkpoint_id: str | None = None
    resume_count: int = 0
    stop_reason: str | None
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LoopTurnRead(BaseModel):
    id: str
    run_id: str
    project_id: str
    turn_index: int
    node_name: str
    action: str
    observation: str
    status: str
    metadata_json: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class EvidenceItemRead(BaseModel):
    id: str
    project_id: str
    run_id: str
    source_id: str | None
    title: str
    summary: str
    evidence_type: str
    reference: str | None
    confidence: int
    metadata_json: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class FindingRead(BaseModel):
    id: str
    project_id: str
    run_id: str
    title: str
    summary: str
    category: str
    impact_level: str
    confidence: int
    evidence_refs_json: list
    metadata_json: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class RiskRead(BaseModel):
    id: str
    project_id: str
    run_id: str
    title: str
    summary: str
    severity: str
    mitigation: str
    evidence_refs_json: list
    metadata_json: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class QuestionRead(BaseModel):
    id: str
    project_id: str
    run_id: str
    prompt: str
    reason: str
    impact: str
    status: str
    answer_text: str | None
    metadata_json: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class RecommendationRead(BaseModel):
    id: str
    project_id: str
    run_id: str
    summary: str
    rationale: str
    confidence: int
    next_steps_json: list
    metadata_json: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class QualityScoreRead(BaseModel):
    id: str
    project_id: str
    run_id: str
    turn_id: str | None
    overall_score: int
    structure_completeness: int
    evidence_coverage: int
    codebase_coverage: int
    risk_transparency: int
    question_resolution: int
    recommendation_confidence: int
    score_delta: int
    reasons_json: list
    next_actions_json: list
    metadata_json: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class AnalysisPackageRead(BaseModel):
    run: AnalysisRunRead
    turns: list[LoopTurnRead]
    evidence_items: list[EvidenceItemRead]
    findings: list[FindingRead]
    risks: list[RiskRead]
    questions: list[QuestionRead]
    recommendations: list[RecommendationRead]
    quality_scores: list[QualityScoreRead]


class QuestionAnswerCreate(BaseModel):
    answer_text: str = Field(min_length=1)


class RunJobRead(BaseModel):
    id: str
    project_id: str
    run_id: str
    job_type: str
    status: str
    attempt_count: int
    max_attempts: int
    queue_name: str = "default"
    locked_by: str | None = None
    locked_at: datetime | None = None
    locked_until: datetime | None = None
    heartbeat_at: datetime | None = None
    available_at: datetime
    next_run_at: datetime | None = None
    last_error: str | None
    payload_json: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RunEventRead(BaseModel):
    id: str
    project_id: str
    run_id: str
    event_type: str
    sequence: int
    payload_json: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class RunEnqueueRead(BaseModel):
    run: AnalysisRunRead
    job: RunJobRead
