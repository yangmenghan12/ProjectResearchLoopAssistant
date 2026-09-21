from dataclasses import dataclass, field
from pathlib import Path
from shutil import rmtree


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    description: str
    expected_checks: list[str]
    case_type: str = "scan_regression"
    research_profile: str | None = None
    expected_profile_checks: list[str] = field(default_factory=list)
    expected_report_sections: list[str] = field(default_factory=list)
    expected_bundle_status: str | None = None


def evaluation_cases() -> list[EvaluationCase]:
    # 这些 case 是确定性回归样例，用来证明 runtime 基础扫描能力稳定，不代表真实 benchmark。
    return [
        EvaluationCase(
            case_id="healthy-small",
            description="包含 README、入口文件和 Python 项目标记的健康小仓库。",
            expected_checks=[
                "readme_present",
                "files_scanned",
                "entrypoint_detected",
                "project_research_package_sections",
                "profile_aware_report_sections",
            ],
        ),
        EvaluationCase(
            case_id="missing-docs",
            description="只有代码文件、没有 README 的仓库，用来确认缺文档会被稳定识别。",
            expected_checks=[
                "readme_missing_detected",
                "files_scanned",
                "project_research_package_sections",
                "profile_aware_report_sections",
            ],
        ),
        EvaluationCase(
            case_id="sensitive-skip",
            description="包含 README 和 .env 的仓库，用来确认敏感路径只进入 skipped_items。",
            expected_checks=[
                "env_skipped",
                "env_not_in_file_tree",
                "readme_present",
                "project_research_package_sections",
                "profile_aware_report_sections",
            ],
        ),
        EvaluationCase(
            case_id="profile-quick-onboarding",
            description="快速接手调研档位基准评估：验证入口、依赖和阅读路径。",
            expected_checks=[
                "profile_key_matches",
                "profile_checks_passed",
                "quality_metadata_profile",
                "quality_metadata_weights",
                "report_sections_present",
                "bundle_summary_present",
                "evidence_refs_valid",
            ],
            case_type="profile_benchmark",
            research_profile="quick_onboarding",
            expected_profile_checks=[
                "profile:has_entrypoint",
                "profile:has_dependency_clues",
                "profile:has_reading_path",
            ],
            expected_report_sections=["调研 Profile", "资料包摘要", "新人接手路径", "30 分钟阅读路线"],
            expected_bundle_status="collected",
        ),
        EvaluationCase(
            case_id="profile-architecture-understanding",
            description="架构理解调研档位基准评估：验证模块边界、入口和架构线索。",
            expected_checks=[
                "profile_key_matches",
                "profile_checks_passed",
                "quality_metadata_profile",
                "quality_metadata_weights",
                "report_sections_present",
                "bundle_summary_present",
                "evidence_refs_valid",
            ],
            case_type="profile_benchmark",
            research_profile="architecture_understanding",
            expected_profile_checks=[
                "profile:has_module_boundaries",
                "profile:has_entrypoint",
                "profile:has_architecture_clues",
            ],
            expected_report_sections=["调研 Profile", "资料包摘要", "模块边界", "主流程与数据流", "扩展点"],
            expected_bundle_status="collected",
        ),
        EvaluationCase(
            case_id="profile-extension-evaluation",
            description="二开评估调研档位基准评估：验证测试线索、风险登记和缓解建议。",
            expected_checks=[
                "profile_key_matches",
                "profile_checks_passed",
                "quality_metadata_profile",
                "quality_metadata_weights",
                "report_sections_present",
                "bundle_summary_present",
                "evidence_refs_valid",
            ],
            case_type="profile_benchmark",
            research_profile="extension_evaluation",
            expected_profile_checks=[
                "profile:has_test_clues",
                "profile:has_risk_register",
                "profile:has_mitigation",
            ],
            expected_report_sections=["调研 Profile", "资料包摘要", "二开可行性", "改造风险", "测试与配置复杂度"],
            expected_bundle_status="collected",
        ),
        EvaluationCase(
            case_id="profile-teaching-breakdown",
            description="教学拆解调研档位基准评估：验证关键文件、学习路径和练习任务。",
            expected_checks=[
                "profile_key_matches",
                "profile_checks_passed",
                "quality_metadata_profile",
                "quality_metadata_weights",
                "report_sections_present",
                "bundle_summary_present",
                "evidence_refs_valid",
            ],
            case_type="profile_benchmark",
            research_profile="teaching_breakdown",
            expected_profile_checks=[
                "profile:has_key_files",
                "profile:has_learning_path",
                "profile:has_exercises",
            ],
            expected_report_sections=["调研 Profile", "资料包摘要", "课程章节建议", "关键代码讲解顺序", "练习任务"],
            expected_bundle_status="collected",
        ),
        EvaluationCase(
            case_id="profile-delivery-acceptance",
            description="交付验收调研档位基准评估：验证 runbook、开放问题状态和交付可接手总结。",
            expected_checks=[
                "profile_key_matches",
                "profile_checks_passed",
                "quality_metadata_profile",
                "quality_metadata_weights",
                "report_sections_present",
                "bundle_summary_present",
                "evidence_refs_valid",
            ],
            case_type="profile_benchmark",
            research_profile="delivery_acceptance",
            expected_profile_checks=[
                "profile:has_runbook_clues",
                "profile:has_open_questions",
                "profile:has_delivery_readiness_summary",
            ],
            expected_report_sections=["调研 Profile", "资料包摘要", "交付完整性", "可启动性", "验收问题清单"],
            expected_bundle_status="collected",
        ),
    ]


