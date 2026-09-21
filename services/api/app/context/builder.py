from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import CodebaseMap, Project, Question, SourceBundle, SourceBundleItem, SourceSnapshot
from app.research.profiles import get_research_profile


def build_analysis_context(session: Session, project_id: str) -> dict:
    """从已持久化事实构建上下文包，不重新读取文件或访问网络。"""

    project = session.get(Project, project_id)
    if project is None:
        raise ValueError("Project not found")

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
    answered_questions = list(
        session.scalars(
            select(Question)
            .where(Question.project_id == project.id, Question.status == "answered")
            .order_by(Question.created_at.asc(), Question.id.asc())
        ).all()
    )
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
    review_feedback_questions = [
        question
        for question in answered_questions
        if question.metadata_json.get("source") == "report_review"
    ]
    profile = get_research_profile(project.research_profile)
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
