from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.context.builder import build_analysis_context
from app.db.base import Base
from app.db.models import Project, SourceBundle, SourceBundleItem
from app.research.profiles import get_research_profile
from app.services.reports import build_analysis_package
from app.verifiers.package import verify_analysis_package
from app.verifiers.quality import score_analysis_package
from tests.test_llm_report_generator import create_reportable_project, session_scope
from tests.test_runs_api import build_client, create_collected_project, enqueue_and_process_run


def item(**kwargs):
    return SimpleNamespace(**kwargs)


def scoring_inputs() -> dict:
    return {
        "snapshots": [{"id": "snapshot-1"}],
        "codebase_maps": [
            {
                "tech_stack": ["python"],
                "dependency_files": ["pyproject.toml"],
                "entrypoint_files": ["app.py"],
                "test_files": [],
                "readme_excerpt": "# README",
            }
        ],
        "evidence": [{"id": "evidence-1"}],
        "findings": [{"evidence_refs": ["evidence-1"]}],
        "risks": [{"severity": "medium", "mitigation": "补充回归测试。"}],
        "questions": [],
        "recommendations": [{"summary": "Continue", "confidence": 70}],
        "previous_overall": None,
    }


def test_quality_score_uses_research_profile_weights_and_returns_metadata():
    quick_score = score_analysis_package(**scoring_inputs(), research_profile="quick_onboarding")
    delivery_score = score_analysis_package(**scoring_inputs(), research_profile="delivery_acceptance")
    fallback_score = score_analysis_package(**scoring_inputs())
    quick_profile = get_research_profile("quick_onboarding")

    assert quick_score["overall_score"] != delivery_score["overall_score"]
    assert quick_score["metadata"]["research_profile"] == "quick_onboarding"
    assert quick_score["metadata"]["quality_weights"] == quick_profile.quality_weights
    assert quick_score["metadata"]["checklist"] == quick_profile.checklist
    assert fallback_score["metadata"]["research_profile"] == "quick_onboarding"


def test_package_verifier_adds_extension_profile_checks_and_metadata():
    evidence = item(
        id="ev-1",
        title="Codebase inventory",
        summary="Repository has app.py, pyproject.toml, tests, and risk notes.",
        evidence_type="codebase_map",
        reference="repo",
        confidence=90,
    )
    package = {
        "research_profile": "extension_evaluation",
        "turns": [item(turn_index=1, node_name="verify_package")],
        "evidence_items": [evidence],
        "findings": [
            item(
                title="Extension surface is testable",
                summary="The repository includes tests and configuration clues.",
                category="extension",
                impact_level="medium",
                confidence=86,
                evidence_refs_json=["ev-1"],
            )
        ],
        "risks": [
            item(
                title="Upgrade risk",
                summary="Changing dependencies may affect runtime behavior.",
                severity="medium",
                mitigation="Run existing tests and add coverage around changed adapters.",
                evidence_refs_json=["ev-1"],
            )
        ],
        "questions": [],
        "recommendations": [
            item(
                summary="Proceed after targeted regression tests.",
                rationale="The package records tests, risks, and mitigations.",
                confidence=88,
                next_steps_json=["Run pytest", "Review risk register"],
            )
        ],
        "quality_scores": [
            item(
                overall_score=90,
                structure_completeness=90,
                evidence_coverage=90,
                codebase_coverage=90,
                risk_transparency=90,
                question_resolution=90,
                recommendation_confidence=90,
                reasons_json=[],
                next_actions_json=[],
            )
        ],
    }
    inventory = {
        "snapshots": [item(id="snapshot-1", source_type="local_directory", status="collected")],
        "codebase_maps": [
            item(
                id="map-1",
                root_label="repo",
                tech_stack_json=["python"],
                dependency_files_json=["pyproject.toml"],
                config_files_json=["pytest.ini"],
                test_files_json=["tests/test_extension.py"],
                entrypoint_files_json=["app.py"],
                readme_excerpt="# README",
            )
        ],
    }

    result = verify_analysis_package(package, inventory)

    check_names = {check["name"] for check in result["checks"]}
    assert {
        "profile:has_test_clues",
        "profile:has_risk_register",
        "profile:has_mitigation",
    } <= check_names
    assert result["research_profile"]["key"] == "extension_evaluation"
    assert result["research_profile"]["label"]
    assert result["research_profile"]["checklist"] == get_research_profile(
        "extension_evaluation"
    ).checklist


def test_teaching_breakdown_profile_includes_course_section_suggestion():
    assert "课程章节建议" in get_research_profile("teaching_breakdown").report_sections


