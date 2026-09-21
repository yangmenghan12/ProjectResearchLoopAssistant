import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import (
    AnalysisRun,
    CodebaseMap,
    EvidenceItem,
    Finding,
    LLMReportArtifact,
    LoopTurn,
    Project,
    QualityScore,
    Question,
    Recommendation,
    ReportSnapshot,
    Risk,
    SourceBundle,
    SourceSnapshot,
    utc_now,
)
from app.engine.runner import enqueue_run
from app.llm.json_tools import extract_json_object
from app.llm.router import LLMRouter
from app.research.profiles import get_research_profile
from app.reports.llm_generator import render_llm_sections_markdown, validate_llm_report_sections
from app.reports.renderer import render_report_markdown
from app.services.audit import record_event
from app.verifiers.package import verify_analysis_package

REVIEWABLE_RUN_STATUSES = {"ready_for_review", "ready_for_review_with_risks"}
REVIEW_STATUS_BY_DECISION = {
    "approved": ("approved", "completed"),
    "rejected": ("rejected", "ready"),
    "needs_more": ("needs_more", "ready"),
}
FOLLOWUP_REVIEW_DECISIONS = {"rejected", "needs_more"}


def create_report_snapshot(
    session: Session,
    project: Project,
    *,
    run_id: str | None = None,
) -> ReportSnapshot:
    """从一次已结束的 Loop run 生成报告快照。

    生成报告不会重新执行分析，也不会修改外部系统；它只把已经沉淀的
    Analysis Package、Source Inventory 和 verifier 结果固化成可评审产物。
    """

    run = get_run_for_report(session, project, run_id)
    if run.status not in REVIEWABLE_RUN_STATUSES:
        raise ValueError(f"Run status `{run.status}` is not ready for report review")

    package = build_analysis_package(session, run)
    inventory = build_inventory(session, project)
    verifier_result = verify_analysis_package(package, inventory)
    title = f"{project.name} Project Research Report"
    markdown = render_report_markdown(project, package, inventory, verifier_result)
    report = ReportSnapshot(
        project_id=project.id,
        run_id=run.id,
        status="draft" if verifier_result["passed"] else "blocked",
        title=title,
        markdown_content=markdown,
        verifier_result_json=verifier_result,
    )
    session.add(report)
    record_event(
        session,
        event_type="report.created",
        message="Analysis report snapshot created",
        project_id=project.id,
        payload={"run_id": run.id, "report_id": report.id, "verifier_passed": verifier_result["passed"]},
    )
    return report


