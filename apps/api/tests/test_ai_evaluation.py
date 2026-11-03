import json

import pytest

from app.ai_evaluation import evaluate_cases, load_evaluation_cases
from app.ai_provider import Generation, MockProvider


def test_phase2_evaluation_dataset_passes_with_mock_provider():
    cases = load_evaluation_cases()

    report = evaluate_cases(MockProvider(), cases)

    assert report["case_count"] == 5
    assert report["passed_cases"] == 5
    assert report["checks_passed"] == report["checks_total"]
    assert {case["agent"] for case in report["cases"]} == {
        "market_intelligence",
        "content_strategist",
        "cross_platform_copywriter",
        "creative_director",
        "quality_brand_safety_reviewer",
    }


def test_evaluation_detects_unscoped_citations_and_bad_review():
    class IncorrectProvider(MockProvider):
        def generate(self, agent_name, system_prompt, input_data):
            generation = super().generate(agent_name, system_prompt, input_data)
            if agent_name == "market_intelligence":
                output = dict(generation.output)
                output["findings"] = [{
                    "statement": "An unsupported finding.",
                    "evidence_summary": "No allowed source supports this.",
                    "source_ids": [999],
                    "confidence": 0.9,
                    "classification": "fact",
                }]
            elif agent_name == "quality_brand_safety_reviewer":
                output = dict(generation.output)
                output["recommendation"] = "pass"
                output["findings"] = []
            else:
                output = generation.output
            return Generation(
                output=output,
                provider=generation.provider,
                model=generation.model,
                input_tokens=generation.input_tokens,
                output_tokens=generation.output_tokens,
                usage_is_estimated=generation.usage_is_estimated,
            )

    report = evaluate_cases(IncorrectProvider(), load_evaluation_cases())
    by_id = {case["id"]: case for case in report["cases"]}

    research_checks = {check["name"]: check["passed"] for check in by_id["research-cites-evidence"]["checks"]}
    review_checks = {check["name"]: check["passed"] for check in by_id["reviewer-blocks-prohibited-claim"]["checks"]}
    assert research_checks["source_references_scoped"] is False
    assert research_checks["fact_findings_cited"] is True
    assert review_checks["review_recommendation"] is False
    assert review_checks["review_has_expected_blocking_finding"] is False


def test_evaluation_dataset_rejects_unknown_version(tmp_path):
    path = tmp_path / "evaluation.json"
    path.write_text(json.dumps({"version": 2, "cases": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported"):
        load_evaluation_cases(path)