def test_profile_checks_do_not_pass_from_guidance_or_negative_text_only():
    package = {
        "research_profile": "extension_evaluation",
        "turns": [item(turn_index=1, node_name="verify_package")],
        "evidence_items": [
            item(
                id="ev-1",
                title="Inventory",
                summary="The current scan did not find tests.",
                evidence_type="source_snapshot",
                reference="README",
                confidence=80,
            )
        ],
        "findings": [
            item(
                title="No test files found",
                summary="The scanner reports missing tests.",
                category="risk",
                evidence_refs_json=["ev-1"],
            )
        ],
        "risks": [
            item(
                title="Missing tests",
                summary="No automated tests were identified.",
                severity="medium",
                mitigation="Add tests before extending.",
                evidence_refs_json=["ev-1"],
            )
        ],
        "questions": [],
        "recommendations": [
            item(
                summary="Follow the extension checklist.",
                rationale="Next steps mention tests and risk review.",
                confidence=80,
                next_steps_json=["Add regression tests", "Review risk register"],
            )
        ],
        "quality_scores": [item(overall_score=90)],
    }
    inventory = {
        "snapshots": [item(id="snapshot-1", source_type="local_file", status="collected")],
        "codebase_maps": [
            item(
                root_label="repo",
                tech_stack_json=["python"],
                dependency_files_json=["pyproject.toml"],
                config_files_json=[],
                test_files_json=[],
                entrypoint_files_json=["app.py"],
                readme_excerpt="# README",
            )
        ],
    }

    result = verify_analysis_package(package, inventory)
    checks = {check["name"]: check for check in result["checks"]}

    assert checks["profile:has_test_clues"]["passed"] is False
    assert checks["profile:has_risk_register"]["passed"] is True
    assert checks["profile:has_mitigation"]["passed"] is True


def test_quick_onboarding_profile_checks_ignore_negative_keyword_text():
    package = {
        "research_profile": "quick_onboarding",
        "turns": [item(turn_index=1, node_name="verify_package")],
        "evidence_items": [
            item(
                id="ev-1",
                title="Inventory gap",
                summary=(
                    "The current scan did not find an entrypoint, dependency file, "
                    "or reading path."
                ),
                evidence_type="source_snapshot",
                reference="README",
                confidence=80,
            )
        ],
        "findings": [
            item(
                title="Missing onboarding clues",
                summary="No entrypoint, dependency manifest, or reading path was identified.",
                category="risk",
                evidence_refs_json=["ev-1"],
            )
        ],
        "risks": [
            item(
                title="Incomplete onboarding",
                summary="The project cannot be handed to a newcomer yet.",
                severity="medium",
                mitigation="Collect codebase inventory and README evidence.",
                evidence_refs_json=["ev-1"],
            )
        ],
        "questions": [],
        "recommendations": [
            item(
                summary="Continue intake.",
                rationale="Negative findings should not satisfy profile checks.",
                confidence=80,
            )
        ],
        "quality_scores": [item(overall_score=90)],
    }
    inventory = {
        "snapshots": [item(id="snapshot-1", source_type="local_file", status="collected")],
        "codebase_maps": [
            item(
                root_label="repo",
                tech_stack_json=["python"],
                dependency_files_json=[],
                config_files_json=[],
                test_files_json=[],
                entrypoint_files_json=[],
                readme_excerpt="# README",
            )
        ],
    }

    result = verify_analysis_package(package, inventory)
    checks = {check["name"]: check for check in result["checks"]}

    assert checks["profile:has_entrypoint"]["passed"] is False
    assert checks["profile:has_dependency_clues"]["passed"] is False
    assert checks["profile:has_reading_path"]["passed"] is False


def test_quick_onboarding_reading_path_does_not_pass_from_recommendation_steps_only():
    package = {
        "research_profile": "quick_onboarding",
        "turns": [item(turn_index=1, node_name="verify_package")],
        "evidence_items": [
            item(
                id="ev-1",
                title="Inventory",
                summary="Collected a repository summary without an explicit onboarding route.",
                evidence_type="source_snapshot",
                confidence=80,
            )
        ],
        "findings": [
            item(
                title="Repository summary exists",
                summary="The source was collected, but no structured route was identified.",
                category="codebase",
                evidence_refs_json=["ev-1"],
            )
        ],
        "risks": [],
        "questions": [],
        "recommendations": [
            item(
                summary="Continue analysis.",
                rationale="Suggested next steps are output guidance, not verifier evidence.",
                confidence=80,
                next_steps_json=["Open README", "Inspect app.py"],
            )
        ],
        "quality_scores": [item(overall_score=90)],
    }
    inventory = {
        "snapshots": [item(id="snapshot-1", source_type="local_file", status="collected")],
        "codebase_maps": [
            item(
                root_label="repo",
                tech_stack_json=["python"],
                dependency_files_json=[],
                config_files_json=[],
                test_files_json=[],
                entrypoint_files_json=[],
                readme_excerpt="# README",
            )
        ],
    }

    result = verify_analysis_package(package, inventory)
    checks = {check["name"]: check for check in result["checks"]}

    assert checks["profile:has_reading_path"]["passed"] is False


