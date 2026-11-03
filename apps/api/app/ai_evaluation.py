import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.ai_provider import AIProviderError, StructuredProvider
from app.ai_schemas import (
    CopyOutput,
    CreativeOutput,
    ResearchOutput,
    ReviewOutput,
    StrategyOutput,
)
from app.ai_workflows import SYSTEM_PROMPTS

EVALUATION_DATASET = Path(__file__).resolve().parent.parent / "evaluations" / "phase2.json"
OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    "market_intelligence": ResearchOutput,
    "content_strategist": StrategyOutput,
    "cross_platform_copywriter": CopyOutput,
    "creative_director": CreativeOutput,
    "quality_brand_safety_reviewer": ReviewOutput,
}


def load_evaluation_cases(path: Path = EVALUATION_DATASET) -> list[dict[str, Any]]:
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Unable to load AI evaluation dataset: {path}") from error
    if not isinstance(dataset, dict) or dataset.get("version") != 1:
        raise ValueError("Unsupported AI evaluation dataset version.")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("AI evaluation dataset must contain at least one case.")
    for case in cases:
        if (
            not isinstance(case, dict)
            or not isinstance(case.get("id"), str)
            or case.get("agent") not in OUTPUT_SCHEMAS
            or not isinstance(case.get("input"), dict)
            or not isinstance(case.get("allowed_source_ids"), list)
            or not isinstance(case.get("requirements", {}), dict)
        ):
            raise ValueError("AI evaluation dataset contains an invalid case.")
    return cases


def evaluate_cases(
    provider: StructuredProvider,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    results = []
    for case in cases:
        agent = case["agent"]
        output_schema = OUTPUT_SCHEMAS[agent]
        system_prompt = (
            SYSTEM_PROMPTS[agent]
            + "\nRequired output schema (JSON Schema): "
            + json.dumps(output_schema.model_json_schema(), separators=(",", ":"))
        )
        try:
            generation = provider.generate(agent, system_prompt, case["input"])
        except AIProviderError as error:
            checks = [{"name": "provider_generation", "passed": False, "detail": error.code}]
            results.append(_case_result(case, checks, provider.name, provider.model))
            continue

        try:
            output = output_schema.model_validate(generation.output)
        except ValidationError:
            checks = [{"name": "structured_output_valid", "passed": False, "detail": "schema_validation_failed"}]
        else:
            checks = [{"name": "structured_output_valid", "passed": True, "detail": "ok"}]
            checks.extend(_evaluate_requirements(case, output))
        results.append(_case_result(case, checks, generation.provider, generation.model))

    checks_total = sum(len(result["checks"]) for result in results)
    checks_passed = sum(
        check["passed"] for result in results for check in result["checks"]
    )
    return {
        "dataset_version": 1,
        "provider": provider.name,
        "model": provider.model,
        "case_count": len(results),
        "passed_cases": sum(result["passed"] for result in results),
        "checks_passed": checks_passed,
        "checks_total": checks_total,
        "pass_rate": round(checks_passed / checks_total, 4) if checks_total else 0,
        "cases": results,
    }


def _case_result(
    case: dict[str, Any],
    checks: list[dict[str, Any]],
    provider_name: str,
    model: str,
) -> dict[str, Any]:
    return {
        "id": case["id"],
        "agent": case["agent"],
        "provider": provider_name,
        "model": model,
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


def _evaluate_requirements(
    case: dict[str, Any],
    output: BaseModel,
) -> list[dict[str, Any]]:
    data = output.model_dump(mode="json")
    requirements = case.get("requirements", {})
    checks = [
        _check(
            "source_references_scoped",
            all(
                source_id in case["allowed_source_ids"]
                for source_id in _source_references(data)
            ),
            "all citations belong to the case's allowed source set",
        )
    ]

    if requirements.get("fact_findings_require_citations"):
        facts = data.get("findings", [])
        checks.append(_check(
            "fact_findings_cited",
            all(
                finding.get("classification") != "fact"
                or bool(finding.get("source_ids"))
                for finding in facts
            ),
            "each finding classified as fact has at least one source",
        ))

    if "minimum_message_pillars" in requirements:
        checks.append(_check(
            "minimum_message_pillars",
            len(data.get("message_pillars", [])) >= requirements["minimum_message_pillars"],
            "strategy contains the requested minimum number of pillars",
        ))

    forbidden_phrases = requirements.get("forbidden_copy_phrases", [])
    if forbidden_phrases:
        copy_text = " ".join(
            str(data.get(field, ""))
            for field in ("title", "hook", "body", "call_to_action")
        ).casefold()
        checks.append(_check(
            "prohibited_copy_phrases_absent",
            all(phrase.casefold() not in copy_text for phrase in forbidden_phrases),
            "configured prohibited phrases do not appear in generated copy",
        ))

    if requirements.get("creative_concepts_require_visual_prompts"):
        concepts = data.get("concepts", [])
        checks.append(_check(
            "creative_concepts_have_visual_prompts",
            bool(concepts) and all(concept.get("visual_prompt") for concept in concepts),
            "every creative concept includes a visual prompt",
        ))

    expected_recommendation = requirements.get("expected_recommendation")
    if expected_recommendation:
        checks.append(_check(
            "review_recommendation",
            data.get("recommendation") == expected_recommendation,
            f"review recommendation is {expected_recommendation}",
        ))

    expected_blocking_code = requirements.get("expected_blocking_code")
    if expected_blocking_code:
        checks.append(_check(
            "review_has_expected_blocking_finding",
            any(
                finding.get("severity") == "blocking"
                and finding.get("code") == expected_blocking_code
                for finding in data.get("findings", [])
            ),
            f"review contains blocking finding {expected_blocking_code}",
        ))
    return checks


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def _source_references(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_ids" and isinstance(child, list):
                yield from child
            else:
                yield from _source_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _source_references(child)
