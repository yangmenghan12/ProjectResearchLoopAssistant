from __future__ import annotations

from typing import Any

from app.reports.renderer import render_report_markdown
from app.research.profiles import get_research_profile
from app.verifiers.package import as_list, value_of, verify_analysis_package
from app.verifiers.quality import score_analysis_package


PROFILE_FINDING_CATEGORIES = {
    "quick_onboarding": ["reading_path"],
    "architecture_understanding": ["architecture_module_boundary"],
    "extension_evaluation": ["extension"],
    "teaching_breakdown": ["teaching_learning_path", "practice_exercise"],
    "delivery_acceptance": ["delivery_readiness"],
}

PROFILE_RISK_KEYS = {
    "extension_evaluation",
    "teaching_breakdown",
    "delivery_acceptance",
}


def evaluate_profile_benchmark(case: Any, scan_result: dict[str, Any]) -> dict[str, Any]:
    """执行调研档位 benchmark 的确定性垂直切片，不触发 LLM、网络或数据库。"""

    profile = resolve_benchmark_profile(case)
    inventory = build_profile_inventory(case, scan_result)
    package = build_profile_package(case, scan_result)
    quality = score_analysis_package(
        snapshots=inventory["snapshots"],
        codebase_maps=normalize_codebase_for_quality(inventory["codebase_maps"]),
        evidence=package["evidence_items"],
        findings=package["findings"],
        risks=package["risks"],
        questions=package["questions"],
        recommendations=package["recommendations"],
        previous_overall=None,
        research_profile=profile.key,
    )
    package["quality_scores"].append(quality_score_item(quality))

    verifier_result = verify_analysis_package(package, inventory)
    markdown = render_report_markdown(package["project"], package, inventory, verifier_result)
    report_sections = extract_markdown_sections(markdown)
    profile_checks = extract_profile_checks(verifier_result)
    checks = build_benchmark_checks(
        case=case,
        profile=profile,
        package=package,
        inventory=inventory,
        quality=quality,
        verifier_result=verifier_result,
        report_sections=report_sections,
        profile_checks=profile_checks,
    )

    expected_checks = list(case.expected_checks)
    check_names = [check["name"] for check in checks]
    missing_checks = [name for name in expected_checks if name not in check_names]
    unexpected_checks = [name for name in check_names if name not in expected_checks]
    passed = (
        bool(verifier_result.get("passed"))
        and not missing_checks
        and not unexpected_checks
        and all(check["passed"] for check in checks)
    )

    return {
        "case_id": case.case_id,
        "description": case.description,
        "case_type": "profile_benchmark",
        "passed": passed,
        "expected_checks": expected_checks,
        "missing_checks": missing_checks,
        "unexpected_checks": unexpected_checks,
        "checks": checks,
        "research_profile": {"key": profile.key, "label": profile.label},
        "profile_checks": profile_checks,
        "quality_summary": {
            "overall_score": quality["overall_score"],
            "metadata_profile": value_of(quality.get("metadata", {}), "research_profile"),
        },
        "report_sections": report_sections,
        "bundle_summary": first_bundle_summary(inventory),
    }


def build_profile_inventory(case: Any, scan_result: dict[str, Any]) -> dict[str, Any]:
    bundle_status = resolve_bundle_status(scan_result)
    return {
        "snapshots": [
            {
                "id": f"{case.case_id}-snapshot",
                "source_type": "local_directory",
                "status": "collected",
                "title": case.description,
            }
        ],
        "codebase_maps": [
            {
                "id": f"{case.case_id}-codebase",
                "root_label": case.case_id,
                "tech_stack_json": scan_result["tech_stack"],
                "dependency_files_json": scan_result["dependency_files"],
                "config_files_json": scan_result["config_files"],
                "test_files_json": scan_result["test_files"],
                "entrypoint_files_json": scan_result["entrypoint_files"],
                "readme_excerpt": scan_result["readme_excerpt"],
                "file_tree_json": scan_result["file_tree"],
                "skipped_items_json": scan_result["skipped_items"],
            }
        ],
        "source_bundles": [
            {
                "name": f"{case.case_id} benchmark bundle",
                "status": bundle_status,
                "item_count": 1 if bundle_status in {"collected", "failed"} else 0,
                "collected_count": 1 if bundle_status == "collected" else 0,
                "failed_count": 1 if bundle_status == "failed" else 0,
                "pending_count": 1 if bundle_status == "pending" else 0,
            }
        ],
    }


