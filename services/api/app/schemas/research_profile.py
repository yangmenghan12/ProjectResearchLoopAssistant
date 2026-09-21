from pydantic import BaseModel


class ResearchProfileRead(BaseModel):
    key: str
    label: str
    description: str
    checklist: list[str]
    quality_weights: dict[str, float]
    verifier_checks: list[str]
    report_sections: list[str]
    required_evidence_types: list[str]
