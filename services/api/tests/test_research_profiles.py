import importlib.util
from pathlib import Path

from app.research.profiles import DEFAULT_RESEARCH_PROFILE, get_research_profile, list_research_profiles
from tests.test_projects_sources_api import build_client

EXPECTED_QUALITY_DIMENSIONS = {
    "structure_completeness",
    "evidence_coverage",
    "codebase_coverage",
    "risk_transparency",
    "question_resolution",
    "recommendation_confidence",
}


def test_research_profile_registry_contains_five_profiles():
    profiles = list_research_profiles()

    assert [profile.key for profile in profiles] == [
        "quick_onboarding",
        "architecture_understanding",
        "extension_evaluation",
        "teaching_breakdown",
        "delivery_acceptance",
    ]
    for profile in profiles:
        assert profile.label
        assert profile.description
        assert profile.checklist
        assert profile.quality_weights
        assert set(profile.quality_weights) == EXPECTED_QUALITY_DIMENSIONS
        assert profile.verifier_checks
        assert profile.report_sections
        assert profile.required_evidence_types


def test_unknown_profile_falls_back_to_quick_onboarding():
    assert get_research_profile("missing").key == DEFAULT_RESEARCH_PROFILE


def test_research_profiles_api_lists_profiles():
    client = build_client()

    response = client.get("/research-profiles")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 5
    assert data[0]["key"] == "quick_onboarding"
    assert data[0]["checklist"]


def test_research_profiles_api_gets_profile_by_key():
    client = build_client()

    response = client.get("/research-profiles/architecture_understanding")

    assert response.status_code == 200
    data = response.json()
    assert data["key"] == "architecture_understanding"
    assert data["required_evidence_types"]


def test_research_profiles_api_returns_404_for_unknown_profile():
    client = build_client()

    response = client.get("/research-profiles/not_registered")

    assert response.status_code == 404
    assert response.json()["detail"] == "Research profile not found"


def test_project_create_persists_research_profile():
    client = build_client()

    response = client.post(
        "/projects",
        json={
            "name": "Profiled project",
            "topic": "Evaluate extension risk",
            "analysis_goal": "Focus on tests and configuration.",
            "research_profile": "extension_evaluation",
            "max_turns": 6,
        },
    )

    assert response.status_code == 201
    assert response.json()["research_profile"] == "extension_evaluation"


def test_project_create_rejects_unknown_research_profile():
    client = build_client()

    response = client.post(
        "/projects",
        json={
            "name": "Unknown profile project",
            "topic": "Evaluate extension risk",
            "research_profile": "unknown_profile",
            "max_turns": 6,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Research profile not found"


def test_research_profile_migration_backfills_audit_sequence_and_drops_temporary_defaults():
    migration = (
        Path(__file__).resolve().parents[1]
        / "app/db/migrations/versions/20260702_0011_research_profile.py"
    )
    spec = importlib.util.spec_from_file_location("research_profile_migration", migration)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    operations: list[tuple[str, str, dict]] = []
    updates: list[dict] = []

    class FakeResult:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self.rows

    class FakeConnection:
        def __init__(self):
            self.rows = [
                {"id": "project-a-event-1", "project_id": "project-a"},
                {"id": "project-a-event-2", "project_id": "project-a"},
                {"id": "project-b-event-1", "project_id": "project-b"},
            ]

        def execute(self, statement, params=None):
            if params is None:
                return FakeResult(self.rows)
            updates.append(dict(params))
            return None

    class FakeBatchOp:
        def __init__(self, table_name):
            self.table_name = table_name

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def create_unique_constraint(self, name, columns):
            operations.append(
                (
                    "create_unique_constraint",
                    self.table_name,
                    {"name": name, "columns": list(columns)},
                )
            )

    fake_connection = FakeConnection()

    class FakeOp:
        @staticmethod
        def add_column(table_name, column):
            operations.append(("add_column", table_name, {"column": column.name}))

        @staticmethod
        def alter_column(table_name, column_name, **kwargs):
            operations.append(("alter_column", table_name, {"column": column_name, **kwargs}))

        @staticmethod
        def get_bind():
            return fake_connection

        @staticmethod
        def batch_alter_table(table_name):
            return FakeBatchOp(table_name)

    module.op = FakeOp
    module.upgrade()

    assert (
        "alter_column",
        "projects",
        {"column": "research_profile", "server_default": None},
    ) in operations
    assert (
        "alter_column",
        "audit_events",
        {"column": "sequence", "server_default": None},
    ) in operations
    assert (
        "create_unique_constraint",
        "audit_events",
        {
            "name": "uq_audit_events_project_sequence",
            "columns": ["project_id", "sequence"],
        },
    ) in operations
    assert updates == [
        {"sequence": 1, "id": "project-a-event-1"},
        {"sequence": 2, "id": "project-a-event-2"},
        {"sequence": 1, "id": "project-b-event-1"},
    ]