def create_intelligent_report_snapshot(
    session: Session,
    project: Project,
    *,
    run_id: str | None = None,
) -> ReportSnapshot:
    """生成确定性报告，再尝试追加受证据约束的 LLM 解读段落。"""

    report = create_report_snapshot(session, project, run_id=run_id)
    session.flush()
    try:
        run = session.get(AnalysisRun, report.run_id)
        if run is None:
            raise LookupError("Run not found")

        package = build_analysis_package(session, run)
        inventory = build_inventory(session, project)
        evidence_ids = [
            str(evidence.id).strip()
            for evidence in package["evidence_items"]
            if str(evidence.id).strip()
        ]
        output_ref = f"llm_report_artifact:{report.id}"
        messages = build_llm_report_messages(project, package, inventory, evidence_ids)
        result = LLMRouter(session).chat(
            task_type="report",
            project_id=project.id,
            run_id=run.id,
            report_id=report.id,
            input_refs=evidence_ids,
            output_ref=output_ref,
            required=False,
            messages=messages,
        )
        call_id = result.get("call_id")
        llm_status = str(result.get("status") or "failed")

        if llm_status != "succeeded":
            try_add_llm_report_artifact(
                session,
                project_id=project.id,
                run_id=run.id,
                report_id=report.id,
                call_id=call_id,
                status=llm_status,
                content_json={
                    "llm_status": llm_status,
                    "error_message": result.get("error_message"),
                },
                validation_result_json={
                    "accepted": False,
                    "messages": [f"LLM report generation returned status `{llm_status}`"],
                },
            )
            return report

        raw_text = str(result.get("text") or "")
        payload = extract_json_object(raw_text)
    except Exception as exc:
        try_add_llm_report_artifact(
            session,
            project_id=project.id,
            run_id=report.run_id,
            report_id=report.id,
            call_id=None,
            status="validation_failed" if isinstance(exc, (json.JSONDecodeError, ValueError)) else "failed",
            content_json={"error_message": str(exc)},
            validation_result_json={
                "accepted": False,
                "messages": [f"LLM report enhancement failed: {exc}"],
            },
        )
        return report

    try:
        validation = validate_llm_report_sections(payload, allowed_evidence_refs=set(evidence_ids))
        if not validation.accepted:
            try_add_llm_report_artifact(
                session,
                project_id=project.id,
                run_id=run.id,
                report_id=report.id,
                call_id=call_id,
                status="rejected",
                content_json=payload,
                validation_result_json={
                    "accepted": False,
                    "messages": validation.messages,
                },
            )
            return report
    except Exception as exc:
        try_add_llm_report_artifact(
            session,
            project_id=project.id,
            run_id=report.run_id,
            report_id=report.id,
            call_id=None,
            status="validation_failed",
            content_json={"error_message": str(exc)},
            validation_result_json={
                "accepted": False,
                "messages": [f"LLM report validation failed: {exc}"],
            },
        )
        return report

    try:
        # LLM 段落只是 advisory：只能追加到 Markdown，不覆盖确定性的标题、状态或 verifier 结果。
        enhanced_markdown = render_llm_sections_markdown(report.markdown_content, validation.normalized or {})
        try_add_llm_report_artifact(
            session,
            project_id=project.id,
            run_id=run.id,
            report_id=report.id,
            call_id=call_id,
            status="accepted",
            content_json=payload,
            validation_result_json={
                "accepted": True,
                "messages": validation.messages,
                "normalized": validation.normalized,
            },
        )
        report.markdown_content = enhanced_markdown
    except Exception as exc:
        try_add_llm_report_artifact(
            session,
            project_id=project.id,
            run_id=report.run_id,
            report_id=report.id,
            call_id=None,
            status="failed",
            content_json={"error_message": str(exc)},
            validation_result_json={
                "accepted": False,
                "messages": [f"LLM report render failed: {exc}"],
            },
        )
        return report
    return report


def add_llm_report_artifact(
    session: Session,
    *,
    project_id: str,
    run_id: str,
    report_id: str,
    call_id: Any,
    status: str,
    content_json: dict[str, Any],
    validation_result_json: dict[str, Any],
) -> LLMReportArtifact:
    artifact = LLMReportArtifact(
        project_id=project_id,
        run_id=run_id,
        report_id=report_id,
        call_id=str(call_id) if call_id else None,
        status=status,
        content_json=content_json,
        validation_result_json=validation_result_json,
    )
    session.add(artifact)
    session.flush()
    return artifact


def try_add_llm_report_artifact(
    session: Session,
    *,
    project_id: str,
    run_id: str,
    report_id: str,
    call_id: Any,
    status: str,
    content_json: dict[str, Any],
    validation_result_json: dict[str, Any],
) -> LLMReportArtifact | None:
    """Best-effort 记录 LLM 报告增强产物，避免 artifact flush 失败污染外层事务。"""

    try:
        with session.begin_nested():
            return add_llm_report_artifact(
                session,
                project_id=project_id,
                run_id=run_id,
                report_id=report_id,
                call_id=call_id,
                status=status,
                content_json=content_json,
                validation_result_json=validation_result_json,
            )
    except Exception:
        return None


