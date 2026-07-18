from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.ai.governance.catalog import all_scenario_policies
from app.domains.ai.governance.contracts import CompanionIntent, ScenarioKey


GOLDEN_MANIFEST_PATH = (
    Path(__file__).resolve().parents[4] / "evaluation" / "goldens" / "stage7.json"
)


class EvaluationContractError(RuntimeError):
    pass


class EvaluationModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class GoldenCase(EvaluationModel):
    case_id: str = Field(alias="caseId", min_length=1, max_length=120)
    input: dict[str, Any]
    required_keys: tuple[str, ...] = Field(alias="requiredKeys", min_length=1)
    allowed_evidence_refs: tuple[str, ...] = Field(
        alias="allowedEvidenceRefs", min_length=1
    )
    required_evidence_refs: tuple[str, ...] = Field(
        default=(), alias="requiredEvidenceRefs"
    )
    forbidden_phrases: tuple[str, ...] = Field(default=(), alias="forbiddenPhrases")
    expected_intent: CompanionIntent | None = Field(default=None, alias="expectedIntent")
    expected_normalized_text: str | None = Field(
        default=None, alias="expectedNormalizedText"
    )
    maximum_cost_cny: Decimal = Field(alias="maximumCostCny", ge=0)
    reference_output: dict[str, Any] = Field(alias="referenceOutput")

    @model_validator(mode="after")
    def coherent(self) -> "GoldenCase":
        if not set(self.required_evidence_refs).issubset(self.allowed_evidence_refs):
            raise ValueError("required evidence must be a subset of allowed evidence")
        if len(self.allowed_evidence_refs) != len(set(self.allowed_evidence_refs)):
            raise ValueError("allowed evidence references must be unique")
        return self


