from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import CodebaseMap, Project, Question, SourceBundle, SourceBundleItem, SourceSnapshot
from app.research.profiles import get_research_profile


def build_analysis_context(session: Session, project_id: str) -> dict:
    """从已持久化事实构建上下文包，不重新读取文件或访问网络。

    Args: session 为数据库会话；project_id 为非空项目 ID。
    Returns: 可序列化的 context 字典。
    Raises: 项目不存在时抛出 ValueError；数据库查询异常向上抛出。
    副作用：仅读取数据库；由 LangGraph `build_context_node` 调用。
    """

    # project 提供项目配置、状态和 research profile，是后续分析上下文的根对象。
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError("Project not found")

    # snapshots 是来源采集后的事实快照，不在这里重新读取本地文件或访问网络。
    snapshots = list(
        session.scalars(
            select(SourceSnapshot)
            .where(SourceSnapshot.project_id == project.id)
            .order_by(SourceSnapshot.created_at.asc(), SourceSnapshot.id.asc())
        ).all()
    )
    # codebase_maps 是代码库扫描摘要，包含技术栈、入口、依赖和测试线索。
    codebase_maps = list(
        session.scalars(
            select(CodebaseMap)
            .where(CodebaseMap.project_id == project.id)
            .order_by(CodebaseMap.created_at.asc(), CodebaseMap.id.asc())
        ).all()
    )
    # answered_questions 包含用户已经回答的问题，也用于筛选报告审核反馈。
    answered_questions = list(
        session.scalars(
            select(Question)
            .where(Question.project_id == project.id, Question.status == "answered")
            .order_by(Question.created_at.asc(), Question.id.asc())
        ).all()
    )
    # source_bundle_rows 是资料包聚合查询结果，每行携带条目数量而非完整 ORM 条目。
    source_bundle_rows = list(
        session.execute(
            select(
                SourceBundle.id,
                SourceBundle.name,
                SourceBundle.status,
                func.count(SourceBundleItem.id).label("item_count"),
            )
            .outerjoin(SourceBundleItem, SourceBundleItem.bundle_id == SourceBundle.id)
            .where(SourceBundle.project_id == project.id)
            .group_by(SourceBundle.id, SourceBundle.name, SourceBundle.status, SourceBundle.created_at)
            .order_by(SourceBundle.created_at.asc(), SourceBundle.id.asc())
        ).all()
    )
    # 只有 metadata.source=report_review 的已回答问题才作为人工审核证据进入分析。
    review_feedback_questions = [
        question
        for question in answered_questions
        if question.metadata_json.get("source") == "report_review"
    ]
    profile = get_research_profile(project.research_profile)
    # 返回可直接放入 graph state 的 JSON 结构，避免把 ORM 对象写入 checkpoint。
    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "topic": project.topic,
            "analysis_goal": project.analysis_goal,
            "status": project.status,
            "research_profile": profile.key,
            "max_turns": project.max_turns,
        },
        "research_profile": {
            "key": profile.key,
            "label": profile.label,
            "checklist": list(profile.checklist),
            "verifier_checks": list(profile.verifier_checks),
        },
        "source_bundles": [
            {
                "id": row.id,
                "name": row.name,
                "status": row.status,
                "item_count": int(row.item_count or 0),
            }
            for row in source_bundle_rows
        ],
        "snapshots": [
            {
                "id": snapshot.id,
                "source_id": snapshot.source_id,
                "source_type": snapshot.source_type,
                "status": snapshot.status,
                "title": snapshot.title,
                "content_excerpt": snapshot.content_excerpt,
                "metadata": snapshot.metadata_json,
                "skipped_items": snapshot.skipped_items_json,
            }
            for snapshot in snapshots
        ],
        "codebase_maps": [
            {
                "id": codebase_map.id,
                "source_id": codebase_map.source_id,
                "root_label": codebase_map.root_label,
                "tech_stack": codebase_map.tech_stack_json,
                "dependency_files": codebase_map.dependency_files_json,
                "config_files": codebase_map.config_files_json,
                "test_files": codebase_map.test_files_json,
                "entrypoint_files": codebase_map.entrypoint_files_json,
                "readme_excerpt": codebase_map.readme_excerpt,
                "file_tree": codebase_map.file_tree_json,
                "skipped_items": codebase_map.skipped_items_json,
                "metadata": codebase_map.metadata_json,
            }
            for codebase_map in codebase_maps
        ],
        "review_feedback": [
            {
                "id": question.id,
                "run_id": question.run_id,
                "prompt": question.prompt,
                "answer_text": question.answer_text,
                "impact": question.impact,
                "metadata": question.metadata_json,
            }
            for question in review_feedback_questions
        ],
    }