def build_llm_report_messages(
    project: Project,
    package: dict[str, Any],
    inventory: dict[str, Any],
    evidence_ids: list[str],
) -> list[dict[str, Any]]:
    """构造紧凑上下文，要求 LLM 只返回 JSON 且只引用允许的证据 ID。"""

    context = {
        "project": {
            "id": project.id,
            "name": project.name,
            "topic": project.topic,
            "analysis_goal": project.analysis_goal,
            "research_profile": package.get("research_profile"),
        },
        "research_profile": package.get("profile_summary"),
        "allowed_evidence_refs": evidence_ids,
        "evidence_items": [
            {
                "id": evidence.id,
                "title": evidence.title,
                "summary": evidence.summary,
                "reference": evidence.reference,
            }
            for evidence in package["evidence_items"]
        ],
        "findings": [
            {
                "title": finding.title,
                "summary": finding.summary,
                "impact_level": finding.impact_level,
                "evidence_refs": finding.evidence_refs_json,
            }
            for finding in package["findings"]
        ],
        "risks": [
            {
                "title": risk.title,
                "summary": risk.summary,
                "severity": risk.severity,
                "mitigation": risk.mitigation,
                "evidence_refs": risk.evidence_refs_json,
            }
            for risk in package["risks"]
        ],
        "recommendations": [
            {
                "summary": recommendation.summary,
                "rationale": recommendation.rationale,
                "confidence": recommendation.confidence,
                "next_steps": recommendation.next_steps_json,
            }
            for recommendation in package["recommendations"]
        ],
        "inventory": {
            "sources": [
                {
                    "title": snapshot.title,
                    "source_type": snapshot.source_type,
                    "status": snapshot.status,
                    "content_excerpt": snapshot.content_excerpt,
                }
                for snapshot in inventory["snapshots"]
            ],
            "source_bundles": [
                {
                    "id": bundle["id"],
                    "name": bundle["name"],
                    "status": bundle["status"],
                    "item_count": bundle["item_count"],
                    "collected_count": bundle["collected_count"],
                    "failed_count": bundle["failed_count"],
                    "pending_count": bundle["pending_count"],
                }
                for bundle in inventory.get("source_bundles", [])
            ],
            "codebase_maps": [
                {
                    "root_label": codebase_map.root_label,
                    "tech_stack": codebase_map.tech_stack_json,
                    "entrypoints": codebase_map.entrypoint_files_json,
                    "tests": codebase_map.test_files_json,
                }
                for codebase_map in inventory["codebase_maps"]
            ],
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "你是工程调研报告编辑。只能基于提供的 evidence id 做解释，"
                "不得新增事实。只返回 JSON object。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "instruction": (
                        "返回格式：{executive_summary: string, sections: "
                        "[{heading: string, body: string, evidence_refs: string[]}]}"
                    ),
                    "context": context,
                },
                ensure_ascii=False,
            ),
        },
    ]


def review_report_snapshot(
    session: Session,
    project: Project,
    report: ReportSnapshot,
    *,
    decision: str,
    comment: str | None,
) -> ReportSnapshot:
    if decision not in REVIEW_STATUS_BY_DECISION:
        raise ValueError(f"Unsupported review decision `{decision}`")
    review_comment = normalized_review_comment(decision, comment)
    report_status, project_status = REVIEW_STATUS_BY_DECISION[decision]
    report.status = report_status
    report.review_decision = decision
    report.review_comment = review_comment
    report.reviewed_at = utc_now()
    project.status = project_status
    feedback_question: Question | None = None
    followup_run: AnalysisRun | None = None
    followup_job = None
    if decision in FOLLOWUP_REVIEW_DECISIONS:
        feedback_question = create_review_feedback_question(
            session,
            project=project,
            report=report,
            decision=decision,
            comment=review_comment or "",
        )
        record_event(
            session,
            event_type="report.review_feedback.created",
            message="Report review feedback recorded for next Loop run",
            project_id=project.id,
            payload={
                "report_id": report.id,
                "question_id": feedback_question.id,
                "decision": decision,
            },
        )
        followup_run, followup_job = enqueue_run(
            session,
            project,
            job_type="start",
            payload={
                "source": "report_review",
                "report_id": report.id,
                "feedback_question_id": feedback_question.id,
            },
        )
    record_event(
        session,
        event_type="report.reviewed",
        message="Analysis report reviewed",
        project_id=project.id,
        payload={
            "report_id": report.id,
            "decision": decision,
            "feedback_question_id": feedback_question.id if feedback_question else None,
            "followup_run_id": followup_run.id if followup_run else None,
            "followup_job_id": followup_job.id if followup_job else None,
        },
    )
    return report


def normalized_review_comment(decision: str, comment: str | None) -> str | None:
    """规范化人工评审备注。

    通过类评审可以没有备注；驳回和要求补充会驱动下一轮 Loop，必须给出
    明确反馈，否则系统只知道“继续”，不知道要修正什么。
    """

    clean_comment = (comment or "").strip()
    if decision in FOLLOWUP_REVIEW_DECISIONS and not clean_comment:
        raise ValueError("Rejected or needs_more reviews require a feedback comment")
    return clean_comment or None