class GoldenSuite(EvaluationModel):
    suite_version: str = Field(alias="suiteVersion", min_length=1, max_length=160)
    family: Literal[
        "xingce_cause",
        "essay",
        "companion_tone",
        "strategic_deprioritization",
        "ocr",
        "external_question_answer",
    ]
    scenario_key: ScenarioKey = Field(alias="scenarioKey")
    required_dimensions: tuple[
        Literal["contract", "grounding", "forbidden_language", "task_expectation"],
        ...,
    ] = Field(alias="requiredDimensions", min_length=1)
    cases: tuple[GoldenCase, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def unique_cases(self) -> "GoldenSuite":
        ids = [item.case_id for item in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("golden case IDs must be unique within a suite")
        return self

    @property
    def manifest_sha256(self) -> str:
        return _fingerprint(self.model_dump(by_alias=True, mode="json"))


class GoldenManifest(EvaluationModel):
    contract_version: Literal["stage7-golden-manifest@v1"] = Field(
        alias="contractVersion"
    )
    suites: tuple[GoldenSuite, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def covers_catalog(self) -> "GoldenManifest":
        versions = [suite.suite_version for suite in self.suites]
        if len(versions) != len(set(versions)):
            raise ValueError("golden suite versions must be unique")
        required = {
            policy.evaluation_suite_version for policy in all_scenario_policies()
        }
        missing = sorted(required - set(versions))
        if missing:
            raise ValueError("golden manifest misses catalog suites: " + ", ".join(missing))
        return self


class EvaluationObservation(EvaluationModel):
    case_id: str = Field(alias="caseId")
    output: dict[str, Any]
    cost_cny: Decimal = Field(alias="costCny", ge=0)


class CaseEvaluation(EvaluationModel):
    case_id: str = Field(alias="caseId")
    passed: bool
    dimensions: dict[str, bool]
    failures: tuple[str, ...]
    cost_cny: Decimal = Field(alias="costCny")


class EvaluationReport(EvaluationModel):
    suite_version: str = Field(alias="suiteVersion")
    scenario_key: ScenarioKey = Field(alias="scenarioKey")
    case_count: int = Field(alias="caseCount", gt=0)
    passed_count: int = Field(alias="passedCount", ge=0)
    dimension_results: dict[str, bool] = Field(alias="dimensionResults")
    observed_total_cost_cny: Decimal = Field(alias="observedTotalCostCny")
    observed_mean_cost_cny: Decimal = Field(alias="observedMeanCostCny")
    observed_p95_cost_cny: Decimal = Field(alias="observedP95CostCny")
    maximum_mean_cost_cny: Decimal = Field(alias="maximumMeanCostCny")
    maximum_p95_cost_cny: Decimal = Field(alias="maximumP95CostCny")
    status: Literal["passed", "failed"]
    cases: tuple[CaseEvaluation, ...]
    report_sha256: str = Field(alias="reportSha256")


def load_golden_manifest(path: Path = GOLDEN_MANIFEST_PATH) -> GoldenManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationContractError(f"golden manifest is unreadable: {path}") from exc
    return GoldenManifest.model_validate(raw)


def evaluate_suite(
    *,
    suite: GoldenSuite,
    observations: tuple[EvaluationObservation, ...],
    maximum_mean_cost_cny: Decimal,
    maximum_p95_cost_cny: Decimal,
) -> EvaluationReport:
    by_id = {item.case_id: item for item in observations}
    expected_ids = {item.case_id for item in suite.cases}
    if set(by_id) != expected_ids or len(by_id) != len(observations):
        raise EvaluationContractError(
            "evaluation observations must match every golden case exactly once"
        )
    results = tuple(_evaluate_case(case, by_id[case.case_id]) for case in suite.cases)
    costs = sorted(item.cost_cny for item in results)
    observed_total = sum(costs, Decimal("0"))
    observed_mean = observed_total / Decimal(len(costs))
    observed_p95 = costs[max(0, ((95 * len(costs) + 99) // 100) - 1)]
    dimensions = {
        dimension: all(item.dimensions[dimension] for item in results)
        for dimension in suite.required_dimensions
    }
    quality_passed = all(item.passed for item in results) and all(dimensions.values())
    cost_passed = (
        observed_mean <= maximum_mean_cost_cny
        and observed_p95 <= maximum_p95_cost_cny
    )
    report_payload = {
        "suiteVersion": suite.suite_version,
        "scenarioKey": suite.scenario_key,
        "cases": [item.model_dump(by_alias=True, mode="json") for item in results],
        "dimensionResults": dimensions,
        "costs": [str(value) for value in costs],
        "maximumMeanCostCny": str(maximum_mean_cost_cny),
        "maximumP95CostCny": str(maximum_p95_cost_cny),
    }
    return EvaluationReport(
        suiteVersion=suite.suite_version,
        scenarioKey=suite.scenario_key,
        caseCount=len(results),
        passedCount=sum(item.passed for item in results),
        dimensionResults=dimensions,
        observedTotalCostCny=observed_total,
        observedMeanCostCny=observed_mean,
        observedP95CostCny=observed_p95,
        maximumMeanCostCny=maximum_mean_cost_cny,
        maximumP95CostCny=maximum_p95_cost_cny,
        status="passed" if quality_passed and cost_passed else "failed",
        cases=results,
        reportSha256=_fingerprint(report_payload),
    )


def reference_observations(suite: GoldenSuite) -> tuple[EvaluationObservation, ...]:
    """Build deterministic self-test observations; never substitutes for release evaluation."""

    return tuple(
        EvaluationObservation(
            caseId=case.case_id,
            output=case.reference_output,
            costCny=case.maximum_cost_cny,
        )
        for case in suite.cases
    )


def _evaluate_case(case: GoldenCase, observation: EvaluationObservation) -> CaseEvaluation:
    output = observation.output
    contract = all(_path_exists(output, key) for key in case.required_keys)
    refs = _evidence_refs(output)
    grounding = bool(refs) and set(refs).issubset(case.allowed_evidence_refs)
    grounding = grounding and set(case.required_evidence_refs).issubset(refs)
    serialized = json.dumps(output, ensure_ascii=False, sort_keys=True)
    forbidden = all(phrase.casefold() not in serialized.casefold() for phrase in case.forbidden_phrases)
    task = observation.cost_cny <= case.maximum_cost_cny
    if case.expected_intent is not None:
        task = task and output.get("companionIntent") == case.expected_intent
    if case.expected_normalized_text is not None:
        task = task and _normalize_text(case.expected_normalized_text) in _normalize_text(
            _recognized_text(output)
        )
    dimensions = {
        "contract": contract,
        "grounding": grounding,
        "forbidden_language": forbidden,
        "task_expectation": task,
    }
    failures = tuple(name for name, passed in dimensions.items() if not passed)
    return CaseEvaluation(
        caseId=case.case_id,
        passed=all(dimensions.values()),
        dimensions=dimensions,
        failures=failures,
        costCny=observation.cost_cny,
    )


def _path_exists(value: dict[str, Any], path: str) -> bool:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return current is not None


def _evidence_refs(output: dict[str, Any]) -> tuple[str, ...]:
    refs = output.get(
        "evidenceRefs", output.get("sourceRefs", output.get("sourceSegmentRefs", ()))
    )
    if not isinstance(refs, list) or any(not isinstance(item, str) for item in refs):
        return ()
    return tuple(refs)


def _recognized_text(output: dict[str, Any]) -> str:
    spans = output.get("textSpans")
    if not isinstance(spans, list):
        return ""
    return "".join(
        str(item.get("text", "")) for item in spans if isinstance(item, dict)
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.casefold())


def _fingerprint(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
