from types import SimpleNamespace

from app.reports.renderer import render_report_markdown
from app.verifiers.package import verify_analysis_package


def item(**kwargs):
    return SimpleNamespace(**kwargs)


def markdown_between(markdown: str, start_heading: str, end_heading: str) -> str:
    start = markdown.index(start_heading)
    end = markdown.index(end_heading)
    return markdown[start:end]


def complete_package():
    evidence = item(
        id="ev-1",
        title="Source inventory",
        summary="Repository has README, entrypoint, and dependency metadata.",
        evidence_type="codebase_map",
        reference="repo",
        confidence=90,
    )
    return {
        "run": item(id="run-1", status="ready_for_review", stop_reason="Quality target reached"),
        "turns": [
            item(turn_index=1, node_name="load_state", action="Load state", observation="Loaded project"),
            item(turn_index=2, node_name="verify_package", action="Verify", observation="Checks passed"),
        ],
        "evidence_items": [evidence],
        "findings": [
            item(
                id="finding-1",
                title="Runtime boundary is clear",
                summary="The project separates collected evidence from analysis output.",
                category="architecture",
                impact_level="high",
                confidence=86,
                evidence_refs_json=["ev-1"],
            )
        ],
        "risks": [
            item(
                id="risk-1",
                title="No enterprise auth yet",
                summary="The current slice remains local-user scoped.",
                severity="medium",
                mitigation="Keep write actions local until RBAC exists.",
                evidence_refs_json=["ev-1"],
            )
        ],
        "questions": [
            item(
                id="question-1",
                prompt="Which buyer persona matters first?",
                reason="Positioning affects future integrations.",
                impact="medium",
                status="answered",
                answer_text="Personal assistant first.",
            )
        ],
        "recommendations": [
            item(
                id="rec-1",
                summary="Continue to M4 review.",
                rationale="Package has evidence, risks, and an explicit stop state.",
                confidence=82,
                next_steps_json=["Generate report", "Review findings"],
            )
        ],
        "quality_scores": [
            item(
                overall_score=88,
                structure_completeness=90,
                evidence_coverage=90,
                codebase_coverage=80,
                risk_transparency=90,
                question_resolution=80,
                recommendation_confidence=85,
                reasons_json=["Complete package"],
                next_actions_json=[],
            )
        ],
    }


def complete_inventory():
    return {
        "snapshots": [
            item(
                id="snapshot-1",
                source_type="local_directory",
                status="collected",
                title="repo",
                content_excerpt="README excerpt",
            )
        ],
        "codebase_maps": [
            item(
                id="map-1",
                root_label="repo",
                tech_stack_json=["python"],
                dependency_files_json=["pyproject.toml"],
                config_files_json=[],
                test_files_json=["tests/test_app.py"],
                entrypoint_files_json=["app.py"],
                readme_excerpt="README excerpt",
            )
        ],
    }


def test_complete_package_passes_verification():
    result = verify_analysis_package(complete_package(), complete_inventory())

    assert result["passed"] is True
    assert result["risk_level"] == "low"
    assert result["blocking_reasons"] == []
    assert {check["name"] for check in result["checks"]} >= {
        "structure_completeness",
        "evidence_coverage",
        "codebase_coverage",
        "risk_transparency",
        "question_resolution",
    }


def test_finding_without_evidence_blocks_report_approval():
    package = complete_package()
    package["findings"][0].evidence_refs_json = []

    result = verify_analysis_package(package, complete_inventory())

    assert result["passed"] is False
    assert result["risk_level"] == "high"
    assert "发现「Runtime boundary is clear」缺少证据引用。" in result["blocking_reasons"]


def test_open_high_impact_question_blocks_report_approval():
    package = complete_package()
    package["questions"][0].impact = "high"
    package["questions"][0].status = "open"

    result = verify_analysis_package(package, complete_inventory())

    assert result["passed"] is False
    assert result["risk_level"] == "high"
    assert "高影响问题仍未回答：Which buyer persona matters first?" in result["blocking_reasons"]


def test_codebase_without_tests_is_warning_not_blocker():
    inventory = complete_inventory()
    inventory["codebase_maps"][0].test_files_json = []

    result = verify_analysis_package(complete_package(), inventory)

    assert result["passed"] is True
    assert result["risk_level"] == "medium"
    warning_checks = [check for check in result["checks"] if check["severity"] == "warning"]
    assert any(check["name"] == "codebase_coverage" for check in warning_checks)