def build_profile_package(case: Any, scan_result: dict[str, Any]) -> dict[str, Any]:
    profile = resolve_benchmark_profile(case)
    evidence_id = f"{case.case_id}-evidence"
    evidence_items = [
        {
            "id": evidence_id,
            "title": f"{case.case_id} codebase evidence",
            "summary": evidence_summary(scan_result),
            "evidence_type": "codebase",
            "reference": case.case_id,
            "confidence": 90,
        }
    ]
    findings = profile_findings(profile.key, evidence_id, scan_result)
    risks = profile_risks(profile.key, evidence_id, scan_result)
    questions = profile_questions(profile.key, scan_result)
    recommendations = [
        {
            "id": f"{case.case_id}-recommendation",
            "summary": "Use the benchmark package as the profile acceptance snapshot.",
            "rationale": "The package records inventory, profile findings, risks, and evidence links.",
            "confidence": 90,
            "next_steps_json": ["Review profile checks", "Render benchmark report"],
        }
    ]

    return {
        "research_profile": profile.key,
        "profile_summary": {
            "key": profile.key,
            "label": profile.label,
            "description": profile.description,
            "checklist": list(profile.checklist),
            "verifier_checks": list(profile.verifier_checks),
            "report_sections": list(profile.report_sections),
            "required_evidence_types": list(profile.required_evidence_types),
        },
        "project": {
            "id": f"{case.case_id}-project",
            "name": case.description,
            "topic": case.description,
            "analysis_goal": f"Evaluate the {profile.key} research profile benchmark.",
            "status": "ready_for_review",
            "research_profile": profile.key,
        },
        "run": {
            "id": f"{case.case_id}-run",
            "status": "ready_for_review",
            "research_profile": profile.key,
        },
        "turns": [
            {
                "id": f"{case.case_id}-turn",
                "turn_index": 1,
                "node_name": "profile_benchmark",
                "action": "Build deterministic benchmark package.",
                "observation": "Synthetic profile package created from readonly code scan output.",
                "metadata_json": {"case_id": case.case_id, "research_profile": profile.key},
            }
        ],
        # 评测包同时保留通用 evidence 和现有 verifier 使用的 evidence_items 命名。
        "evidence": evidence_items,
        "evidence_items": evidence_items,
        "findings": findings,
        "risks": risks,
        "questions": questions,
        "recommendations": recommendations,
        "quality_scores": [],
    }


def evidence_summary(scan_result: dict[str, Any]) -> str:
    return (
        "Readonly scan found "
        f"{len(scan_result.get('entrypoint_files', []))} entrypoints, "
        f"{len(scan_result.get('dependency_files', []))} dependency files, "
        f"and {len(scan_result.get('test_files', []))} test files."
    )


def resolve_benchmark_profile(case: Any) -> Any:
    profile_key = value_of(case, "research_profile")
    if profile_key not in PROFILE_FINDING_CATEGORIES:
        raise ValueError(f"Unsupported profile benchmark research profile `{profile_key}`.")
    return get_research_profile(profile_key)


def resolve_bundle_status(scan_result: dict[str, Any]) -> str:
    if scan_result.get("collection_error") or scan_result.get("error"):
        return "failed"
    if scan_has_any(scan_result, "file_tree", "entrypoint_files", "dependency_files", "test_files") or scan_result.get(
        "readme_excerpt"
    ):
        return "collected"
    return "pending"


def profile_findings(profile_key: str, evidence_id: str, scan_result: dict[str, Any]) -> list[dict[str, Any]]:
    categories = profile_finding_categories(profile_key, scan_result)
    return [
        finding(
            title=f"{profile_key} benchmark finding: {category}",
            category=category,
            evidence_id=evidence_id,
        )
        for category in categories
    ]


