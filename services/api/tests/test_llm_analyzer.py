import pytest

from app.engine.llm_merge import merge_llm_analysis
from app.llm.analyzer import validate_llm_analysis_output
from app.llm.json_tools import extract_json_object


def valid_llm_payload():
    return {
        "evidence": [
            {
                "client_ref": "llm:evidence",
                "title": "Evidence",
                "summary": "Evidence summary",
                "evidence_type": "llm_observation",
                "confidence": 80,
            }
        ],
        "findings": [
            {
                "title": "Finding",
                "summary": "Finding summary",
                "category": "architecture",
                "impact_level": "medium",
                "confidence": 75,
                "evidence_refs": ["evidence:source", "llm:evidence"],
            }
        ],
        "risks": [
            {
                "title": "Risk",
                "summary": "Risk summary",
                "severity": "medium",
                "mitigation": "Add validation.",
                "evidence_refs": ["llm:evidence"],
            }
        ],
        "questions": [
            {
                "prompt": "What is the rollout plan?",
                "reason": "The timeline affects implementation.",
                "impact": "medium",
                "status": "open",
            }
        ],
        "recommendations": [
            {
                "summary": "Proceed with guarded rollout.",
                "rationale": "The evidence supports a low-risk next step.",
                "confidence": 70,
                "next_steps": ["Validate with one project."],
            }
        ],
    }


def test_extract_json_object_accepts_fenced_json():
    text = '```json\n{"evidence": [], "findings": []}\n```'

    assert extract_json_object(text)["evidence"] == []


def test_extract_json_object_reads_json_embedded_in_prose():
    text = 'Analysis follows:\n{"evidence": [{"client_ref": "llm:1"}], "findings": []}\nDone.'

    result = extract_json_object(text)

    assert result["evidence"][0]["client_ref"] == "llm:1"


def test_extract_json_object_returns_first_complete_object_from_multiple_objects():
    text = 'prefix {"a": 1} middle {"b": 2}'

    assert extract_json_object(text) == {"a": 1}


def test_validate_llm_analysis_output_rejects_unknown_evidence_refs():
    payload = valid_llm_payload()
    payload["findings"][0]["evidence_refs"] = ["missing-ref"]

    result = validate_llm_analysis_output(payload, {"evidence:source"})

    assert result.accepted is False
    assert any("missing-ref" in message for message in result.validation["messages"])


@pytest.mark.parametrize("collection", ["findings", "risks"])
def test_validate_llm_analysis_output_rejects_dangling_llm_evidence_refs(collection):
    payload = valid_llm_payload()
    payload[collection][0]["evidence_refs"] = ["llm:missing"]

    result = validate_llm_analysis_output(payload, {"evidence:source"})

    assert result.accepted is False
    assert "Unknown evidence ref: llm:missing" in result.validation["messages"]


def test_validate_llm_analysis_output_accepts_defined_llm_evidence_refs():
    payload = valid_llm_payload()
    payload["findings"][0]["evidence_refs"] = ["evidence:source", "llm:evidence"]
    payload["risks"][0]["evidence_refs"] = ["llm:evidence"]

    result = validate_llm_analysis_output(payload, {"evidence:source"})

    assert result.accepted is True
    assert result.validation["messages"] == []


def test_validate_llm_analysis_output_normalizes_bad_collections():
    payload = {
        "evidence": {"client_ref": "llm:bad"},
        "findings": [],
        "risks": [],
        "questions": [],
        "recommendations": [],
        "coverage_notes": "not-a-list",
    }

    result = validate_llm_analysis_output(payload, set())

    assert result.accepted is False
    assert result.content["evidence"] == []
    assert result.content["coverage_notes"] == []
    assert "evidence must be a list" in result.validation["messages"]
    assert "coverage_notes must be a list" in result.validation["messages"]


def test_validate_llm_analysis_output_rejects_none_collection_values():
    payload = {
        "evidence": None,
        "findings": [],
        "risks": [],
        "questions": [],
        "recommendations": [],
        "coverage_notes": None,
    }

    result = validate_llm_analysis_output(payload, set())

    assert result.accepted is False
    assert result.content["evidence"] == []
    assert result.content["coverage_notes"] == []
    assert "evidence must be a list" in result.validation["messages"]
    assert "coverage_notes must be a list" in result.validation["messages"]


def test_validate_llm_analysis_output_rejects_missing_required_collections():
    result = validate_llm_analysis_output({}, set())

    assert result.accepted is False
    assert result.content["evidence"] == []
    assert "evidence is required" in result.validation["messages"]
    assert "findings is required" in result.validation["messages"]
    assert "risks is required" in result.validation["messages"]
    assert "questions is required" in result.validation["messages"]
    assert "recommendations is required" in result.validation["messages"]


@pytest.mark.parametrize(
    ("collection", "refs"),
    [
        ("findings", "__missing__"),
        ("findings", None),
        ("findings", []),
        ("risks", "__missing__"),
        ("risks", None),
        ("risks", []),
    ],
)
def test_validate_llm_analysis_output_rejects_missing_or_empty_evidence_refs(collection, refs):
    payload = valid_llm_payload()
    if refs == "__missing__":
        payload[collection][0].pop("evidence_refs")
    else:
        payload[collection][0]["evidence_refs"] = refs

    result = validate_llm_analysis_output(payload, {"evidence:source"})

    assert result.accepted is False
    assert f"{collection}[0].evidence_refs must be a non-empty list" in result.validation["messages"]


@pytest.mark.parametrize(
    "collection",
    ["evidence", "findings", "risks", "questions", "recommendations"],
)
def test_validate_llm_analysis_output_rejects_non_object_items(collection):
    payload = valid_llm_payload()
    payload[collection] = ["not-an-object"]

    result = validate_llm_analysis_output(payload, {"evidence:source"})

    assert result.accepted is False
    assert f"{collection}[0] must be an object" in result.validation["messages"]


