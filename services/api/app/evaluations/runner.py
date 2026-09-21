from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.orm import Session

from app.db.models import EvaluationRun, utc_now
from app.evaluations.cases import EvaluationCase, evaluation_cases, write_case_repo
from app.evaluations.profile_benchmark import evaluate_profile_benchmark
from app.reports.renderer import (
    PROFILE_AWARE_SECTION_HEADINGS,
    PROJECT_RESEARCH_SECTION_HEADINGS,
)
from app.research.profiles import list_research_profiles
from app.tools.code_scan_tools import scan_codebase

KNOWN_CASE_TYPES = {"scan_regression", "profile_benchmark"}


def _check(name: str, passed: bool, message: str) -> dict:
    return {
        "name": name,
        "passed": passed,
        "message": message,
    }


def _unknown_case_type_result(case: EvaluationCase) -> dict:
    checks = [
        _check(
            "known_case_type",
            False,
            f"未知 evaluation case type: {case.case_type}",
        )
    ]
    expected_check_names = set(case.expected_checks)
    actual_check_names = {check["name"] for check in checks}
    return {
        "case_id": case.case_id,
        "description": case.description,
        "case_type": case.case_type,
        "passed": False,
        "expected_checks": case.expected_checks,
        "missing_checks": sorted(expected_check_names - actual_check_names),
        "unexpected_checks": sorted(actual_check_names - expected_check_names),
        "checks": checks,
    }


def _scan_case(case_root: Path) -> dict:
    return scan_codebase(case_root, max_file_bytes=256 * 1024, excerpt_chars=500)


def _project_research_package_check() -> dict:
    required_sections = {
        "工程接手指南",
        "启动与运行线索",
        "架构地图",
        "证据索引",
        "后续阅读路径",
    }
    configured_sections = set(PROJECT_RESEARCH_SECTION_HEADINGS)
    missing_sections = sorted(required_sections - configured_sections)
    return _check(
        "project_research_package_sections",
        not missing_sections,
        (
            "项目调研包核心章节已纳入确定性报告渲染器。"
            if not missing_sections
            else f"项目调研包核心章节缺失: {', '.join(missing_sections)}"
        ),
    )


def _profile_aware_report_check() -> dict:
    required_static_sections = {"调研 Profile", "资料包摘要"}
    configured_static_sections = set(PROFILE_AWARE_SECTION_HEADINGS)
    missing_static_sections = sorted(
        required_static_sections - configured_static_sections
    )
    profiles_without_sections = [
        profile.key
        for profile in list_research_profiles()
        if not profile.report_sections
    ]
    teaching_profile = next(
        (
            profile
            for profile in list_research_profiles()
            if profile.key == "teaching_breakdown"
        ),
        None,
    )
    teaching_required = {"课程章节建议", "关键代码讲解顺序", "练习任务"}
    teaching_missing = (
        sorted(teaching_required - set(teaching_profile.report_sections))
        if teaching_profile is not None
        else sorted(teaching_required)
    )
    passed = (
        not missing_static_sections
        and not profiles_without_sections
        and not teaching_missing
    )
    messages = []
    if missing_static_sections:
        messages.append(
            f"缺少 profile-aware 固定章节: {', '.join(missing_static_sections)}"
        )
    if profiles_without_sections:
        messages.append(
            f"以下调研档位缺少动态报告章节: {', '.join(profiles_without_sections)}"
        )
    if teaching_missing:
        messages.append(f"教学拆解档位缺少章节: {', '.join(teaching_missing)}")
    return _check(
        "profile_aware_report_sections",
        passed,
        "调研档位和资料包摘要报告护栏已启用。" if passed else "；".join(messages),
    )


