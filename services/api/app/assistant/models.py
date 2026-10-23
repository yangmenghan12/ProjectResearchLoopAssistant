from sqlalchemy import ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models import LONG_TEXT, TimestampMixin, new_id


class AssistantConversation(TimestampMixin, Base):
    __tablename__ = "assistant_conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(160), default="新的调研")
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True)


class ResearchEvidencePackage(TimestampMixin, Base):
    """Immutable, bounded source excerpts shared by question and report runs."""
    __tablename__ = "research_evidence_packages"
    __table_args__ = (UniqueConstraint("run_id", name="uq_research_evidence_run"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    items_json: Mapped[list] = mapped_column(JSON, default=list)
    inventory_json: Mapped[dict] = mapped_column(JSON, default=dict)
    parent_id: Mapped[str | None] = mapped_column(String(36))


class AssistantMessage(TimestampMixin, Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "sequence", name="uq_assistant_message_sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("assistant_conversations.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(20), default="chat")
    status: Mapped[str] = mapped_column(String(30), default="completed")
    content: Mapped[str] = mapped_column(LONG_TEXT, default="")
    run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    evidence_package_id: Mapped[str | None] = mapped_column(ForeignKey("research_evidence_packages.id"))
    citations_json: Mapped[list] = mapped_column(JSON, default=list)
    report_id: Mapped[str | None] = mapped_column(ForeignKey("report_snapshots.id"))
    error: Mapped[str | None] = mapped_column(Text)
