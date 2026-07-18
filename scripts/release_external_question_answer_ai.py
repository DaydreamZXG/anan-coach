from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.domains.ai.governance.evaluation import (
    EvaluationObservation,
    evaluate_suite,
    load_golden_manifest,
)
from app.domains.ai.governance.repository import AIGovernanceRepository
from app.domains.ai.model_cost import ModelPriceCard, ModelTokenUsage
from app.domains.external_ingestion.answering.provider import ExternalAIAnswerProvider
from app.domains.external_ingestion.matching.models import MatchQuery


SUITE_VERSION = "stage15-external-question-answer@v1"
SCENARIO_KEY = "external_question_answer"
OPERATION = "external_question_answer_generate"
RELEASE_ID = "stage15-external-question-answer-20260718"
MAXIMUM_MEAN_COST_CNY = Decimal("0.11")
MAXIMUM_P95_COST_CNY = Decimal("0.12")


def main() -> None:
    settings = get_settings()
    provider = ExternalAIAnswerProvider(settings)
    provider.validate_configuration()
    price_card = ModelPriceCard.from_settings(settings)
    suite = next(
        item
        for item in load_golden_manifest().suites
        if item.suite_version == SUITE_VERSION
    )
    with get_session_factory()() as session:
        current = session.execute(
            text(
                """
                select deployment_id
                from ai_audit.scenario_deployments deployment
                where scenario_key = :scenario_key
                  and operation = :operation
                  and provider = :provider
                  and model = :model
                  and prompt_version = :prompt_version
                  and schema_version = :schema_version
                  and evaluation_suite_version = :suite_version
                  and not exists (
                    select 1 from ai_audit.scenario_deployments successor
                    where successor.supersedes_deployment_id = deployment.deployment_id
                  )
                order by published_at desc limit 1
                """
            ),
            {
                "scenario_key": SCENARIO_KEY,
                "operation": OPERATION,
                "provider": provider.provider_name,
                "model": provider.model,
                "prompt_version": provider.prompt_version,
                "schema_version": provider.schema_version,
                "suite_version": SUITE_VERSION,
            },
        ).scalar_one_or_none()
        if current is not None:
            print(f"deployment_ready={current}")
            return

    observations: list[EvaluationObservation] = []
    for case in suite.cases:
        track = str(case.input["track"])
        source_refs = tuple(str(item) for item in case.input["sourceSegmentRefs"])
        result = provider.generate(
            query=MatchQuery(
                track=track,
                promptText=str(case.input["prompt"]),
                materialTexts=("用户上传的材料片段，信息可能不完整。",)
                if track == "essay"
                else (),
            ),
            source_segment_refs=source_refs,
            idempotency_key=f"release-evaluation:{RELEASE_ID}:{case.case_id}",
        )
        cost = price_card.cost(
            ModelTokenUsage(
                input_tokens=result.usage_input_tokens,
                cached_input_tokens=result.usage_cached_input_tokens,
                output_tokens=result.usage_output_tokens,
            )
        ).total_cost_cny
        observations.append(
            EvaluationObservation(
                caseId=case.case_id,
                output=result.output.model_dump(by_alias=True, mode="json"),
                costCny=cost,
            )
        )
    report = evaluate_suite(
        suite=suite,
        observations=tuple(observations),
        maximum_mean_cost_cny=MAXIMUM_MEAN_COST_CNY,
        maximum_p95_cost_cny=MAXIMUM_P95_COST_CNY,
    )
    repository = AIGovernanceRepository()
    with get_session_factory()() as session:
        repository.register_evaluation_suite(session, suite=suite)
        existing_run = session.execute(
            text(
                """
                select evaluation_run_id
                from ai_audit.release_evaluation_runs
                where suite_version = :suite_version
                  and scenario_key = :scenario_key
                  and operation = :operation
                  and provider = :provider
                  and model = :model
                  and prompt_version = :prompt_version
                  and schema_version = :schema_version
                  and report_sha256 = :report_sha256
                """
            ),
            {
                "suite_version": SUITE_VERSION,
                "scenario_key": SCENARIO_KEY,
                "operation": OPERATION,
                "provider": provider.provider_name,
                "model": provider.model,
                "prompt_version": provider.prompt_version,
                "schema_version": provider.schema_version,
                "report_sha256": report.report_sha256,
            },
        ).scalar_one_or_none()
        run_id = (
            UUID(str(existing_run))
            if existing_run is not None
            else repository.record_evaluation_run(
                session,
                report=report,
                operation=OPERATION,
                provider=provider.provider_name,
                model=provider.model,
                model_tier="high",
                prompt_version=provider.prompt_version,
                schema_version=provider.schema_version,
                price_version=price_card.version,
            )
        )
        if report.status != "passed":
            session.commit()
            failures = {
                case.case_id: case.failures
                for case in report.cases
                if not case.passed
            }
            raise RuntimeError(f"release evaluation failed: {failures}")
        deployment_id = repository.publish_scenario_deployment(
            session,
            release_id=RELEASE_ID,
            scenario_key=SCENARIO_KEY,
            operation=OPERATION,
            provider=provider.provider_name,
            model=provider.model,
            prompt_version=provider.prompt_version,
            schema_version=provider.schema_version,
            evaluation_run_id=run_id,
        )
        session.commit()
    print(
        f"deployment_ready={deployment_id} cases={report.passed_count}/{report.case_count} "
        f"mean_cost_cny={report.observed_mean_cost_cny} "
        f"p95_cost_cny={report.observed_p95_cost_cny}"
    )


if __name__ == "__main__":
    main()
