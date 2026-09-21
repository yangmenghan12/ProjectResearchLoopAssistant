from pathlib import Path

import pytest

from app.evaluations.cases import evaluation_cases, write_case_repo
from app.evaluations.profile_benchmark import (
    build_profile_package,
    evaluate_profile_benchmark,
    evidence_refs_valid,
)
from app.research.profiles import get_research_profile
from app.tools.code_scan_tools import scan_codebase


PROFILE_CASE_IDS = [
    "profile-quick-onboarding",
    "profile-architecture-understanding",
    "profile-extension-evaluation",
    "profile-teaching-breakdown",
    "profile-delivery-acceptance",
]


def profile_case(case_id: str):
    return next(case for case in evaluation_cases() if case.case_id == case_id)


@pytest.mark.parametrize("case_id", PROFILE_CASE_IDS)
def test_profile_benchmark_case_returns_expected_result_shape(tmp_path: Path, case_id: str):
    case = profile_case(case_id)
    case_root = write_case_repo(tmp_path, case)
    scan_result = scan_codebase(case_root, max_file_bytes=256 * 1024, excerpt_chars=500)

    result = evaluate_profile_benchmark(case, scan_result)

    profile = get_research_profile(case.research_profile)
    assert result["case_type"] == "profile_benchmark"
    assert result["passed"] is True
    assert result["research_profile"] == {"key": profile.key, "label": profile.label}
    assert result["quality_summary"]["metadata_profile"] == profile.key
    assert result["quality_summary"]["overall_score"] >= 70
    assert result["bundle_summary"]["status"] == "collected"
    assert result["bundle_summary"]["item_count"] >= 1
    assert set(case.expected_report_sections) <= set(result["report_sections"])
    passed_profile_checks = {
        check["name"]
        for check in result["profile_checks"]
        if check["passed"]
    }
    assert set(case.expected_profile_checks) <= passed_profile_checks
    assert {check["name"] for check in result["checks"]} == set(case.expected_checks)


def test_profile_benchmark_fails_when_expected_profile_check_is_missing(tmp_path: Path):
    base_case = profile_case("profile-quick-onboarding")
    broken_case = base_case.__class__(
        **{
            **base_case.__dict__,
            "expected_profile_checks": ["profile:missing_profile_check"],
        }
    )
    case_root = write_case_repo(tmp_path, base_case)
    scan_result = scan_codebase(case_root, max_file_bytes=256 * 1024, excerpt_chars=500)

    result = evaluate_profile_benchmark(broken_case, scan_result)

    assert result["passed"] is False
    failed = next(check for check in result["checks"] if check["name"] == "profile_checks_passed")
    assert failed["passed"] is False
    assert "profile:missing_profile_check" in failed["message"]


def test_profile_benchmark_bundle_status_is_not_derived_from_expected_status(tmp_path: Path):
    base_case = profile_case("profile-quick-onboarding")
    failed_expected_case = base_case.__class__(
        **{
            **base_case.__dict__,
            "expected_bundle_status": "failed",
        }
    )
    case_root = write_case_repo(tmp_path, base_case)
    scan_result = scan_codebase(case_root, max_file_bytes=256 * 1024, excerpt_chars=500)

    result = evaluate_profile_benchmark(failed_expected_case, scan_result)

    assert result["passed"] is False
    assert result["bundle_summary"]["status"] == "collected"
    failed = next(check for check in result["checks"] if check["name"] == "bundle_summary_present")
    assert failed["passed"] is False
    assert "failed" in failed["message"]


def test_profile_benchmark_fails_when_scan_derived_onboarding_clues_are_missing(tmp_path: Path):
    case = profile_case("profile-quick-onboarding")
    case_root = write_case_repo(tmp_path, case)
    scan_result = scan_codebase(case_root, max_file_bytes=256 * 1024, excerpt_chars=500)
    stripped_scan_result = {
        **scan_result,
        "dependency_files": [],
        "entrypoint_files": [],
        "test_files": [],
        "config_files": [],
        "file_tree": [],
    }

    result = evaluate_profile_benchmark(case, stripped_scan_result)

    assert result["passed"] is False
    failed = next(check for check in result["checks"] if check["name"] == "profile_checks_passed")
    assert failed["passed"] is False
    assert "profile:has_reading_path" in failed["message"]


def test_profile_benchmark_raises_for_unsupported_profile_key(tmp_path: Path):
    base_case = profile_case("profile-quick-onboarding")
    unsupported_case = base_case.__class__(
        **{
            **base_case.__dict__,
            "research_profile": "unsupported_profile",
        }
    )
    case_root = write_case_repo(tmp_path, base_case)
    scan_result = scan_codebase(case_root, max_file_bytes=256 * 1024, excerpt_chars=500)

    with pytest.raises(ValueError, match="Unsupported profile benchmark"):
        evaluate_profile_benchmark(unsupported_case, scan_result)


def test_evidence_refs_valid_fails_for_missing_finding_reference(tmp_path: Path):
    case = profile_case("profile-quick-onboarding")
    case_root = write_case_repo(tmp_path, case)
    scan_result = scan_codebase(case_root, max_file_bytes=256 * 1024, excerpt_chars=500)
    package = build_profile_package(case, scan_result)
    package["findings"][0]["evidence_refs_json"] = ["missing-evidence"]

    assert evidence_refs_valid(package) is False
