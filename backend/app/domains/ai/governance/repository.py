from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.ai.governance.catalog import (
    PAGE_PERMISSION_POLICY_VERSION,
    PROMPT_INJECTION_POLICY_VERSION,
    ROUTE_POLICY_VERSION,
    scenario_policy,
)
from app.domains.ai.governance.contracts import (
    AICallAttribution,
    EvidenceReference,
    ScenarioKey,
)
from app.domains.ai.governance.retrieval import RetrievalBundle
from app.domains.ai.governance.evaluation import EvaluationReport, GoldenSuite


AI_COST_POLICY_VERSION = "ai-cost-gate@v1"


class AIGovernanceReleaseError(RuntimeError):
    pass


class AIGovernanceRepository:
    def register_evaluation_suite(
        self,
        session: Session,
        *,
        suite: GoldenSuite,
    ) -> None:
        session.execute(
            text(
                """
                insert into ai_audit.evaluation_suite_releases (
                  suite_version, family, case_manifest_sha256, case_count,
                  required_dimensions, created_at
                ) values (
                  :suite_version, :family, :manifest_sha256, :case_count,
                  cast(:required_dimensions as jsonb), :created_at
                )
                on conflict (suite_version) do nothing
                """
            ),
            {
                "suite_version": suite.suite_version,
                "family": suite.family,
                "manifest_sha256": suite.manifest_sha256,
                "case_count": len(suite.cases),
                "required_dimensions": _json(list(suite.required_dimensions)),
                "created_at": datetime.now(UTC),
            },
        )
        stored = session.execute(
            text(
                "select family, case_manifest_sha256, case_count, required_dimensions "
                "from ai_audit.evaluation_suite_releases "
                "where suite_version = :suite_version"
            ),
            {"suite_version": suite.suite_version},
        ).mappings().one()
        if (
            str(stored["family"]),
            str(stored["case_manifest_sha256"]),
            int(stored["case_count"]),
            tuple(stored["required_dimensions"]),
        ) != (
            suite.family,
            suite.manifest_sha256,
            len(suite.cases),
            suite.required_dimensions,
        ):
            raise AIGovernanceReleaseError(
                "Evaluation suite version already names a different golden manifest"
            )

    def record_evaluation_run(
        self,
        session: Session,
        *,
        report: EvaluationReport,
        operation: str,
        provider: str,
        model: str,
        model_tier: str,
        prompt_version: str,
        schema_version: str,
        price_version: str,
    ) -> UUID:
        run_id = uuid4()
        session.execute(
            text(
                """
                insert into ai_audit.release_evaluation_runs (
                  evaluation_run_id, suite_version, scenario_key, operation,
                  provider, model, model_tier, prompt_version, schema_version,
                  price_version, case_count, passed_count, dimension_results,
                  observed_total_cost_cny, observed_mean_cost_cny,
                  observed_p95_cost_cny, maximum_mean_cost_cny,
                  maximum_p95_cost_cny, report_sha256, status, evaluated_at
                ) values (
                  cast(:run_id as uuid), :suite_version, :scenario_key, :operation,
                  :provider, :model, :model_tier, :prompt_version, :schema_version,
                  :price_version, :case_count, :passed_count,
                  cast(:dimension_results as jsonb), :observed_total_cost_cny,
                  :observed_mean_cost_cny, :observed_p95_cost_cny,
                  :maximum_mean_cost_cny, :maximum_p95_cost_cny,
                  :report_sha256, :status, :evaluated_at
                )
                """
            ),
            {
                "run_id": str(run_id),
                "suite_version": report.suite_version,
                "scenario_key": report.scenario_key,
                "operation": operation,
                "provider": provider,
                "model": model,
                "model_tier": model_tier,
                "prompt_version": prompt_version,
                "schema_version": schema_version,
                "price_version": price_version,
                "case_count": report.case_count,
                "passed_count": report.passed_count,
                "dimension_results": _json(report.dimension_results),
                "observed_total_cost_cny": report.observed_total_cost_cny,
                "observed_mean_cost_cny": report.observed_mean_cost_cny,
                "observed_p95_cost_cny": report.observed_p95_cost_cny,
                "maximum_mean_cost_cny": report.maximum_mean_cost_cny,
                "maximum_p95_cost_cny": report.maximum_p95_cost_cny,
                "report_sha256": report.report_sha256,
                "status": report.status,
                "evaluated_at": datetime.now(UTC),
            },
        )
        return run_id

    def publish_scenario_deployment(
        self,
        session: Session,
        *,
        release_id: str,
        scenario_key: ScenarioKey,
        operation: str,
        provider: str,
        model: str,
        prompt_version: str,
        schema_version: str,
        evaluation_run_id: UUID,
        supersedes_deployment_id: UUID | None = None,
    ) -> UUID:
        policy = scenario_policy(scenario_key)
        deployment_id = uuid4()
        identity = {
            "releaseId": release_id,
            "scenarioKey": scenario_key,
            "operation": operation,
            "provider": provider,
            "model": model,
            "promptVersion": prompt_version,
            "schemaVersion": schema_version,
            "evaluationRunId": str(evaluation_run_id),
            "evaluationSuiteVersion": policy.evaluation_suite_version,
            "routePolicyVersion": ROUTE_POLICY_VERSION,
            "permissionPolicyVersion": PAGE_PERMISSION_POLICY_VERSION,
            "injectionPolicyVersion": PROMPT_INJECTION_POLICY_VERSION,
            "costPolicyVersion": AI_COST_POLICY_VERSION,
        }
        session.execute(
            text(
                """
                insert into ai_audit.scenario_deployments (
                  deployment_id, release_id, scenario_key, operation,
                  responding_agent, impact_level, model_tier, provider, model,
                  prompt_version, schema_version, route_policy_version,
                  permission_policy_version, injection_policy_version,
                  cost_policy_version, evaluation_run_id, deployment_fingerprint,
                  evaluation_suite_version, supersedes_deployment_id, published_at
                ) values (
                  cast(:deployment_id as uuid), :release_id, :scenario_key,
                  :operation, :responding_agent, :impact_level, :model_tier,
                  :provider, :model, :prompt_version, :schema_version,
                  :route_policy_version, :permission_policy_version,
                  :injection_policy_version, :cost_policy_version,
                  cast(:evaluation_run_id as uuid), :deployment_fingerprint,
                  :evaluation_suite_version,
                  cast(:supersedes_deployment_id as uuid), :published_at
                )
                """
            ),
            {
                "deployment_id": str(deployment_id),
                "release_id": release_id,
                "scenario_key": scenario_key,
                "operation": operation,
                "responding_agent": policy.responding_agent,
                "impact_level": policy.impact_level,
                "model_tier": policy.model_tier,
                "provider": provider,
                "model": model,
                "prompt_version": prompt_version,
                "schema_version": schema_version,
                "route_policy_version": ROUTE_POLICY_VERSION,
                "permission_policy_version": PAGE_PERMISSION_POLICY_VERSION,
                "injection_policy_version": PROMPT_INJECTION_POLICY_VERSION,
                "cost_policy_version": AI_COST_POLICY_VERSION,
                "evaluation_run_id": str(evaluation_run_id),
                "deployment_fingerprint": deployment_fingerprint(identity),
                "evaluation_suite_version": policy.evaluation_suite_version,
                "supersedes_deployment_id": (
                    str(supersedes_deployment_id)
                    if supersedes_deployment_id is not None
                    else None
                ),
                "published_at": datetime.now(UTC),
            },
        )
        return deployment_id

    def record_retrieval_bundle(
        self,
        session: Session,
        *,
        user_id: str,
        scenario_key: ScenarioKey,
        route_id: str,
        workspace: str,
        bundle: RetrievalBundle,
    ) -> None:
        source_refs = [
            {
                **item.reference.model_dump(by_alias=True, mode="json"),
                "trustScope": item.trust_scope,
                "lexicalScore": item.lexical_score,
                "semanticScore": item.semantic_score,
                "sourceWeight": item.source_weight,
                "finalScore": item.final_score,
                "untrustedInput": item.untrusted_input,
            }
            for item in bundle.sources
        ]
        blocked = [
            item.model_dump(by_alias=True, mode="json")
            for item in bundle.blocked_source_refs
        ]
        session.execute(
            text(
                """
                insert into ai_audit.retrieval_bundle_events (
                  retrieval_bundle_id, user_id, scenario_key, route_id, workspace,
                  policy_version, query_fingerprint, source_refs,
                  blocked_source_refs, security_decision, bundle_fingerprint,
                  created_at
                ) values (
                  cast(:retrieval_bundle_id as uuid), cast(:user_id as uuid),
                  :scenario_key, :route_id, :workspace, :policy_version,
                  :query_fingerprint, cast(:source_refs as jsonb),
                  cast(:blocked_source_refs as jsonb), :security_decision,
                  :bundle_fingerprint, :created_at
                )
                on conflict (retrieval_bundle_id) do nothing
                """
            ),
            {
                "retrieval_bundle_id": str(bundle.bundle_id),
                "user_id": user_id,
                "scenario_key": scenario_key,
                "route_id": route_id,
                "workspace": workspace,
                "policy_version": bundle.policy_version,
                "query_fingerprint": bundle.query_fingerprint,
                "source_refs": _json(source_refs),
                "blocked_source_refs": _json(blocked),
                "security_decision": bundle.security_decision,
                "bundle_fingerprint": bundle.fingerprint,
                "created_at": datetime.now(UTC),
            },
        )

    def attribution(
        self,
        session: Session,
        *,
        environment: str,
        scenario_key: ScenarioKey,
        operation: str,
        provider: str,
        model: str,
        prompt_version: str,
        schema_version: str,
        evidence_refs: tuple[EvidenceReference, ...],
        retrieval_bundle_id: UUID | None,
        reservation_id: UUID | None,
        reservation_source: str | None,
    ) -> AICallAttribution:
        policy = scenario_policy(scenario_key)
        deployment_id: UUID | None = None
        evaluation_state = "local_unreleased"
        if environment.strip().casefold() == "production":
            row = session.execute(
                text(
                    """
                    select deployment.deployment_id
                    from ai_audit.scenario_deployments deployment
                    where deployment.scenario_key = :scenario_key
                      and deployment.operation = :operation
                      and deployment.provider = :provider
                      and deployment.model = :model
                      and deployment.prompt_version = :prompt_version
                      and deployment.schema_version = :schema_version
                      and deployment.responding_agent = :responding_agent
                      and deployment.impact_level = :impact_level
                      and deployment.model_tier = :model_tier
                      and deployment.route_policy_version = :route_policy_version
                      and deployment.permission_policy_version =
                          :permission_policy_version
                      and deployment.injection_policy_version =
                          :injection_policy_version
                      and deployment.evaluation_suite_version =
                          :evaluation_suite_version
                      and not exists (
                        select 1 from ai_audit.scenario_deployments successor
                        where successor.supersedes_deployment_id =
                              deployment.deployment_id
                      )
                    order by deployment.published_at desc
                    limit 1
                    """
                ),
                {
                    "scenario_key": scenario_key,
                    "operation": operation,
                    "provider": provider,
                    "model": model,
                    "prompt_version": prompt_version,
                    "schema_version": schema_version,
                    "responding_agent": policy.responding_agent,
                    "impact_level": policy.impact_level,
                    "model_tier": policy.model_tier,
                    "route_policy_version": ROUTE_POLICY_VERSION,
                    "permission_policy_version": PAGE_PERMISSION_POLICY_VERSION,
                    "injection_policy_version": PROMPT_INJECTION_POLICY_VERSION,
                    "evaluation_suite_version": policy.evaluation_suite_version,
                },
            ).mappings().one_or_none()
            if row is None:
                raise AIGovernanceReleaseError(
                    "No evaluated production deployment matches this AI call identity"
                )
            deployment_id = UUID(str(row["deployment_id"]))
            evaluation_state = "passed"
        return AICallAttribution(
            scenarioKey=scenario_key,
            operation=operation,
            respondingAgent=policy.responding_agent,
            impactLevel=policy.impact_level,
            modelTier=policy.model_tier,
            deploymentId=deployment_id,
            evaluationState=evaluation_state,
            routePolicyVersion=ROUTE_POLICY_VERSION,
            permissionPolicyVersion=PAGE_PERMISSION_POLICY_VERSION,
            injectionPolicyVersion=PROMPT_INJECTION_POLICY_VERSION,
            evidenceRefs=evidence_refs,
            retrievalBundleId=retrieval_bundle_id,
            reservationId=reservation_id,
            reservationSource=reservation_source,
            entitlementState=("reserved" if reservation_id else "not_applicable"),
        )

    def insert_model_call_attribution(
        self,
        session: Session,
        *,
        call_id: UUID,
        attribution: AICallAttribution,
    ) -> None:
        payload = attribution.model_dump(by_alias=True, mode="json")
        session.execute(
            text(
                """
                insert into ai_audit.model_call_attributions (
                  call_id, scenario_key, operation, responding_agent,
                  impact_level, model_tier, deployment_id, evaluation_state,
                  route_policy_version, permission_policy_version,
                  injection_policy_version, evidence_refs, evidence_fingerprint,
                  retrieval_bundle_id, reservation_id, reservation_source,
                  entitlement_state, created_at
                ) values (
                  cast(:call_id as uuid), :scenario_key, :operation,
                  :responding_agent, :impact_level, :model_tier,
                  cast(:deployment_id as uuid), :evaluation_state,
                  :route_policy_version, :permission_policy_version,
                  :injection_policy_version, cast(:evidence_refs as jsonb),
                  :evidence_fingerprint, cast(:retrieval_bundle_id as uuid),
                  cast(:reservation_id as uuid), :reservation_source,
                  :entitlement_state, :created_at
                )
                on conflict (call_id) do nothing
                """
            ),
            {
                "call_id": str(call_id),
                "scenario_key": payload["scenarioKey"],
                "operation": payload["operation"],
                "responding_agent": payload["respondingAgent"],
                "impact_level": payload["impactLevel"],
                "model_tier": payload["modelTier"],
                "deployment_id": payload["deploymentId"],
                "evaluation_state": payload["evaluationState"],
                "route_policy_version": payload["routePolicyVersion"],
                "permission_policy_version": payload["permissionPolicyVersion"],
                "injection_policy_version": payload["injectionPolicyVersion"],
                "evidence_refs": _json(payload["evidenceRefs"]),
                "evidence_fingerprint": attribution.evidence_fingerprint,
                "retrieval_bundle_id": payload["retrievalBundleId"],
                "reservation_id": payload["reservationId"],
                "reservation_source": payload["reservationSource"],
                "entitlement_state": payload["entitlementState"],
                "created_at": datetime.now(UTC),
            },
        )
        stored = session.execute(
            text(
                """
                select scenario_key, operation, evidence_fingerprint,
                       deployment_id, evaluation_state, reservation_id,
                       reservation_source
                from ai_audit.model_call_attributions
                where call_id = cast(:call_id as uuid)
                """
            ),
            {"call_id": str(call_id)},
        ).mappings().one()
        expected = (
            attribution.scenario_key,
            attribution.operation,
            attribution.evidence_fingerprint,
            attribution.deployment_id,
            attribution.evaluation_state,
            attribution.reservation_id,
            attribution.reservation_source,
        )
        actual = (
            str(stored["scenario_key"]),
            str(stored["operation"]),
            str(stored["evidence_fingerprint"]),
            (
                UUID(str(stored["deployment_id"]))
                if stored["deployment_id"] is not None
                else None
            ),
            str(stored["evaluation_state"]),
            (
                UUID(str(stored["reservation_id"]))
                if stored["reservation_id"] is not None
                else None
            ),
            (
                str(stored["reservation_source"])
                if stored["reservation_source"] is not None
                else None
            ),
        )
        if actual != expected:
            raise AIGovernanceReleaseError(
                "Stable model-call key refers to different AI governance attribution"
            )


def generic_evidence_ref(*, kind: str, source_id: str, version: str) -> EvidenceReference:
    return EvidenceReference(
        kind=kind,
        sourceId=source_id,
        version=version,
        anchor="root",
        title=kind.replace("_", " "),
    )


def deployment_fingerprint(value: dict[str, Any]) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