def _evaluate_scan_case(case: EvaluationCase, scan_result: dict) -> dict:
    file_paths = {
        item["path"] for item in scan_result["file_tree"] if item.get("kind") == "file"
    }
    skipped_paths = {item["path"] for item in scan_result["skipped_items"]}

    if case.case_id == "healthy-small":
        checks = [
            _check(
                "readme_present",
                scan_result["readme_excerpt"] is not None,
                "README 摘要已形成基础证据线索。",
            ),
            _check(
                "files_scanned",
                {"README.md", "app.py", "pyproject.toml"} <= file_paths,
                "扫描结果包含 README、入口文件和项目标记。",
            ),
            _check(
                "entrypoint_detected",
                "app.py" in scan_result["entrypoint_files"],
                "入口文件 app.py 被识别。",
            ),
        ]
    elif case.case_id == "missing-docs":
        checks = [
            _check(
                "readme_missing_detected",
                scan_result["readme_excerpt"] is None,
                "缺少 README 被记录为可验证发现，不导致 harness 失败。",
            ),
            _check(
                "files_scanned",
                "app.py" in file_paths and scan_result["metadata"]["total_files"] == 1,
                "即使没有 README，代码文件仍可被扫描。",
            ),
        ]
    elif case.case_id == "sensitive-skip":
        checks = [
            _check(
                "env_skipped",
                ".env" in skipped_paths,
                ".env 被敏感路径策略捕获到 skipped_items。",
            ),
            _check(
                "env_not_in_file_tree",
                ".env" not in file_paths,
                ".env 没有进入 file_tree，避免把敏感文件作为证据展开。",
            ),
            _check(
                "readme_present",
                scan_result["readme_excerpt"] is not None,
                "普通 README 仍可被读取。",
            ),
        ]
    else:
        checks = [
            _check(
                "known_case",
                False,
                f"未知 evaluation case: {case.case_id}",
            )
        ]

    checks.append(_project_research_package_check())
    checks.append(_profile_aware_report_check())

    expected_check_names = set(case.expected_checks)
    actual_check_names = {check["name"] for check in checks}
    missing_checks = sorted(expected_check_names - actual_check_names)
    unexpected_checks = sorted(actual_check_names - expected_check_names)
    check_names_match = not missing_checks and not unexpected_checks

    return {
        "case_id": case.case_id,
        "description": case.description,
        "case_type": case.case_type,
        "passed": all(check["passed"] for check in checks) and check_names_match,
        "expected_checks": case.expected_checks,
        "missing_checks": missing_checks,
        "unexpected_checks": unexpected_checks,
        "checks": checks,
    }


def build_summary(results: list[dict]) -> dict:
    passed = sum(1 for result in results if result["passed"])
    failed = len(results) - passed
    by_type: dict[str, dict[str, int]] = {}
    profiles: dict[str, dict[str, int]] = {}
    for result in results:
        case_type = result.get("case_type", "unknown")
        by_type.setdefault(case_type, {"total": 0, "passed": 0, "failed": 0})
        by_type[case_type]["total"] += 1
        by_type[case_type]["passed" if result["passed"] else "failed"] += 1
        profile = result.get("research_profile")
        if isinstance(profile, dict) and profile.get("key"):
            key = profile["key"]
            profiles.setdefault(key, {"total": 0, "passed": 0, "failed": 0})
            profiles[key]["total"] += 1
            profiles[key]["passed" if result["passed"] else "failed"] += 1
    return {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "by_type": by_type,
        "profiles": profiles,
    }


def run_evaluations(
    session: Session, *, workspace_root: Path | None = None
) -> EvaluationRun:
    workspace_context = (
        nullcontext(workspace_root)
        if workspace_root is not None
        else TemporaryDirectory(prefix="loop-evaluations-")
    )

    with workspace_context as active_workspace:
        root = Path(active_workspace).resolve()
        return _run_evaluations_in_workspace(session, root)


def _run_evaluations_in_workspace(session: Session, root: Path) -> EvaluationRun:
    root.mkdir(parents=True, exist_ok=True)

    run = EvaluationRun(status="running", summary_json={}, case_results_json=[])
    session.add(run)
    session.flush()

    results = []
    for case in evaluation_cases():
        if case.case_type not in KNOWN_CASE_TYPES:
            results.append(_unknown_case_type_result(case))
            continue

        # 每次运行都重建 fixture，保证 case 输入稳定，不被上一次本地运行污染。
        case_root = write_case_repo(root, case)
        scan_result = _scan_case(case_root)
        if case.case_type == "scan_regression":
            results.append(_evaluate_scan_case(case, scan_result))
        else:
            results.append(evaluate_profile_benchmark(case, scan_result))

    run.summary_json = build_summary(results)
    run.case_results_json = results
    run.status = "succeeded" if run.summary_json["failed"] == 0 else "failed"
    run.completed_at = utc_now()

    session.commit()
    session.refresh(run)
    return run
