import pytest

from app.engine.state_machine import (
    can_start_run,
    decide_terminal_state,
    ensure_can_start_run,
)
from app.verifiers.quality import score_analysis_package


@pytest.mark.parametrize(
    "status",
    ["draft", "ready", "ready_for_review", "ready_for_review_with_risks", "failed"],
)
def test_can_start_run_from_non_running_states(status: str):
    assert can_start_run(status) is True


@pytest.mark.parametrize("status", ["running", "paused_for_input", "completed", "cancelled"])
def test_cannot_start_run_from_active_or_terminal_states(status: str):
    assert can_start_run(status) is False
    with pytest.raises(ValueError):
        ensure_can_start_run(status)


def test_decide_terminal_state_pauses_for_open_high_impact_question():
    decision = decide_terminal_state(
        snapshots=[{"id": "s1"}],
        findings=[{"evidence_refs": ["e1"]}],
        risks=[],
        questions=[{"status": "open", "impact": "high"}],
        recommendations=[{"summary": "Continue"}],
        quality_score={"overall_score": 82},
    )

    assert decision["status"] == "paused_for_input"
    assert "问题" in decision["reason"]


def test_decide_terminal_state_fails_without_snapshots():
    decision = decide_terminal_state(
        snapshots=[],
        findings=[],
        risks=[],
        questions=[],
        recommendations=[],
        quality_score={"overall_score": 0},
    )

    assert decision["status"] == "failed"


def test_decide_terminal_state_returns_ready_for_review_when_quality_is_high():
    decision = decide_terminal_state(
        snapshots=[{"id": "s1"}],
        findings=[{"evidence_refs": ["e1"]}],
        risks=[],
        questions=[],
        recommendations=[{"summary": "Continue"}],
        quality_score={"overall_score": 85},
    )

    assert decision["status"] == "ready_for_review"


def test_decide_terminal_state_returns_review_with_risks_when_quality_is_partial():
    decision = decide_terminal_state(
        snapshots=[{"id": "s1"}],
        findings=[{"evidence_refs": ["e1"]}],
        risks=[{"severity": "medium", "mitigation": "Add tests"}],
        questions=[],
        recommendations=[{"summary": "Continue"}],
        quality_score={"overall_score": 72},
    )

    assert decision["status"] == "ready_for_review_with_risks"


def test_score_analysis_package_returns_weighted_six_dimension_score():
    score = score_analysis_package(
        snapshots=[{"id": "s1"}],
        codebase_maps=[
            {
                "tech_stack": ["python"],
                "dependency_files": ["pyproject.toml"],
                "entrypoint_files": ["app.py"],
                "test_files": [],
                "readme_excerpt": "# README",
            }
        ],
        evidence=[{"id": "e1"}],
        findings=[{"evidence_refs": ["e1"]}],
        risks=[{"severity": "medium", "mitigation": "Add tests"}],
        questions=[],
        recommendations=[{"summary": "Continue", "confidence": 70}],
        previous_overall=None,
    )

    assert set(score) == {
        "overall_score",
        "structure_completeness",
        "evidence_coverage",
        "codebase_coverage",
        "risk_transparency",
        "question_resolution",
        "recommendation_confidence",
        "score_delta",
        "reasons",
        "next_actions",
        "metadata",
    }
    assert score["overall_score"] >= 70
    assert score["codebase_coverage"] < 100
    assert "补充测试" in " ".join(score["next_actions"])
