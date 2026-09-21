from app.db.models import SourceBundle, SourceBundleItem
from app.services.reports import build_llm_report_messages, build_inventory, build_analysis_package, create_report_snapshot
from app.evaluations.runner import run_evaluations

from tests.test_llm_report_generator import create_reportable_project, session_scope


def test_report_snapshot_renders_project_research_package_sections():
    with session_scope() as session:
        project, run, _ = create_reportable_project(session)

        report = create_report_snapshot(session, project, run_id=run.id)

    markdown = report.markdown_content
    assert markdown.startswith("# LLM report project 项目调研报告")
    assert "# LLM report project 分析报告" not in markdown.splitlines()[0]
    assert "## 工程接手指南" in markdown
    assert "## 启动与运行线索" in markdown
    assert "## 架构地图" in markdown
    assert "## 证据索引" in markdown
    assert "## 后续阅读路径" in markdown


def test_report_snapshot_renders_profile_and_bundle_sections_for_teaching_breakdown():
    with session_scope() as session:
        project, run, _ = create_reportable_project(session)
        project.research_profile = "teaching_breakdown"
        bundle = SourceBundle(project_id=project.id, name="课程资料包", status="partial")
        session.add(bundle)
        session.flush()
        session.add_all(
            [
                SourceBundleItem(
                    bundle_id=bundle.id,
                    project_id=project.id,
                    input_type="markdown_file",
                    raw_value="docs/course-outline.md",
                    status="collected",
                ),
                SourceBundleItem(
                    bundle_id=bundle.id,
                    project_id=project.id,
                    input_type="github_repo",
                    raw_value="https://github.com/example/course-demo",
                    status="failed",
                    error_message="Network disabled",
                ),
            ]
        )

        package = build_analysis_package(session, run)
        inventory = build_inventory(session, project)
        report = create_report_snapshot(session, project, run_id=run.id)

    assert package["profile_summary"]["key"] == "teaching_breakdown"
    assert "课程章节建议" in package["profile_summary"]["report_sections"]
    assert inventory["source_bundles"][0]["name"] == "课程资料包"
    assert inventory["source_bundles"][0]["item_count"] == 2

    markdown = report.markdown_content
    assert "## 调研 Profile" in markdown
    assert "- 档位：教学拆解 (`teaching_breakdown`)" in markdown
    assert "## 资料包摘要" in markdown
    assert "课程资料包" in markdown
    assert "## 课程章节建议" in markdown
    assert "## 关键代码讲解顺序" in markdown
    assert "## 练习任务" in markdown


def test_llm_report_prompt_uses_engineering_research_role():
    with session_scope() as session:
        project, run, _ = create_reportable_project(session)
        project.research_profile = "teaching_breakdown"
        bundle = SourceBundle(project_id=project.id, name="课程资料包", status="collected")
        session.add(bundle)
        session.flush()
        session.add(
            SourceBundleItem(
                bundle_id=bundle.id,
                project_id=project.id,
                input_type="markdown_file",
                raw_value="docs/course-outline.md",
                status="collected",
            )
        )
        package = build_analysis_package(session, run)
        inventory = build_inventory(session, project)
        evidence_ids = [str(evidence.id) for evidence in package["evidence_items"]]

        messages = build_llm_report_messages(project, package, inventory, evidence_ids)

    system_prompt = messages[0]["content"]
    user_prompt = messages[1]["content"]
    assert "工程调研报告编辑" in system_prompt
    assert "代码库巡检报告编辑" not in system_prompt
    assert '"research_profile"' in user_prompt
    assert '"teaching_breakdown"' in user_prompt
    assert '"source_bundles"' in user_prompt
    assert "课程资料包" in user_prompt


def test_evaluation_harness_records_project_research_package_semantic_check(tmp_path):
    with session_scope() as session:
        run = run_evaluations(session, workspace_root=tmp_path)

    assert run.status == "succeeded"
    assert run.summary_json["total"] == 8
    assert run.summary_json["by_type"]["scan_regression"]["total"] == 3
    assert run.summary_json["by_type"]["profile_benchmark"]["total"] == 5

    scan_results = [
        result
        for result in run.case_results_json
        if result["case_type"] == "scan_regression"
    ]
    profile_results = [
        result
        for result in run.case_results_json
        if result["case_type"] == "profile_benchmark"
    ]
    assert len(scan_results) == 3
    assert len(profile_results) == 5

    for result in scan_results:
        checks = {check["name"]: check for check in result["checks"]}
        assert checks["project_research_package_sections"]["passed"] is True

    for result in profile_results:
        check_names = {check["name"] for check in result["checks"]}
        assert "report_sections_present" in check_names
        assert result["research_profile"]["key"] in run.summary_json["profiles"]
        assert result["quality_summary"]["overall_score"] >= 70