def profile_finding_categories(profile_key: str, scan_result: dict[str, Any]) -> list[str]:
    if profile_key == "quick_onboarding":
        return ["reading_path"] if scan_has_any(scan_result, "entrypoint_files", "config_files", "test_files") else []
    if profile_key == "architecture_understanding":
        return ["architecture_module_boundary"] if scan_has_any(scan_result, "file_tree", "entrypoint_files") else []
    if profile_key == "extension_evaluation":
        return ["extension"] if scan_has_any(scan_result, "test_files", "config_files", "dependency_files") else []
    if profile_key == "teaching_breakdown":
        categories = []
        if scan_has_any(scan_result, "entrypoint_files", "file_tree"):
            categories.append("teaching_learning_path")
        if scan_has_any(scan_result, "test_files"):
            categories.append("practice_exercise")
        return categories
    if profile_key == "delivery_acceptance":
        return ["delivery_readiness"] if scan_has_delivery_clues(scan_result) else []
    raise ValueError(f"Unsupported profile benchmark research profile `{profile_key}`.")


def profile_risks(profile_key: str, evidence_id: str, scan_result: dict[str, Any]) -> list[dict[str, Any]]:
    if profile_key not in PROFILE_FINDING_CATEGORIES:
        raise ValueError(f"Unsupported profile benchmark research profile `{profile_key}`.")
    if profile_key not in PROFILE_RISK_KEYS:
        return []
    if profile_key == "extension_evaluation" and not scan_has_any(
        scan_result,
        "test_files",
        "config_files",
        "dependency_files",
    ):
        return []
    if profile_key == "teaching_breakdown" and not scan_has_any(scan_result, "file_tree", "test_files"):
        return []
    if profile_key == "delivery_acceptance" and not scan_has_delivery_clues(scan_result):
        return []
    return [
        {
            "id": f"{profile_key}-risk",
            "title": f"{profile_key} profile risk",
            "summary": "Profile-specific acceptance depends on keeping evidence and mitigation explicit.",
            "severity": "medium",
            "mitigation": "Review the generated profile checks and update the benchmark fixture before extending scope.",
            "evidence_refs_json": [evidence_id],
            "evidence_refs": [evidence_id],
        }
    ]


def profile_questions(profile_key: str, scan_result: dict[str, Any]) -> list[dict[str, Any]]:
    if profile_key not in PROFILE_FINDING_CATEGORIES:
        raise ValueError(f"Unsupported profile benchmark research profile `{profile_key}`.")
    if profile_key != "delivery_acceptance":
        return []
    if not scan_result.get("readme_excerpt"):
        return []
    return [
        {
            "id": f"{profile_key}-question",
            "prompt": "Is the handoff package acceptable for benchmark delivery?",
            "reason": "Delivery acceptance requires an explicit answered handoff question.",
            "impact": "medium",
            "status": "answered",
            "answer_text": "Yes; the benchmark package includes runbook clues and evidence references.",
        }
    ]


def scan_has_any(scan_result: dict[str, Any], *keys: str) -> bool:
    return any(bool(scan_result.get(key)) for key in keys)


def scan_has_delivery_clues(scan_result: dict[str, Any]) -> bool:
    return bool(scan_result.get("readme_excerpt")) and scan_has_any(
        scan_result,
        "entrypoint_files",
        "dependency_files",
        "config_files",
    )


def finding(title: str, category: str, evidence_id: str) -> dict[str, Any]:
    return {
        "id": f"{category}-finding",
        "title": title,
        "summary": "Synthetic benchmark finding anchored to the readonly codebase evidence.",
        "category": category,
        "impact_level": "medium",
        "confidence": 90,
        "evidence_refs_json": [evidence_id],
        "evidence_refs": [evidence_id],
    }


def benchmark_check(name: str, passed: bool, message: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "message": message}


