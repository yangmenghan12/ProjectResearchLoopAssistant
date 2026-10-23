from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.models import Project, ReportSnapshot
from app.db.session import get_session
from app.reports.diff import build_report_package_snapshot, diff_report_snapshots
from app.reports.pdf import render_markdown_pdf
from app.schemas.report import (
    ReportCreate,
    ReportDiffRead,
    ReportMarkdownRead,
    ReportReviewCreate,
    ReportSnapshotRead,
)
from app.services.reports import (
    create_intelligent_report_snapshot,
    create_report_snapshot,
    get_latest_report,
    review_report_snapshot,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["reports"])


@router.post("/reports", response_model=ReportSnapshotRead, status_code=status.HTTP_201_CREATED)
def create_report(
    project_id: str,
    payload: ReportCreate | None = Body(default=None),
    session: Session = Depends(get_session),
) -> ReportSnapshot:
    """从指定或最新 run 生成确定性 Project Research Package 报告。

    Args: project_id 为项目 ID；payload 可选并可带 run_id；session 为数据库会话。
    Returns: 已提交并刷新的 ReportSnapshot。
    Raises: 资源不存在返回 404；前置条件不满足返回 409。
    副作用：读取分析产物并写入报告快照；由报告生成接口调用。
    """
    project = get_project_or_404(session, project_id)
    try:
        report = create_report_snapshot(session, project, run_id=payload.run_id if payload else None)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    session.refresh(report)
    return report


@router.post("/reports/intelligent", response_model=ReportSnapshotRead, status_code=status.HTTP_201_CREATED)
def create_intelligent_report(
    project_id: str,
    payload: ReportCreate | None = Body(default=None),
    session: Session = Depends(get_session),
) -> ReportSnapshot:
    """生成确定性报告，并在配置可用时追加 LLM 报告增强内容。

    Args: project_id 为项目 ID；payload 可选并可带 run_id；session 为数据库会话。
    Returns: 已提交并刷新的 ReportSnapshot。
    Raises: 资源不存在返回 404；报告前置条件不满足返回 409。
    副作用：可能发起外部 LLM IO，并写入报告与 LLM 账本。
    """
    project = get_project_or_404(session, project_id)
    try:
        report = create_intelligent_report_snapshot(session, project, run_id=payload.run_id if payload else None)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    session.refresh(report)
    return report


@router.get("/reports/latest", response_model=ReportSnapshotRead)
def get_latest_project_report(project_id: str, session: Session = Depends(get_session)) -> ReportSnapshot:
    """返回项目最新报告，供工作台默认展示。

    Args: project_id 为项目 ID；session 为数据库会话。
    Returns: 最新的 ReportSnapshot。
    Raises: 项目或报告不存在时返回 HTTP 404。
    副作用：仅读取数据库；由报告页面调用。
    """
    project = get_project_or_404(session, project_id)
    report = get_latest_report(session, project)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.get("/reports/{report_id}", response_model=ReportSnapshotRead)
def get_report(project_id: str, report_id: str, session: Session = Depends(get_session)) -> ReportSnapshot:
    """读取属于指定项目的单份报告，避免跨项目访问。

    Args: project_id/report_id 为资源 ID；session 为数据库会话。
    Returns: 匹配的 ReportSnapshot。
    Raises: 报告不存在或归属不匹配时返回 HTTP 404。
    副作用：仅读取数据库；由报告详情页面调用。
    """
    return get_report_or_404(session, project_id, report_id)


@router.get("/reports/{report_id}/markdown", response_model=ReportMarkdownRead)
def get_report_markdown(project_id: str, report_id: str, session: Session = Depends(get_session)) -> dict[str, str]:
    """返回报告 Markdown 正文，供预览和客户端导出使用。

    Args: project_id/report_id 为资源 ID；session 为数据库会话。
    Returns: `{"markdown": str}`。
    Raises: 报告不存在或归属不匹配时返回 HTTP 404。
    副作用：仅读取数据库；由报告预览接口调用。
    """
    report = get_report_or_404(session, project_id, report_id)
    return {"markdown": report.markdown_content}


