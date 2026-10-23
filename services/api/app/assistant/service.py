from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.assistant.models import AssistantConversation, AssistantMessage, ResearchEvidencePackage
from app.core.security import validate_source_input
from app.db.models import AnalysisRun, Project, Source, utc_now
from app.db.session import bind_worker_session_to
from app.llm.json_tools import extract_json_object
from app.llm.router import LLMRouter
from app.services.run_events import record_run_event
from app.services.run_jobs import create_run_job


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=8000)
    mode: str = Field(default="auto", pattern="^(auto|chat|qa|report)$")
    source: str | None = Field(default=None, max_length=2000)


def messages_for(session: Session, conversation_id: str):
    messages = list(session.scalars(select(AssistantMessage).where(
        AssistantMessage.conversation_id == conversation_id).order_by(
            AssistantMessage.sequence)).all())
    changed = False
    for message in messages:
        if message.run_id and message.status in {"queued", "running"}:
            run = session.get(AnalysisRun, message.run_id)
            if run and run.status in {"cancelled", "failed"}:
                message.status = run.status
                message.content = "任务已停止。" if run.status == "cancelled" else "调研失败，可重新发送问题后重试。"
                changed = True
    if changed:
        session.flush()
    return messages


def message_dict(message: AssistantMessage) -> dict:
    return {key: getattr(message, key) for key in (
        "id", "conversation_id", "role", "mode", "status", "content", "run_id",
        "evidence_package_id", "citations_json", "report_id", "error", "created_at")}


def conversation_dict(session: Session, conversation: AssistantConversation) -> dict:
    return {"id": conversation.id, "title": conversation.title, "project_id": conversation.project_id,
            "messages": [message_dict(m) for m in messages_for(session, conversation.id)]}


def infer_source(text: str) -> str | None:
    """Only explicit human text can attach a source; model/tool text never grants IO."""
    github = re.search(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?(?=$|[\s，。])", text)
    if github:
        return github.group(0)
    candidate = text.strip().strip('"')
    if re.match(r"^[A-Za-z]:[\\/]", candidate) or candidate.startswith("/"):
        return candidate
    return None


def choose_mode(session: Session, conversation: AssistantConversation, payload: MessageInput, attached: bool) -> str:
    if payload.mode != "auto":
        return payload.mode
    if attached:
        return "report" if any(w in payload.content for w in ("报告", "report")) else "qa"
    if not conversation.project_id:
        return "chat"
    recent = [{"role": m.role, "content": m.content[:1500]} for m in messages_for(session, conversation.id)[-4:]]
    result = LLMRouter(session).chat(task_type="assistant_router", project_id=conversation.project_id,
        messages=[{"role": "system", "content": '识别用户意图，仅返回 JSON {"mode":"chat|qa|report"}。普通聊天选 chat，询问当前项目选 qa，明确要求生成调研报告选 report。'},
                  *recent, {"role": "user", "content": payload.content}])
    try:
        mode = extract_json_object(result.get("text", "")).get("mode")
        if mode in {"chat", "qa", "report"}:
            return mode
    except (ValueError, TypeError, AttributeError):
        pass
    # Safe, explicit fallback when the provider is unavailable.
    if payload.content.strip().lower() in {"你好", "谢谢", "hi", "hello", "早上好"}:
        return "chat"
    return "report" if any(w in payload.content for w in ("生成报告", "调研报告")) else "qa"


def submit_message(session: Session, conversation: AssistantConversation, payload: MessageInput) -> AssistantMessage:
    # Row lock serializes submissions, keeping message history and active-source binding stable.
    session.refresh(conversation, with_for_update=True)
    messages_for(session, conversation.id)
    active = session.scalar(select(AssistantMessage.id).where(
        AssistantMessage.conversation_id == conversation.id, AssistantMessage.status.in_(["queued", "running"])))
    if active:
        raise ValueError("当前调研尚未完成，请等待完成后继续。")
    source_uri = payload.source or infer_source(payload.content)
    if source_uri and payload.mode == "chat":
        raise ValueError("请切换到项目问答或生成报告后接入项目。")
    source = None
    if source_uri:
        path = Path(source_uri).expanduser()
        source_type = "github_repo" if source_uri.startswith("https://github.com/") else (
            "local_file" if path.is_file() else "local_directory")
        validated = validate_source_input(source_type, source_uri)
        if source_type != "github_repo" and path.is_symlink():
            raise ValueError("请提供实际项目路径，不要使用符号链接。")
        if not conversation.project_id:
            project = Project(name=(path.name or "代码调研")[:160], topic="个人项目调研",
                              analysis_goal="基于源码证据回答问题并生成调研报告")
            session.add(project)
            session.flush()
            conversation.project_id = project.id
        existing = session.scalar(select(Source).where(Source.project_id == conversation.project_id).limit(1))
        if existing and existing.normalized_uri != validated.normalized_uri:
            raise ValueError("一个会话绑定一个项目；分析其他地址请新建会话。")
        source = existing or Source(project_id=conversation.project_id, source_type=source_type,
            uri=source_uri, normalized_uri=validated.normalized_uri, metadata_json=validated.metadata)
        session.add(source)
        session.flush()
    mode = choose_mode(session, conversation, payload, bool(source_uri))
    if mode != "chat" and not conversation.project_id:
        raise ValueError("请先提供 GitHub 地址或本机项目路径。")
    sequence = (session.scalar(select(func.max(AssistantMessage.sequence)).where(
        AssistantMessage.conversation_id == conversation.id)) or 0) + 1
    session.add(AssistantMessage(conversation_id=conversation.id, sequence=sequence,
        role="user", content=payload.content, mode=mode))
    reply = AssistantMessage(conversation_id=conversation.id, role="assistant", content="", mode=mode,
                             sequence=sequence + 1, status="running" if mode == "chat" else "queued")
    session.add(reply)
    if conversation.title == "新的调研":
        conversation.title = payload.content[:70]
    conversation.updated_at = utc_now()
    session.flush()
    if mode == "chat":
        recent = [{"role": m.role, "content": m.content[:3000]} for m in messages_for(session, conversation.id)
                  if m.id != reply.id][-10:]
        result = LLMRouter(session).chat(task_type="assistant_chat", project_id=conversation.project_id,
            messages=[{"role": "system", "content": "你是个人调研助手，可以正常聊天。此模式没有读取代码，不能声称已检查项目。用户需要分析代码时，提示使用项目问答或生成报告。"}, *recent])
        reply.content = result.get("text") or "当前模型未就绪，请在模型设置中配置服务后继续聊天。"
        reply.status = "completed" if result.get("status") == "succeeded" else "unavailable"
        session.commit()
        return reply
    project = session.get(Project, conversation.project_id)
    source = source or session.scalar(select(Source).where(Source.project_id == project.id).limit(1))
    if source is None:
        raise ValueError("项目没有可读取的来源。")
    run = AnalysisRun(project_id=project.id, status="queued", max_turns=6, execution_mode="background")
    session.add(run)
    session.flush()
    run.thread_id = f"assistant:{mode}:{run.id}"
    reply.run_id = run.id
    bind_worker_session_to(session)
    create_run_job(session, project.id, run.id, "assistant", {
        "conversation_id": conversation.id, "message_id": reply.id, "source_id": source.id,
        "question": payload.content, "mode": mode})
    record_run_event(session, project.id, run.id, "run_queued", {"mode": mode, "message_id": reply.id})
    session.commit()
    return reply