def test_validate_llm_analysis_output_keeps_original_item_indexes_in_messages():
    payload = valid_llm_payload()
    payload["evidence"] = [
        "not-an-object",
        {
            "client_ref": "",
            "title": "Evidence",
            "summary": "Evidence summary",
            "evidence_type": "llm_observation",
        },
    ]

    result = validate_llm_analysis_output(payload, {"evidence:source"})

    assert result.accepted is False
    assert "evidence[0] must be an object" in result.validation["messages"]
    assert "evidence[1].client_ref must be a non-empty string" in result.validation["messages"]


@pytest.mark.parametrize(
    ("collection", "field", "value", "message"),
    [
        ("evidence", "client_ref", "", "evidence[0].client_ref must be a non-empty string"),
        ("evidence", "title", None, "evidence[0].title must be a non-empty string"),
        ("evidence", "confidence", 101, "evidence[0].confidence must be between 0 and 100"),
        ("evidence", "metadata", "bad", "evidence[0].metadata must be an object"),
        ("findings", "summary", "", "findings[0].summary must be a non-empty string"),
        ("findings", "confidence", -1, "findings[0].confidence must be between 0 and 100"),
        ("risks", "mitigation", "", "risks[0].mitigation must be a non-empty string"),
        ("questions", "impact", 123, "questions[0].impact must be a non-empty string"),
        ("questions", "status", 42, "questions[0].status must be a string"),
        (
            "recommendations",
            "rationale",
            "",
            "recommendations[0].rationale must be a non-empty string",
        ),
        (
            "recommendations",
            "next_steps",
            ["Validate", 1],
            "recommendations[0].next_steps must be a list of strings",
        ),
    ],
)
def test_validate_llm_analysis_output_rejects_malformed_persistence_fields(
    collection, field, value, message
):
    payload = valid_llm_payload()
    payload[collection][0][field] = value

    result = validate_llm_analysis_output(payload, {"evidence:source"})

    assert result.accepted is False
    assert message in result.validation["messages"]


def test_validate_llm_analysis_output_rejects_deterministic_evidence_client_ref():
    payload = valid_llm_payload()
    payload["evidence"][0]["client_ref"] = "evidence:source"

    result = validate_llm_analysis_output(payload, {"evidence:source"})

    assert result.accepted is False
    assert (
        "evidence[0].client_ref conflicts with existing evidence ref: evidence:source"
        in result.validation["messages"]
    )


def test_validate_llm_analysis_output_rejects_duplicate_llm_evidence_client_refs():
    payload = valid_llm_payload()
    payload["evidence"].append(
        {
            "client_ref": "llm:evidence",
            "title": "Duplicate evidence",
            "summary": "Duplicate evidence summary",
            "evidence_type": "llm_observation",
        }
    )

    result = validate_llm_analysis_output(payload, {"evidence:source"})

    assert result.accepted is False
    assert "Duplicate evidence client_ref: llm:evidence" in result.validation["messages"]


def test_validate_llm_analysis_output_accepts_unique_llm_evidence_client_refs():
    payload = valid_llm_payload()
    payload["evidence"].append(
        {
            "client_ref": "llm:second-evidence",
            "title": "Second evidence",
            "summary": "Second evidence summary",
            "evidence_type": "llm_observation",
        }
    )
    payload["findings"][0]["evidence_refs"].append("llm:second-evidence")

    result = validate_llm_analysis_output(payload, {"evidence:source"})

    assert result.accepted is True
    assert result.validation["messages"] == []


def test_validate_llm_analysis_output_allows_known_and_defined_llm_evidence_refs():
    payload = valid_llm_payload()
    payload["evidence"].extend(
        [
            {
                "client_ref": "llm:derived",
                "title": "Derived finding evidence",
                "summary": "Derived finding evidence summary",
                "evidence_type": "llm_observation",
            },
            {
                "client_ref": "llm:risk",
                "title": "Derived risk evidence",
                "summary": "Derived risk evidence summary",
                "evidence_type": "llm_observation",
            },
        ]
    )
    payload["findings"][0]["evidence_refs"] = ["evidence:source", "llm:derived"]
    payload["risks"][0]["evidence_refs"] = ["llm:risk"]

    result = validate_llm_analysis_output(payload, {"evidence:source"})

    assert result.accepted is True
    assert result.validation["messages"] == []


def test_merge_llm_analysis_marks_all_merged_items_with_source_metadata():
    analysis = {
        "evidence": [{"client_ref": "evidence:source", "metadata": {"source": "deterministic"}}],
        "findings": [],
        "risks": [],
        "questions": [],
        "recommendations": [],
    }
    llm_payload = {
        "evidence": [{"client_ref": "llm:evidence"}],
        "findings": [{"title": "Finding", "metadata": {"model": "test"}}],
        "risks": [{"title": "Risk"}],
        "questions": [{"prompt": "Question?"}],
        "recommendations": [{"summary": "Recommendation"}],
    }

    merged = merge_llm_analysis(analysis, llm_payload)

    for key in ["evidence", "findings", "risks", "questions", "recommendations"]:
        assert merged[key][-1]["metadata"]["source"] == "llm_analyzer"
    assert merged["evidence"][0]["metadata"]["source"] == "deterministic"
    assert "metadata" not in llm_payload["evidence"][0]


def test_merge_llm_analysis_rejects_non_object_llm_items():
    analysis = {
        "evidence": [],
        "findings": [],
        "risks": [],
        "questions": [],
        "recommendations": [],
    }

    with pytest.raises(ValueError, match="LLM analysis item must be an object"):
        merge_llm_analysis(analysis, {"findings": ["not-an-object"]})