def test_missing_source_snapshots_blocks_verification():
    result = verify_analysis_package(complete_package(), {"snapshots": [], "codebase_maps": []})

    assert result["passed"] is False
    assert result["risk_level"] == "high"
    assert "没有可用的输入源采集快照。" in result["blocking_reasons"]


def test_render_report_markdown_includes_verifier_findings_and_evidence():
    project = item(
        id="project-1",
        name="Loop Engineering Runtime",
        topic="Analyze a loop runtime project",
        analysis_goal="Decide whether to proceed to productization.",
        status="ready_for_review",
    )
    package = complete_package()
    inventory = complete_inventory()
    verifier_result = verify_analysis_package(package, inventory)

    markdown = render_report_markdown(project, package, inventory, verifier_result)

    assert "# Loop Engineering Runtime 项目调研报告" in markdown
    assert "## 校验摘要" in markdown
    assert "Runtime boundary is clear" in markdown
    assert "Source inventory" in markdown
    assert "No enterprise auth yet" in markdown
    assert "Continue to M4 review." in markdown
    assert "## 调研过程时间线" in markdown


def test_render_report_markdown_includes_v3_enhanced_sections():
    project = item(
        id="project-1",
        name="Loop Engineering Runtime",
        topic="Analyze a loop runtime project",
        analysis_goal="Decide whether to proceed to productization.",
        status="ready_for_review",
    )
    package = complete_package()
    inventory = complete_inventory()
    verifier_result = {
        "passed": True,
        "risk_level": "low",
        "summary": "报告已通过确定性校验，暂无风险提示。",
        "checks": [],
        "blocking_reasons": [],
        "warning_reasons": [],
        "llm_assisted": {
            "status": "succeeded",
            "summary": "建议更明确。",
            "issues": [{"severity": "warning", "message": "证据仍需抽样复核。"}],
        },
    }

    markdown = render_report_markdown(project, package, inventory, verifier_result)

    headings = ["## 校验摘要", "## 执行摘要", "## 关键证据链", "## 智能复核意见", "### 校验项"]
    heading_positions = [markdown.index(heading) for heading in headings]
    assert heading_positions == sorted(heading_positions)

    executive_summary = markdown_between(markdown, "## 执行摘要", "## 关键证据链")
    assert "- 发现数量：`1`" in executive_summary
    assert "- 风险数量：`1`" in executive_summary
    assert "- 校验结论：是，风险等级：低；报告已通过确定性校验，暂无风险提示。" in executive_summary

    evidence_chain = markdown_between(markdown, "## 关键证据链", "## 智能复核意见")
    assert "- Runtime boundary is clear" in evidence_chain
    assert "`ev-1` Source inventory：Repository has README, entrypoint, and dependency metadata." in evidence_chain

    llm_review = markdown_between(markdown, "## 智能复核意见", "### 校验项")
    assert "- 状态：`succeeded`" in llm_review
    assert "- 摘要：建议更明确。" in llm_review
    assert "  - `warning` 证据仍需抽样复核。" in llm_review


def test_render_report_markdown_handles_empty_llm_assisted_result_safely():
    project = item(
        id="project-1",
        name="Loop Engineering Runtime",
        topic="Analyze a loop runtime project",
        analysis_goal="Decide whether to proceed to productization.",
        status="ready_for_review",
    )
    package = complete_package()
    inventory = complete_inventory()
    base_verifier_result = {
        "passed": True,
        "risk_level": "low",
        "summary": "报告已通过确定性校验，暂无风险提示。",
        "checks": [],
        "blocking_reasons": [],
        "warning_reasons": [],
    }

    missing_markdown = render_report_markdown(project, package, inventory, base_verifier_result)
    invalid_markdown = render_report_markdown(
        project,
        package,
        inventory,
        base_verifier_result | {"llm_assisted": "unsafe"},
    )
    empty_issue_markdown = render_report_markdown(
        project,
        package,
        inventory,
        base_verifier_result
        | {
            "llm_assisted": {
                "status": "succeeded",
                "summary": "无有效问题。",
                "issues": [{"severity": "warning", "message": ""}, {"severity": "error"}],
            }
        },
    )

    assert "- 状态：`skipped`" in markdown_between(missing_markdown, "## 智能复核意见", "### 校验项")
    assert "- 状态：`skipped`" in markdown_between(invalid_markdown, "## 智能复核意见", "### 校验项")
    empty_issue_review = markdown_between(empty_issue_markdown, "## 智能复核意见", "### 校验项")
    assert "- 问题：暂无。" in empty_issue_review
    assert "\n- 问题：\n" not in empty_issue_review