def create_review_feedback_question(
    session: Session,
    *,
    project: Project,
    report: ReportSnapshot,
    decision: str,
    comment: str,
) -> Question:
    """把报告评审意见固化为已回答问题，供下一轮上下文构建读取。"""

    question = Question(
        project_id=project.id,
        run_id=report.run_id,
        prompt="人工评审反馈需要进入下一轮 Loop",
        reason="用户在报告评审中要求系统补充或重做部分分析。",
        impact="high" if decision == "rejected" else "medium",
        status="answered",
        answer_text=comment,
        answered_at=utc_now(),
        metadata_json={
            "source": "report_review",
            "decision": decision,
            "report_id": report.id,
        },
    )
    session.add(question)
    session.flush()
    return question


def get_latest_report(session: Session, project: Project) -> ReportSnapshot | None:
    return session.scalar(
        select(ReportSnapshot)
        .where(ReportSnapshot.project_id == project.id)
        .order_by(ReportSnapshot.created_at.desc(), ReportSnapshot.id.desc())
    )


def get_run_for_report(session: Session, project: Project, run_id: str | None) -> AnalysisRun:
    if run_id:
        run = session.get(AnalysisRun, run_id)
        if run is None or run.project_id != project.id:
            raise LookupError("Run not found")
        return run

    run = session.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.project_id == project.id)
        .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
    )
    if run is None:
        raise LookupError("No analysis run exists for this project")
    return run


def build_analysis_package(session: Session, run: AnalysisRun) -> dict[str, Any]:
    project = session.get(Project, run.project_id)
    profile = get_research_profile(project.research_profile if project is not None else None)
    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "research_profile": profile.key,
        }
        if project is not None
        else None,
        "research_profile": profile.key,
        "profile_summary": {
            "key": profile.key,
            "label": profile.label,
            "description": profile.description,
            "checklist": list(profile.checklist),
            "report_sections": list(profile.report_sections),
            "required_evidence_types": list(profile.required_evidence_types),
        },
        "run": run,
        "turns": list_by_run(session, LoopTurn, run.id),
        "evidence_items": list_by_run(session, EvidenceItem, run.id),
        "findings": list_by_run(session, Finding, run.id),
        "risks": list_by_run(session, Risk, run.id),
        "questions": list_by_run(session, Question, run.id),
        "recommendations": list_by_run(session, Recommendation, run.id),
        "quality_scores": list_by_run(session, QualityScore, run.id),
    }


def build_inventory(session: Session, project: Project) -> dict[str, Any]:
    snapshots = list(
        session.scalars(
            select(SourceSnapshot)
            .where(SourceSnapshot.project_id == project.id)
            .order_by(SourceSnapshot.created_at.asc(), SourceSnapshot.id.asc())
        ).all()
    )
    codebase_maps = list(
        session.scalars(
            select(CodebaseMap)
            .where(CodebaseMap.project_id == project.id)
            .order_by(CodebaseMap.created_at.asc(), CodebaseMap.id.asc())
        ).all()
    )
    source_bundles = list(
        session.scalars(
            select(SourceBundle)
            .options(selectinload(SourceBundle.items))
            .where(SourceBundle.project_id == project.id)
            .order_by(SourceBundle.created_at.asc(), SourceBundle.id.asc())
        ).all()
    )
    return {
        "snapshots": snapshots,
        "codebase_maps": codebase_maps,
        "source_bundles": [
            {
                "id": bundle.id,
                "name": bundle.name,
                "status": bundle.status,
                "item_count": len(bundle.items),
                "collected_count": count_bundle_items_by_status(bundle, "collected"),
                "failed_count": count_bundle_items_by_status(bundle, "failed"),
                "pending_count": count_bundle_items_by_status(bundle, "pending"),
            }
            for bundle in source_bundles
        ],
    }


def count_bundle_items_by_status(bundle: SourceBundle, status: str) -> int:
    return sum(1 for item in bundle.items if item.status == status)


def list_by_run(session: Session, model, run_id: str):
    if model is LoopTurn:
        order_by = (LoopTurn.turn_index.asc(), LoopTurn.id.asc())
    else:
        order_by = (model.created_at.asc(), model.id.asc())
    return list(session.scalars(select(model).where(model.run_id == run_id).order_by(*order_by)).all())