def build_benchmark_checks(
    *,
    case: Any,
    profile: Any,
    package: dict[str, Any],
    inventory: dict[str, Any],
    quality: dict[str, Any],
    verifier_result: dict[str, Any],
    report_sections: list[str],
    profile_checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    quality_metadata = quality.get("metadata", {})
    passed_profile_checks = {check["name"] for check in profile_checks if check["passed"]}
    missing_profile_checks = [
        check_name for check_name in case.expected_profile_checks if check_name not in passed_profile_checks
    ]
    missing_report_sections = [
        section for section in case.expected_report_sections if section not in report_sections
    ]
    bundle_summary = first_bundle_summary(inventory)
    expected_bundle_status = case.expected_bundle_status or "collected"

    return [
        benchmark_check(
            "profile_key_matches",
            value_of(verifier_result.get("research_profile", {}), "key") == profile.key
            and value_of(package.get("project", {}), "research_profile") == profile.key
            and value_of(package.get("run", {}), "research_profile") == profile.key,
            f"Expected profile `{profile.key}`.",
        ),
        benchmark_check(
            "profile_checks_passed",
            not missing_profile_checks,
            (
                "All expected profile checks passed."
                if not missing_profile_checks
                else f"Missing expected profile checks: {', '.join(missing_profile_checks)}"
            ),
        ),
        benchmark_check(
            "quality_metadata_profile",
            value_of(quality_metadata, "research_profile") == profile.key,
            f"Quality metadata profile is `{value_of(quality_metadata, 'research_profile')}`.",
        ),
        benchmark_check(
            "quality_metadata_weights",
            value_of(quality_metadata, "quality_weights") == profile.quality_weights,
            "Quality metadata includes the active profile weights.",
        ),
        benchmark_check(
            "report_sections_present",
            not missing_report_sections,
            (
                "All expected report sections are present."
                if not missing_report_sections
                else f"Missing report sections: {', '.join(missing_report_sections)}"
            ),
        ),
        benchmark_check(
            "bundle_summary_present",
            value_of(bundle_summary, "status") == expected_bundle_status
            and int(value_of(bundle_summary, "item_count", 0) or 0) >= 1,
            (
                f"Expected bundle status `{expected_bundle_status}`, "
                f"actual `{value_of(bundle_summary, 'status', 'missing')}`, "
                f"item_count `{value_of(bundle_summary, 'item_count', 0)}`."
            ),
        ),
        benchmark_check(
            "evidence_refs_valid",
            evidence_refs_valid(package),
            "Finding and risk evidence references resolve to package evidence ids.",
        ),
    ]


def normalize_codebase_for_quality(codebase_maps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    key_map = {
        "tech_stack_json": "tech_stack",
        "dependency_files_json": "dependency_files",
        "config_files_json": "config_files",
        "test_files_json": "test_files",
        "entrypoint_files_json": "entrypoint_files",
        "file_tree_json": "file_tree",
        "skipped_items_json": "skipped_items",
    }
    for codebase_map in codebase_maps:
        item = dict(codebase_map)
        for source_key, target_key in key_map.items():
            if source_key in item:
                item[target_key] = item[source_key]
        normalized.append(item)
    return normalized


def quality_score_item(quality: dict[str, Any]) -> dict[str, Any]:
    return {
        **quality,
        "reasons_json": quality.get("reasons", []),
        "next_actions_json": quality.get("next_actions", []),
        "metadata_json": quality["metadata"],
    }


def extract_markdown_sections(markdown: str) -> list[str]:
    return [line[3:].strip() for line in markdown.splitlines() if line.startswith("## ")]


def evidence_refs_valid(package: dict[str, Any]) -> bool:
    evidence_ids = {
        str(value_of(evidence, "id"))
        for evidence in as_list(package.get("evidence_items"))
        if value_of(evidence, "id")
    }
    for item in [*as_list(package.get("findings")), *as_list(package.get("risks"))]:
        refs = [
            str(ref)
            for key in ("evidence_refs_json", "evidence_refs")
            for ref in as_list(value_of(item, key, []))
        ]
        if not refs or any(str(ref) not in evidence_ids for ref in refs):
            return False
    return bool(evidence_ids)


def extract_profile_checks(verifier_result: dict[str, Any]) -> list[dict[str, Any]]:
    checks = []
    for check in as_list(verifier_result.get("checks")):
        name = str(value_of(check, "name", ""))
        if not name.startswith("profile:"):
            continue
        messages = [str(message) for message in as_list(value_of(check, "messages", []))]
        checks.append(
            {
                "name": name,
                "passed": bool(value_of(check, "passed", False)),
                "message": " ".join(messages),
            }
        )
    return checks


def first_bundle_summary(inventory: dict[str, Any]) -> dict[str, Any]:
    bundles = as_list(inventory.get("source_bundles"))
    return bundles[0] if bundles else {}