def write_case_repo(root: Path, case: EvaluationCase) -> Path:
    root = root.resolve()
    case_root = (root / case.case_id).resolve()
    if case_root.exists():
        if root not in case_root.parents:
            raise ValueError(f"Refusing to reset evaluation case outside workspace: {case_root}")
        rmtree(case_root)
    case_root.mkdir(parents=True, exist_ok=True)

    if case.case_id == "healthy-small":
        (case_root / "README.md").write_text(
            "# Healthy Small\n\nA tiny deterministic Python fixture.\n",
            encoding="utf-8",
        )
        (case_root / "app.py").write_text(
            "def main() -> str:\n    return 'ok'\n\nif __name__ == '__main__':\n    print(main())\n",
            encoding="utf-8",
        )
        (case_root / "pyproject.toml").write_text(
            "[project]\nname = 'healthy-small'\nversion = '0.1.0'\n",
            encoding="utf-8",
        )
        return case_root

    if case.case_id == "missing-docs":
        (case_root / "app.py").write_text(
            "def handler() -> str:\n    return 'missing docs still scans'\n",
            encoding="utf-8",
        )
        return case_root

    if case.case_id == "sensitive-skip":
        (case_root / "README.md").write_text(
            "# Sensitive Skip\n\nThe scanner must skip local secrets.\n",
            encoding="utf-8",
        )
        (case_root / ".env").write_text(
            "LOCAL_SECRET=do-not-read\n",
            encoding="utf-8",
        )
        return case_root

    if case.case_id == "profile-quick-onboarding":
        (case_root / "tests").mkdir()
        (case_root / "README.md").write_text(
            "# Quick Onboarding Fixture\n\nRun with `python app.py` after installing dependencies.\n",
            encoding="utf-8",
        )
        (case_root / "app.py").write_text(
            "def main() -> str:\n    return 'quick onboarding'\n\nif __name__ == '__main__':\n    print(main())\n",
            encoding="utf-8",
        )
        (case_root / "pyproject.toml").write_text(
            "[project]\nname = 'profile-quick-onboarding'\nversion = '0.1.0'\n",
            encoding="utf-8",
        )
        (case_root / "tests" / "test_app.py").write_text(
            "from app import main\n\n\ndef test_main():\n    assert main() == 'quick onboarding'\n",
            encoding="utf-8",
        )
        return case_root

    if case.case_id == "profile-architecture-understanding":
        (case_root / "api").mkdir()
        (case_root / "services").mkdir()
        (case_root / "ui").mkdir()
        (case_root / "config").mkdir()
        (case_root / "README.md").write_text(
            "# Architecture Understanding Fixture\n\n"
            "Entrypoint: `python app.py`.\n"
            "Module boundaries: API routes call workflow services, UI pages read API summaries, "
            "and config/settings.py owns runtime settings.\n",
            encoding="utf-8",
        )
        (case_root / "app.py").write_text(
            "def main() -> str:\n    return 'architecture understanding'\n\n\nif __name__ == '__main__':\n    print(main())\n",
            encoding="utf-8",
        )
        (case_root / "pyproject.toml").write_text(
            "[project]\nname = 'profile-architecture-understanding'\nversion = '0.1.0'\n",
            encoding="utf-8",
        )
        (case_root / "api" / "routes.py").write_text(
            "def route_summary() -> dict[str, str]:\n    return {'boundary': 'api routes'}\n",
            encoding="utf-8",
        )
        (case_root / "services" / "workflow.py").write_text(
            "def run_workflow() -> str:\n    return 'workflow service'\n",
            encoding="utf-8",
        )
        (case_root / "ui" / "page.tsx").write_text(
            "export default function Page() {\n  return <main>Architecture overview</main>;\n}\n",
            encoding="utf-8",
        )
        (case_root / "config" / "settings.py").write_text(
            "APP_NAME = 'architecture-understanding'\n",
            encoding="utf-8",
        )
        return case_root

    if case.case_id == "profile-extension-evaluation":
        (case_root / "tests").mkdir()
        (case_root / "README.md").write_text(
            "# Extension Evaluation Fixture\n\n"
            "Entrypoint: `python app.py`. Extension risk register and mitigation notes live in settings.py.\n",
            encoding="utf-8",
        )
        (case_root / "app.py").write_text(
            "def extension_ready() -> bool:\n    return True\n\n\nif __name__ == '__main__':\n    print(extension_ready())\n",
            encoding="utf-8",
        )
        (case_root / "pyproject.toml").write_text(
            "[project]\nname = 'profile-extension-evaluation'\nversion = '0.1.0'\n",
            encoding="utf-8",
        )
        (case_root / "settings.py").write_text(
            "RISK_REGISTER = ['config drift', 'missing regression coverage']\n"
            "MITIGATION = 'Add smoke tests before extending the workflow.'\n",
            encoding="utf-8",
        )
        (case_root / "tests" / "test_extension.py").write_text(
            "from app import extension_ready\n\n\ndef test_extension_ready():\n    assert extension_ready() is True\n",
            encoding="utf-8",
        )
        return case_root

    if case.case_id == "profile-teaching-breakdown":
        (case_root / "lesson").mkdir()
        (case_root / "tests").mkdir()
        (case_root / "README.md").write_text(
            "# Teaching Breakdown Fixture\n\n"
            "Key files: app.py and lesson/service.py. Learning path: read app, service, then tests. "
            "Exercise: change the lesson title and update the test.\n",
            encoding="utf-8",
        )
        (case_root / "app.py").write_text(
            "def lesson_title() -> str:\n    return 'teaching breakdown'\n\n\nif __name__ == '__main__':\n    print(lesson_title())\n",
            encoding="utf-8",
        )
        (case_root / "pyproject.toml").write_text(
            "[project]\nname = 'profile-teaching-breakdown'\nversion = '0.1.0'\n",
            encoding="utf-8",
        )
        (case_root / "lesson" / "service.py").write_text(
            "def build_learning_path() -> list[str]:\n    return ['entrypoint', 'service', 'exercise']\n",
            encoding="utf-8",
        )
        (case_root / "tests" / "test_lesson.py").write_text(
            "from lesson.service import build_learning_path\n\n\ndef test_learning_path():\n    assert build_learning_path()[-1] == 'exercise'\n",
            encoding="utf-8",
        )
        return case_root

    if case.case_id == "profile-delivery-acceptance":
        (case_root / "tests").mkdir()
        (case_root / "README.md").write_text(
            "# Delivery Acceptance Fixture\n\n"
            "Runbook: install dependencies, copy config.example.env, then run `python app.py`. "
            "Open questions are tracked before handoff, and delivery readiness is summarized here.\n",
            encoding="utf-8",
        )
        (case_root / "app.py").write_text(
            "def smoke() -> str:\n    return 'delivery acceptance ready'\n\n\nif __name__ == '__main__':\n    print(smoke())\n",
            encoding="utf-8",
        )
        (case_root / "pyproject.toml").write_text(
            "[project]\nname = 'profile-delivery-acceptance'\nversion = '0.1.0'\n",
            encoding="utf-8",
        )
        (case_root / "config.example.env").write_text(
            "APP_MODE=example\n",
            encoding="utf-8",
        )
        (case_root / "tests" / "test_smoke.py").write_text(
            "from app import smoke\n\n\ndef test_smoke():\n    assert smoke() == 'delivery acceptance ready'\n",
            encoding="utf-8",
        )
        return case_root

    raise ValueError(f"Unknown evaluation case: {case.case_id}")