@router.get("/reports/{report_id}/export.pdf")
def export_report_pdf(project_id: str, report_id: str, session: Session = Depends(get_session)) -> Response:
    """把报告 Markdown 渲染为 PDF 字节流并返回下载响应。

    Args: project_id/report_id 为资源 ID；session 为数据库会话。
    Returns: media type 为 `application/pdf` 的 Response。
    Raises: 报告不存在时返回 404；PDF 渲染异常向上抛出。
    副作用：读取数据库并执行本地 PDF 渲染；由 PDF 导出接口调用。
    """
    report = get_report_or_404(session, project_id, report_id)
    pdf_content = render_markdown_pdf(title=report.title, markdown=report.markdown_content)
    return Response(
        content=pdf_content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report.id}.pdf"'},
    )


@router.get("/reports/{report_id}/export.md")
def export_report_markdown(project_id: str, report_id: str, session: Session = Depends(get_session)) -> Response:
    """以附件响应返回报告 Markdown 原文。

    Args: project_id/report_id 为资源 ID；session 为数据库会话。
    Returns: media type 为 `text/markdown` 的 Response。
    Raises: 报告不存在或归属不匹配时返回 HTTP 404。
    副作用：仅读取数据库；由 Markdown 导出接口调用。
    """
    report = get_report_or_404(session, project_id, report_id)
    return Response(
        content=report.markdown_content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{report.id}.md"'},
    )


@router.get("/reports/{report_id}/diff", response_model=ReportDiffRead)
def get_report_diff(
    project_id: str,
    report_id: str,
    base_report_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """比较目标报告和基准报告的结构化差异。

    Args: project_id/report_id 为目标资源 ID；base_report_id 为基准报告 ID。
    Returns: 包含报告 ID 和 diff 内容的字典。
    Raises: 任一报告不存在或归属不匹配时返回 HTTP 404。
    副作用：仅读取数据库并执行内存中的 diff；由报告对比接口调用。
    """
    get_project_or_404(session, project_id)
    target_report = get_report_or_404(session, project_id, report_id)
    base_report = get_report_or_404(session, project_id, base_report_id)
    # 按报告创建时间截断 run 产物，避免评审反馈等后写入数据污染历史版本 diff。
    base_package = build_report_package_snapshot(session, base_report)
    target_package = build_report_package_snapshot(session, target_report)
    return {
        "base_report_id": base_report.id,
        "target_report_id": target_report.id,
        "diff": diff_report_snapshots(base_package, target_package),
    }


@router.post("/reports/{report_id}/review", response_model=ReportSnapshotRead)
def review_report(
    project_id: str,
    report_id: str,
    payload: ReportReviewCreate,
    session: Session = Depends(get_session),
) -> ReportSnapshot:
    """记录人工审核决定，并在需要补充时创建后续问题。

    Args: project_id/report_id 为资源 ID；payload 含审核决定和可选评论；session 为数据库会话。
    Returns: 已提交并刷新的 ReportSnapshot。
    Raises: 资源不存在返回 404；审核值无效返回 400。
    副作用：更新报告，可能写入问题和审计事件；由报告审核接口调用。
    """
    project = get_project_or_404(session, project_id)
    report = get_report_or_404(session, project_id, report_id)
    try:
        review_report_snapshot(
            session,
            project,
            report,
            decision=payload.decision,
            comment=payload.comment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    session.refresh(report)
    return report


def get_project_or_404(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def get_report_or_404(session: Session, project_id: str, report_id: str) -> ReportSnapshot:
    report = session.get(ReportSnapshot, report_id)
    if report is None or report.project_id != project_id:
        raise HTTPException(status_code=404, detail="Report not found")
    return report