def test_delivery_acceptance_can_pass_without_open_questions_when_readiness_is_explicit():
    package = {
        "research_profile": "delivery_acceptance",
        "turns": [item(turn_index=1, node_name="verify_package")],
        "evidence_items": [
            item(
                id="ev-1",
                title="Runbook",
                summary="README contains startup steps.",
                evidence_type="source_snapshot",
            )
        ],
        "findings": [
            item(
                title="Delivery readiness summary",
                summary="The project is ready for handoff after documented startup verification.",
                category="delivery_readiness",
                evidence_refs_json=["ev-1"],
            )
        ],
        "risks": [],
        "questions": [],
        "recommendations": [
            item(
                summary="Ready for delivery.",
                rationale="Runbook and readiness summary are present.",
                confidence=90,
            )
        ],
        "quality_scores": [item(overall_score=90)],
    }
    inventory = {
        "snapshots": [item(id="snapshot-1", source_type="local_file", status="collected")],
        "codebase_maps": [
            item(
                root_label="repo",
                tech_stack_json=["python"],
                dependency_files_json=["pyproject.toml"],
                config_files_json=["settings.py"],
                test_files_json=["tests/test_app.py"],
                entrypoint_files_json=["app.py"],
                readme_excerpt="# README\nRun with python app.py",
            )
        ],
    }

    result = verify_analysis_package(package, inventory)
    checks = {check["name"]: check for check in result["checks"]}

    assert checks["profile:has_open_questions"]["passed"] is True
    assert result["risk_level"] == "low"


def test_context_builder_returns_research_profile_and_source_bundle_summaries():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        project = Project(
            name="Profile context",
            topic="Build a teaching outline",
            analysis_goal="Prepare courseware",
            research_profile="teaching_breakdown",
        )
        session.add(project)
        session.flush()
        bundle = SourceBundle(project_id=project.id, name="Course intake", status="partial")
        session.add(bundle)
        session.flush()
        session.add_all(
            [
                SourceBundleItem(
                    bundle_id=bundle.id,
                    project_id=project.id,
                    input_type="markdown_file",
                    raw_value="notes.md",
                    status="pending",
                ),
                SourceBundleItem(
                    bundle_id=bundle.id,
                    project_id=project.id,
                    input_type="github_repo",
                    raw_value="https://github.com/example/repo",
                    status="failed",
                    error_message="Network disabled",
                ),
            ]
        )
        project_id = project.id
        bundle_id = bundle.id
        session.commit()

        context = build_analysis_context(session, project_id)

    assert context["research_profile"]["key"] == "teaching_breakdown"
    assert context["research_profile"]["label"]
    assert context["research_profile"]["checklist"] == get_research_profile(
        "teaching_breakdown"
    ).checklist
    assert context["research_profile"]["verifier_checks"] == get_research_profile(
        "teaching_breakdown"
    ).verifier_checks
    assert context["source_bundles"] == [
        {
            "id": bundle_id,
            "name": "Course intake",
            "status": "partial",
            "item_count": 2,
        }
    ]


def test_langgraph_runner_persists_quality_score_metadata(tmp_path):
    client = build_client()
    project = create_collected_project(client, tmp_path)

    package = enqueue_and_process_run(client, project["id"])

    metadata = package["quality_scores"][0]["metadata_json"]
    assert metadata["research_profile"] == "quick_onboarding"
    assert metadata["quality_weights"] == get_research_profile("quick_onboarding").quality_weights
    assert metadata["checklist"] == get_research_profile("quick_onboarding").checklist


def test_report_package_carries_project_profile_when_quality_metadata_is_empty():
    with session_scope() as session:
        project, run, _ = create_reportable_project(session)
        project.research_profile = "extension_evaluation"
        package = build_analysis_package(session, run)
        inventory = {
            "snapshots": [],
            "codebase_maps": [],
        }

        result = verify_analysis_package(package, inventory)

    assert package["project"]["research_profile"] == "extension_evaluation"
    assert result["research_profile"]["key"] == "extension_evaluation"
