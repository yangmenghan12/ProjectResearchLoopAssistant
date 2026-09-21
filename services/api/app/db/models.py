from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db.base import Base

LONG_TEXT = Text().with_variant(mysql.LONGTEXT(), "mysql")


class UTCDateTime(TypeDecorator[datetime]):
    """数据库可能丢失时区信息，读写时统一按 UTC 归一化。"""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utc_now,
        onupdate=utc_now,
    )


class Project(TimestampMixin, Base):
    """分析项目是 Loop Runtime 的主实体，后续 run、证据和报告都挂在项目下。"""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    analysis_goal: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft")
    research_profile: Mapped[str] = mapped_column(String(80), nullable=False, default="quick_onboarding")
    max_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=6)

    sources: Mapped[list["Source"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    source_bundles: Mapped[list["SourceBundle"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    source_snapshots: Mapped[list["SourceSnapshot"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    codebase_maps: Mapped[list["CodebaseMap"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    analysis_runs: Mapped[list["AnalysisRun"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    report_snapshots: Mapped[list["ReportSnapshot"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )


class Source(TimestampMixin, Base):
    """输入源只记录元数据和规范化位置；读取、clone、扫描由采集服务统一完成。"""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_uri: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    project: Mapped[Project] = relationship(back_populates="sources")
    source_snapshots: Mapped[list["SourceSnapshot"]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
    )
    codebase_maps: Mapped[list["CodebaseMap"]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
    )


class SourceBundle(TimestampMixin, Base):
    """资料包把用户一次提交的多个输入固定下来，采集任务按条目状态处理它。"""

    __tablename__ = "source_bundles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    project: Mapped[Project] = relationship(back_populates="source_bundles")
    items: Mapped[list["SourceBundleItem"]] = relationship(
        back_populates="bundle",
        cascade="all, delete-orphan",
        order_by="SourceBundleItem.created_at, SourceBundleItem.id",
    )


class SourceBundleItem(TimestampMixin, Base):
    """资料包条目保留原始输入、规范化结果和失败原因，避免无效输入丢失上下文。"""

    __tablename__ = "source_bundle_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    bundle_id: Mapped[str] = mapped_column(
        ForeignKey("source_bundles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"),
        index=True,
    )
    input_type: Mapped[str] = mapped_column(String(40), nullable=False)
    raw_value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_uri: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    bundle: Mapped[SourceBundle] = relationship(back_populates="items")
    source: Mapped[Source | None] = relationship()


class SourceSnapshot(TimestampMixin, Base):
    """输入源采集结果快照，保存可审计的只读事实摘要和跳过原因。"""

    __tablename__ = "source_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="collected")
    title: Mapped[str | None] = mapped_column(Text)
    content_excerpt: Mapped[str | None] = mapped_column(Text)
    content_path: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    skipped_items_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="source_snapshots")
    source: Mapped[Source] = relationship(back_populates="source_snapshots")


class CodebaseMap(TimestampMixin, Base):
    """代码库地图保存确定性扫描结果，后续 Loop 只能基于这些证据做分析。"""

    __tablename__ = "codebase_maps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    root_label: Mapped[str] = mapped_column(Text, nullable=False)
    tech_stack_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    dependency_files_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    config_files_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    test_files_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    entrypoint_files_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    readme_excerpt: Mapped[str | None] = mapped_column(Text)
    file_tree_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    skipped_items_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    project: Mapped[Project] = relationship(back_populates="codebase_maps")
    source: Mapped[Source] = relationship(back_populates="codebase_maps")


class AuditEvent(TimestampMixin, Base):
    """审计事件记录用户和系统关键动作，保证分析过程可追溯。"""

    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("project_id", "sequence", name="uq_audit_events_project_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(80), nullable=False, default="local-user")
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    project: Mapped[Project | None] = relationship(back_populates="audit_events")


class AnalysisRun(TimestampMixin, Base):
    """一次 Loop 运行，是分析过程、产物和停止原因的聚合根。"""

    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="running")
    current_turn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    thread_id: Mapped[str | None] = mapped_column(String(120), index=True)
    execution_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="sync")
    last_checkpoint_id: Mapped[str | None] = mapped_column(String(120))
    resume_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stop_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    project: Mapped[Project] = relationship(back_populates="analysis_runs")
    turns: Mapped[list["LoopTurn"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    evidence_items: Mapped[list["EvidenceItem"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    findings: Mapped[list["Finding"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    risks: Mapped[list["Risk"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    questions: Mapped[list["Question"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    recommendations: Mapped[list["Recommendation"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    quality_scores: Mapped[list["QualityScore"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    report_snapshots: Mapped[list["ReportSnapshot"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class LoopTurn(TimestampMixin, Base):
    """Loop 每个节点的执行记录，用于恢复、审计和解释为什么继续或停止。"""

    __tablename__ = "loop_turns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    node_name: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    observation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="completed")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=utc_now)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    run: Mapped[AnalysisRun] = relationship(back_populates="turns")
    quality_scores: Mapped[list["QualityScore"]] = relationship(back_populates="turn")


class EvidenceItem(TimestampMixin, Base):
    """证据项保存可追溯事实，发现和风险只能引用证据而不是凭空判断。"""

    __tablename__ = "evidence_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"), index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(80), nullable=False)
    reference: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    run: Mapped[AnalysisRun] = relationship(back_populates="evidence_items")


class Finding(TimestampMixin, Base):
    """分析发现，必须尽量通过 evidence_refs_json 指向证据项。"""

    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    impact_level: Mapped[str] = mapped_column(String(40), nullable=False, default="medium")
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=75)
    evidence_refs_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    run: Mapped[AnalysisRun] = relationship(back_populates="findings")


class Risk(TimestampMixin, Base):
    """风险登记项记录严重级别、影响和缓解建议。"""

    __tablename__ = "risks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(40), nullable=False, default="medium")
    mitigation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    run: Mapped[AnalysisRun] = relationship(back_populates="risks")


class Question(TimestampMixin, Base):
    """高风险缺口通过问题暂停 Loop，等待用户回答后恢复。"""

    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str] = mapped_column(String(40), nullable=False, default="medium")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="open")
    answer_text: Mapped[str | None] = mapped_column(Text)
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    run: Mapped[AnalysisRun] = relationship(back_populates="questions")


class Recommendation(TimestampMixin, Base):
    """推荐结论和下一步建议，是 Analysis Package 的收束点。"""

    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=70)
    next_steps_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    run: Mapped[AnalysisRun] = relationship(back_populates="recommendations")


class QualityScore(TimestampMixin, Base):
    """Analysis Quality Rubric 每轮评分，驱动继续、暂停或停止决策。"""

    __tablename__ = "quality_scores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    turn_id: Mapped[str | None] = mapped_column(ForeignKey("loop_turns.id"), index=True)
    overall_score: Mapped[int] = mapped_column(Integer, nullable=False)
    structure_completeness: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_coverage: Mapped[int] = mapped_column(Integer, nullable=False)
    codebase_coverage: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_transparency: Mapped[int] = mapped_column(Integer, nullable=False)
    question_resolution: Mapped[int] = mapped_column(Integer, nullable=False)
    recommendation_confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    score_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasons_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    next_actions_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    run: Mapped[AnalysisRun] = relationship(back_populates="quality_scores")
    turn: Mapped[LoopTurn | None] = relationship(back_populates="quality_scores")


class ReportSnapshot(TimestampMixin, Base):
    """报告快照把一次 Analysis Package 的校验结果、Markdown 和人工评审结论固化下来。"""

    __tablename__ = "report_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft", index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    markdown_content: Mapped[str] = mapped_column(LONG_TEXT, nullable=False)
    verifier_result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    review_decision: Mapped[str | None] = mapped_column(String(40))
    review_comment: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    project: Mapped[Project] = relationship(back_populates="report_snapshots")
    run: Mapped[AnalysisRun] = relationship(back_populates="report_snapshots")


class LLMProfile(TimestampMixin, Base):
    """LLM 任务档位。API key 只通过环境变量名引用，不入库。"""

    __tablename__ = "llm_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    task_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    # 只保存环境变量名，避免密钥进入数据库或测试快照。
    api_key_env_name: Mapped[str | None] = mapped_column(String(120))
    api_key_required: Mapped[bool] = mapped_column(Integer, nullable=False, default=True)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=1200)
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.2)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    daily_budget_cents: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=True)


class LLMCall(TimestampMixin, Base):
    """每次 LLM 调用的可审计账本，不保存 API key。"""

    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    report_id: Mapped[str | None] = mapped_column(ForeignKey("report_snapshots.id"), index=True)
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("llm_profiles.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    input_refs_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    output_ref: Mapped[str | None] = mapped_column(Text)
    usage_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    error_message: Mapped[str | None] = mapped_column(Text)


class LLMAnalysisArtifact(TimestampMixin, Base):
    """LLM 输出的结构化产物。只有 accepted=True 的内容可合并进 Analysis Package。"""

    __tablename__ = "llm_analysis_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    call_id: Mapped[str | None] = mapped_column(ForeignKey("llm_calls.id"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(80), nullable=False)
    content_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    validation_result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    accepted: Mapped[bool] = mapped_column(Integer, nullable=False, default=False)


class ToolRegistration(TimestampMixin, Base):
    """Tool Gateway 的只读工具登记表，只描述能力和权限级别，不承载执行逻辑。"""

    __tablename__ = "tool_registrations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    permission_level: Mapped[str] = mapped_column(String(20), nullable=False, default="L0")
    input_schema_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=True)


class ToolCall(TimestampMixin, Base):
    """工具调用审计账本，记录 run 相关上下文、输入摘要、输出摘要和错误信息。"""

    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    turn_id: Mapped[str | None] = mapped_column(ForeignKey("loop_turns.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    permission_level: Mapped[str] = mapped_column(String(20), nullable=False, default="L0")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued", index=True)
    input_summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class LLMReportArtifact(TimestampMixin, Base):
    """LLM 报告生成产物，保存候选内容和校验结果，供后续确定性流程裁决。"""

    __tablename__ = "llm_report_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    report_id: Mapped[str | None] = mapped_column(ForeignKey("report_snapshots.id"), index=True)
    call_id: Mapped[str | None] = mapped_column(ForeignKey("llm_calls.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    content_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    validation_result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class EvaluationRun(TimestampMixin, Base):
    """Evaluation Harness 的运行记录，保存摘要和 case 结果。"""

    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="running", index=True)
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    case_results_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class RunJob(TimestampMixin, Base):
    """后台运行任务，负责把 HTTP 请求和实际 LangGraph 执行解耦。"""

    __tablename__ = "run_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    queue_name: Mapped[str] = mapped_column(String(80), nullable=False, default="default", index=True)
    locked_by: Mapped[str | None] = mapped_column(String(120))
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class RunEvent(TimestampMixin, Base):
    """运行事件是前端订阅和恢复 UI 状态的 durable 事件流。"""

    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_run_events_run_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class GraphCheckpoint(TimestampMixin, Base):
    """LangGraph checkpoint tuple 的 MySQL 保存格式。"""

    __tablename__ = "graph_checkpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    thread_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(120))
    checkpoint_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    channel_values_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class GraphCheckpointWrite(TimestampMixin, Base):
    """LangGraph pending writes，用于 interrupt/resume 恢复。"""

    __tablename__ = "graph_checkpoint_writes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    thread_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(120), nullable=False)
    write_index: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(String(160), nullable=False)
    value_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    task_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
