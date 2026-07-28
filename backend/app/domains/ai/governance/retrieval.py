from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.ai.governance.contracts import EvidenceReference, GlobalAskContext
from app.domains.ai.governance.routing import prompt_injection_detected
from app.domains.private_knowledge.projection import (
    PrivateKnowledgeProjectionGateway,
    build_projection_gateway,
)


RETRIEVAL_POLICY_VERSION = "authorized-hybrid-retrieval@v1"


class RetrievalModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class RetrievedSource(RetrievalModel):
    reference: EvidenceReference
    trust_scope: str = Field(alias="trustScope", min_length=1, max_length=80)
    excerpt: str = Field(min_length=1, max_length=2000)
    display_summary: str | None = Field(
        default=None,
        alias="displaySummary",
        min_length=1,
        max_length=500,
    )
    lexical_score: float = Field(alias="lexicalScore", ge=0, le=1)
    semantic_score: float = Field(alias="semanticScore", ge=0, le=1)
    source_weight: float = Field(alias="sourceWeight", ge=0, le=1)
    final_score: float = Field(alias="finalScore", ge=0, le=1)
    untrusted_input: bool = Field(alias="untrustedInput")


class RetrievalBundle(RetrievalModel):
    bundle_id: UUID = Field(alias="bundleId")
    policy_version: str = Field(default=RETRIEVAL_POLICY_VERSION, alias="policyVersion")
    query_fingerprint: str = Field(alias="queryFingerprint", pattern=r"^[0-9a-f]{64}$")
    sources: tuple[RetrievedSource, ...] = Field(max_length=12)
    blocked_source_refs: tuple[EvidenceReference, ...] = Field(
        default=(), alias="blockedSourceRefs", max_length=50
    )
    security_decision: str = Field(alias="securityDecision")

    @model_validator(mode="after")
    def has_unique_sources(self) -> "RetrievalBundle":
        identities = [item.reference.source_id for item in self.sources]
        if len(identities) != len(set(identities)):
            raise ValueError("retrieval sources must be deduplicated")
        return self

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.model_dump(by_alias=True, mode="json"))


class ObjectAuthorizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Candidate:
    reference: EvidenceReference
    text: str
    trust_scope: str
    source_weight: float
    untrusted_input: bool
    display_summary: str | None = None
    retrieval_score: float = 0.0


class AuthorizedHybridRetriever:
    """Owner-scoped full-text + structured-semantic retrieval with MMR deduplication.

    Canonical content remains in its owning domain. This component reads current
    immutable versions and emits a disposable retrieval bundle; it never creates a
    second source of truth or lets retrieved text alter system permissions.
    """

    def __init__(
        self,
        session: Session,
        *,
        private_projection: PrivateKnowledgeProjectionGateway | None = None,
        resolve_projection_from_environment: bool = True,
    ) -> None:
        self._session = session
        self._private_projection = (
            build_projection_gateway(session)
            if resolve_projection_from_environment and private_projection is None
            else private_projection
        )

    def retrieve(
        self,
        *,
        user_id: str,
        question: str,
        context: GlobalAskContext,
        context_sources: tuple[_Candidate, ...] = (),
        maximum_sources: int = 8,
    ) -> RetrievalBundle:
        if not 1 <= maximum_sources <= 12:
            raise ValueError("maximum_sources must be between 1 and 12")
        self._authorize_object_refs(user_id=user_id, context=context)
        explicit_candidates = self._explicit_object_candidates(
            user_id=user_id,
            context=context,
        )
        private_candidates = (
            self._projected_private_materials(
                user_id=user_id,
                question=question,
                maximum_sources=12,
            )
            if self._private_projection is not None
            else self._confirmed_private_materials(user_id=user_id)
        )
        candidates = [*explicit_candidates, *context_sources, *private_candidates]
        query_terms = _terms(question)
        ranked: list[tuple[float, _Candidate, float, float]] = []
        blocked: list[EvidenceReference] = []
        for candidate in candidates:
            if candidate.untrusted_input and prompt_injection_detected(candidate.text):
                blocked.append(candidate.reference)
                continue
            lexical = _jaccard(query_terms, _terms(candidate.text))
            semantic = max(_semantic_overlap(question, candidate), candidate.retrieval_score)
            score = min(
                1.0,
                (0.55 * lexical) + (0.25 * semantic) + (0.20 * candidate.source_weight),
            )
            if score > 0.20 or candidate.trust_scope in {
                "learning_brain",
                "target_position",
                "authorized_question",
                "owned_attempt",
                "authorized_essay_review",
                "authorized_plan",
                "authorized_position",
                "authorized_report",
            }:
                ranked.append((score, candidate, lexical, semantic))
        ranked.sort(key=lambda item: (-item[0], item[1].reference.source_id))
        selected = _mmr_select(ranked, maximum_sources=maximum_sources)
        sources = tuple(
            RetrievedSource(
                reference=candidate.reference,
                trustScope=candidate.trust_scope,
                excerpt=_excerpt(candidate.text),
                displaySummary=candidate.display_summary,
                lexicalScore=round(lexical, 6),
                semanticScore=round(semantic, 6),
                sourceWeight=candidate.source_weight,
                finalScore=round(score, 6),
                untrustedInput=candidate.untrusted_input,
            )
            for score, candidate, lexical, semantic in selected
        )
        return RetrievalBundle(
            bundleId=uuid4(),
            queryFingerprint=_fingerprint({"question": question}),
            sources=sources,
            blockedSourceRefs=tuple(blocked),
            securityDecision=(
                "sources_blocked"
                if blocked and sources
                else "all_sources_blocked"
                if blocked
                else "clear"
            ),
        )

    def _authorize_object_refs(self, *, user_id: str, context: GlobalAskContext) -> None:
        for item in context.object_refs:
            if not self._object_ref_owned(
                user_id=user_id,
                object_type=item.type,
                object_id=item.id,
                version=item.version,
            ):
                raise ObjectAuthorizationError(f"unauthorized_{item.type}_reference")

    def _object_ref_owned(
        self,
        *,
        user_id: str,
        object_type: str,
        object_id: str,
        version: str,
    ) -> bool:
        if object_type == "question":
            return self._question_ref_owned(
                user_id=user_id,
                question_id=object_id,
                version=version,
            )
        if object_type == "attempt":
            return self._attempt_ref_owned(
                user_id=user_id,
                attempt_id=object_id,
                version=version,
            )
        statements = {
            "position": """
                select exists (
                  select 1 from learner.position_goal_versions goal
                  where goal.user_id = cast(:user_id as uuid)
                    and goal.position_id = cast(:object_id as uuid)
                    and goal.goal_version_id = cast(:version as uuid)
                  union all
                  select 1 from learner.eligibility_snapshots eligibility
                  where eligibility.user_id = cast(:user_id as uuid)
                    and eligibility.position_id = cast(:object_id as uuid)
                    and eligibility.snapshot_id = cast(:version as uuid)
                )
            """,
            "plan": """
                select exists (
                  select 1 from learning.daily_plan_versions plan
                  where plan.user_id = cast(:user_id as uuid)
                    and plan.plan_version_id = cast(:object_id as uuid)
                    and plan.version_no::text = :version
                  union all
                  select 1 from learning.weekly_plan_versions plan
                  where plan.user_id = cast(:user_id as uuid)
                    and plan.weekly_plan_version_id = cast(:object_id as uuid)
                    and plan.version_no::text = :version
                )
            """,
            "report": """
                select exists (
                  select 1 from learning.report_projection_versions report
                  where report.user_id = cast(:user_id as uuid)
                    and report.projection_version_id = cast(:object_id as uuid)
                    and report.input_fingerprint = :version
                )
            """,
            "material": """
                select exists (
                  select 1 from learning.source_artifact_versions artifact
                  where artifact.owner_user_id = cast(:user_id as uuid)
                    and artifact.artifact_id = cast(:object_id as uuid)
                    and artifact.artifact_version_id = cast(:version as uuid)
                    and artifact.state = 'available'
                  union all
                  select 1 from learning.essay_attempts attempt
                  where attempt.user_id = cast(:user_id as uuid)
                    and attempt.material_bundle_version_id = cast(:object_id as uuid)
                    and attempt.task_version_id = cast(:version as uuid)
                )
            """,
        }
        statement = statements[object_type]
        try:
            return bool(
                self._session.execute(
                    text(statement),
                    {
                        "user_id": user_id,
                        "object_id": object_id,
                        "version": version,
                    },
                ).scalar_one()
            )
        except Exception as exc:
            raise ObjectAuthorizationError(f"malformed_{object_type}_reference") from exc

    def _question_ref_owned(
        self,
        *,
        user_id: str,
        question_id: str,
        version: str,
    ) -> bool:
        xingce = self._session.execute(
            text(
                """
                select exists (
                  select 1 from learning.xingce_attempt_records attempt
                  where attempt.user_id = cast(:user_id as uuid)
                    and attempt.question_id = :question_id
                    and attempt.content_version_id = :version
                )
                """
            ),
            {
                "user_id": user_id,
                "question_id": question_id,
                "version": version,
            },
        ).scalar_one()
        if bool(xingce):
            return True
        essay_version = _uuid_or_none(version)
        if essay_version is None:
            return False
        return bool(
            self._session.execute(
                text(
                    """
                    select exists (
                      select 1 from learning.essay_attempts attempt
                      where attempt.user_id = cast(:user_id as uuid)
                        and attempt.task_key = :question_id
                        and attempt.task_version_id = cast(:version as uuid)
                    )
                    """
                ),
                {
                    "user_id": user_id,
                    "question_id": question_id,
                    "version": essay_version,
                },
            ).scalar_one()
        )

    def _attempt_ref_owned(
        self,
        *,
        user_id: str,
        attempt_id: str,
        version: str,
    ) -> bool:
        try:
            normalized_attempt_id = str(UUID(attempt_id))
        except (TypeError, ValueError, AttributeError):
            return False
        xingce = self._session.execute(
            text(
                """
                select exists (
                  select 1 from learning.xingce_attempt_records attempt
                  where attempt.user_id = cast(:user_id as uuid)
                    and attempt.attempt_record_id = cast(:attempt_id as uuid)
                    and attempt.content_version_id = :version
                )
                """
            ),
            {
                "user_id": user_id,
                "attempt_id": normalized_attempt_id,
                "version": version,
            },
        ).scalar_one()
        if bool(xingce):
            return True
        essay_version = _uuid_or_none(version)
        if essay_version is None:
            return False
        return bool(
            self._session.execute(
                text(
                    """
                    select exists (
                      select 1 from learning.essay_attempts attempt
                      where attempt.user_id = cast(:user_id as uuid)
                        and attempt.attempt_id = cast(:attempt_id as uuid)
                        and attempt.task_version_id = cast(:version as uuid)
                    )
                    """
                ),
                {
                    "user_id": user_id,
                    "attempt_id": normalized_attempt_id,
                    "version": essay_version,
                },
            ).scalar_one()
        )

    def _explicit_object_candidates(
        self,
        *,
        user_id: str,
        context: GlobalAskContext,
    ) -> tuple[_Candidate, ...]:
        candidates: list[_Candidate] = []
        for item in context.object_refs:
            if item.type == "question":
                candidates.extend(
                    self._question_context_candidates(
                        user_id=user_id,
                        question_id=item.id,
                        version=item.version,
                    )
                )
            elif item.type == "plan":
                candidate = self._plan_context_candidate(
                    user_id=user_id,
                    plan_id=item.id,
                    version=item.version,
                )
                if candidate is not None:
                    candidates.append(candidate)
            elif item.type == "position":
                candidate = self._position_context_candidate(
                    user_id=user_id,
                    position_id=item.id,
                    version=item.version,
                )
                if candidate is not None:
                    candidates.append(candidate)
            elif item.type == "report":
                candidate = self._report_context_candidate(
                    user_id=user_id,
                    report_id=item.id,
                    version=item.version,
                )
                if candidate is not None:
                    candidates.append(candidate)
        return tuple(candidates)

    def _plan_context_candidate(
        self,
        *,
        user_id: str,
        plan_id: str,
        version: str,
    ) -> _Candidate | None:
        daily = (
            self._session.execute(
                text(
                    """
                select plan.local_date, plan.day_mode, plan.headline,
                       plan.rationale, plan.goal_gap, plan.scheduled_minutes,
                       plan.due_reflection_count, plan.due_verification_count,
                       plan.track_explanation, plan.invalidation_conditions,
                       plan.replan_reason_codes, plan.generated_at,
                       plan.valid_until,
                       coalesce(
                         jsonb_agg(
                           jsonb_build_object(
                             'order', task.task_order,
                             'track', task.track,
                             'stage', task.stage,
                             'title', task.title,
                             'reason', task.reason,
                             'estimatedMinutes', task.estimated_minutes,
                             'mandatory', task.mandatory,
                             'priorityScore', task.priority_score
                           )
                           order by task.task_order
                         ) filter (where task.task_id is not null),
                         '[]'::jsonb
                       ) as tasks
                  from learning.daily_plan_versions plan
                  left join learning.daily_plan_tasks task
                    on task.plan_version_id = plan.plan_version_id
                   and task.user_id = plan.user_id
                   and task.local_date = plan.local_date
                 where plan.user_id = cast(:user_id as uuid)
                   and plan.plan_version_id = cast(:plan_id as uuid)
                   and plan.version_no::text = :version
                 group by plan.plan_version_id
                """
                ),
                {"user_id": user_id, "plan_id": plan_id, "version": version},
            )
            .mappings()
            .one_or_none()
        )
        if daily is not None:
            content = _join_context_text(
                ("日期", daily["local_date"]),
                ("当天模式", daily["day_mode"]),
                ("计划结论", daily["headline"]),
                ("这样安排的原因", daily["rationale"]),
                ("岗位分差", daily["goal_gap"]),
                ("安排时长（分钟）", daily["scheduled_minutes"]),
                ("待复盘数量", daily["due_reflection_count"]),
                ("待复测数量", daily["due_verification_count"]),
                ("两科学习安排", daily["track_explanation"]),
                ("当天任务", daily["tasks"]),
                ("会触发重新安排的情况", daily["invalidation_conditions"]),
                ("本次调整原因", daily["replan_reason_codes"]),
                ("生成时间", daily["generated_at"]),
                ("有效至", daily["valid_until"]),
            )
            return context_candidate(
                kind="daily_plan",
                source_id=f"daily-plan:{plan_id}",
                version=version,
                anchor="confirmed-plan",
                title=f"{daily['local_date']} · 当天计划",
                text_value=content,
                trust_scope="authorized_plan",
                source_weight=1.0,
            )

        weekly = (
            self._session.execute(
                text(
                    """
                select plan.week_start, plan.rhythm, plan.days,
                       plan.replan_reason_codes, plan.generated_at,
                       plan.valid_until
                  from learning.weekly_plan_versions plan
                 where plan.user_id = cast(:user_id as uuid)
                   and plan.weekly_plan_version_id = cast(:plan_id as uuid)
                   and plan.version_no::text = :version
                """
                ),
                {"user_id": user_id, "plan_id": plan_id, "version": version},
            )
            .mappings()
            .one_or_none()
        )
        if weekly is None:
            return None
        content = _join_context_text(
            ("周起始日", weekly["week_start"]),
            ("周节奏", weekly["rhythm"]),
            ("七天安排", weekly["days"]),
            ("本次调整原因", weekly["replan_reason_codes"]),
            ("生成时间", weekly["generated_at"]),
            ("有效至", weekly["valid_until"]),
        )
        return context_candidate(
            kind="weekly_plan",
            source_id=f"weekly-plan:{plan_id}",
            version=version,
            anchor="confirmed-week",
            title=f"{weekly['week_start']} · 周计划",
            text_value=content,
            trust_scope="authorized_plan",
            source_weight=1.0,
        )

    def _position_context_candidate(
        self,
        *,
        user_id: str,
        position_id: str,
        version: str,
    ) -> _Candidate | None:
        row = (
            self._session.execute(
                text(
                    """
                select 'goal' as context_kind,
                       position.year, position.position_code, position.name,
                       position.department_full, position.system_code,
                       position.region, position.city, position.paper_type,
                       position.headcount, position.education_min,
                       position.major_required, position.political_req,
                       position.hukou_req, position.service_min_yrs,
                       position.has_extra_exam, position.remark,
                       goal.role, goal.status, goal.target_score_lower,
                       goal.target_score_recommended, goal.target_score_upper,
                       goal.target_score_evidence_level,
                       goal.target_score_sample_count,
                       goal.target_score_valid_until,
                       eligibility.status as eligibility_status,
                       eligibility.checks as eligibility_checks,
                       eligibility.missing_fact_keys
                  from learner.position_goal_versions goal
                  join product.positions position
                    on position.position_id = goal.position_id
                  join learner.eligibility_snapshots eligibility
                    on eligibility.snapshot_id = goal.eligibility_snapshot_id
                   and eligibility.user_id = goal.user_id
                   and eligibility.position_id = goal.position_id
                 where goal.user_id = cast(:user_id as uuid)
                   and goal.position_id = cast(:position_id as uuid)
                   and goal.goal_version_id = cast(:version as uuid)
                """
                ),
                {"user_id": user_id, "position_id": position_id, "version": version},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            row = (
                self._session.execute(
                    text(
                        """
                    select 'eligibility' as context_kind,
                           position.year, position.position_code, position.name,
                           position.department_full, position.system_code,
                           position.region, position.city, position.paper_type,
                           position.headcount, position.education_min,
                           position.major_required, position.political_req,
                           position.hukou_req, position.service_min_yrs,
                           position.has_extra_exam, position.remark,
                           null::text as role, null::text as status,
                           null::numeric as target_score_lower,
                           null::numeric as target_score_recommended,
                           null::numeric as target_score_upper,
                           null::text as target_score_evidence_level,
                           null::integer as target_score_sample_count,
                           null::timestamptz as target_score_valid_until,
                           eligibility.status as eligibility_status,
                           eligibility.checks as eligibility_checks,
                           eligibility.missing_fact_keys
                      from learner.eligibility_snapshots eligibility
                      join product.positions position
                        on position.position_id = eligibility.position_id
                     where eligibility.user_id = cast(:user_id as uuid)
                       and eligibility.position_id = cast(:position_id as uuid)
                       and eligibility.snapshot_id = cast(:version as uuid)
                    """
                    ),
                    {"user_id": user_id, "position_id": position_id, "version": version},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        is_goal = row["context_kind"] == "goal"
        content = _join_context_text(
            ("考试年度", row["year"]),
            ("职位", f"{row['department_full']} · {row['name']}"),
            ("职位代码", row["position_code"]),
            ("地区", " · ".join(filter(None, (row["region"], row["city"])))),
            ("招录人数", row["headcount"]),
            ("试卷类型", row["paper_type"]),
            ("学历要求", row["education_min"]),
            ("专业要求", row["major_required"]),
            ("政治面貌要求", row["political_req"]),
            ("户籍要求", row["hukou_req"]),
            ("基层经历要求（年）", row["service_min_yrs"]),
            ("是否有额外考试", row["has_extra_exam"]),
            ("备注", row["remark"]),
            ("当前目标状态", {"role": row["role"], "status": row["status"]}),
            (
                "建议备考分数",
                {
                    "lower": row["target_score_lower"],
                    "recommended": row["target_score_recommended"],
                    "upper": row["target_score_upper"],
                    "evidenceLevel": row["target_score_evidence_level"],
                    "sampleCount": row["target_score_sample_count"],
                    "validUntil": row["target_score_valid_until"],
                },
            ),
            ("资格核验状态", row["eligibility_status"]),
            ("资格逐项结果", row["eligibility_checks"]),
            ("还缺的本人资料", row["missing_fact_keys"]),
        )
        return context_candidate(
            kind="target_position" if is_goal else "position_eligibility",
            source_id=f"{'position-goal' if is_goal else 'position-eligibility'}:{version}",
            version=version,
            anchor="confirmed-goal-and-eligibility" if is_goal else "eligibility-snapshot",
            title=f"{row['department_full']} · {row['name']}",
            text_value=content,
            trust_scope="authorized_position",
            source_weight=1.0,
        )

    def _report_context_candidate(
        self,
        *,
        user_id: str,
        report_id: str,
        version: str,
    ) -> _Candidate | None:
        row = (
            self._session.execute(
                text(
                    """
                select report.report_kind, report.subject_ref, report.payload,
                       report.evidence_refs, report.next_plan_evidence_refs,
                       report.generated_at
                  from learning.report_projection_versions report
                 where report.user_id = cast(:user_id as uuid)
                   and report.projection_version_id = cast(:report_id as uuid)
                   and report.input_fingerprint = :version
                """
                ),
                {"user_id": user_id, "report_id": report_id, "version": version},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        content = _join_context_text(
            ("报告类型", row["report_kind"]),
            ("报告对象", row["subject_ref"]),
            ("报告结论", row["payload"]),
            ("引用的学习记录", row["evidence_refs"]),
            ("已经影响的后续计划", row["next_plan_evidence_refs"]),
            ("生成时间", row["generated_at"]),
        )
        return context_candidate(
            kind="learning_report",
            source_id=f"report:{report_id}",
            version=version,
            anchor="confirmed-report",
            title=f"{row['report_kind']} · 学习报告",
            text_value=content,
            trust_scope="authorized_report",
            source_weight=1.0,
        )

    def _question_context_candidates(
        self,
        *,
        user_id: str,
        question_id: str,
        version: str,
    ) -> tuple[_Candidate, ...]:
        xingce = (
            self._session.execute(
                text(
                    """
                select attempt.attempt_record_id, question.module_name,
                       question.question_type, question.stem, question.answer,
                       question.material_text,
                       (
                         select string_agg(
                           option.label || '．' || option.option_text,
                           E'\n' order by option.option_index
                         )
                         from product.question_options option
                         where option.question_id = question.question_id
                       ) as options_text,
                       coalesce(
                         explanation.preferred_analysis,
                         explanation.stored_analysis,
                         explanation.original_analysis
                       ) as explanation,
                       answer.payload as answer_payload,
                       attempt.timing_evidence,
                       assessment.result_snapshot
                  from learning.xingce_attempt_records attempt
                  join product.questions question
                    on question.question_id = attempt.question_id
                  join learning.xingce_answer_versions answer
                    on answer.answer_version_id = attempt.answer_version_id
                   and answer.user_id = attempt.user_id
                   and answer.session_id = attempt.session_id
                   and answer.question_id = attempt.question_id
                  left join product.question_explanations explanation
                    on explanation.question_id = question.question_id
                  left join learning.xingce_assessments assessment
                    on assessment.attempt_record_id = attempt.attempt_record_id
                   and assessment.user_id = attempt.user_id
                   and assessment.session_id = attempt.session_id
                   and assessment.question_id = attempt.question_id
                 where attempt.user_id = cast(:user_id as uuid)
                   and attempt.question_id = :question_id
                   and attempt.content_version_id = :version
                 order by attempt.created_at desc
                 limit 1
                """
                ),
                {
                    "user_id": user_id,
                    "question_id": question_id,
                    "version": version,
                },
            )
            .mappings()
            .one_or_none()
        )
        if xingce is not None:
            title = str(xingce["module_name"] or "行测题目")
            canonical_text = _join_context_text(
                ("题型", xingce["question_type"]),
                ("题干", xingce["stem"]),
                ("共享材料", xingce["material_text"]),
                ("选项", xingce["options_text"]),
                ("正确答案", xingce["answer"]),
                ("解析", xingce["explanation"]),
            )
            attempt_text = _join_context_text(
                (
                    "作答轨迹",
                    _selected_json(
                        xingce["answer_payload"],
                        (
                            "selectedOptionIds",
                            "eliminatedOptionIds",
                            "confidence",
                            "marked",
                            "skipped",
                            "elapsedSeconds",
                        ),
                    ),
                ),
                ("用时证据", xingce["timing_evidence"]),
                ("判分结果", xingce["result_snapshot"]),
            )
            attempt_id = str(xingce["attempt_record_id"])
            return tuple(
                candidate
                for candidate in (
                    context_candidate(
                        kind="question_content",
                        source_id=f"question:{question_id}:{version}",
                        version=version,
                        anchor="canonical-question",
                        title=f"{title} · 当前题",
                        text_value=canonical_text,
                        trust_scope="authorized_question",
                        source_weight=1.0,
                    )
                    if canonical_text
                    else None,
                    context_candidate(
                        kind="question_attempt",
                        source_id=f"question-attempt:{attempt_id}",
                        version=version,
                        anchor="submitted-attempt",
                        title=f"{title} · 本次作答记录",
                        text_value=attempt_text,
                        trust_scope="owned_attempt",
                        source_weight=1.0,
                    )
                    if attempt_text
                    else None,
                )
                if candidate is not None
            )

        essay_version = _uuid_or_none(version)
        if essay_version is None:
            return ()
        essay = (
            self._session.execute(
                text(
                    """
                select attempt.attempt_id, task.question_type, task.prompt,
                       task.word_limit, task.maximum_score,
                       (
                         select string_agg(
                           segment.text,
                           E'\n\n' order by segment.material_order,
                                           segment.segment_order
                         )
                         from product.essay_material_segments segment
                         where segment.material_bundle_version_id =
                               attempt.material_bundle_version_id
                       ) as material_text,
                       answer.answer_version_id, answer.answer_text,
                       answer.thinking_payload,
                       review.review_version_id, review.score_recommended,
                       review.score_maximum, review.score_confidence,
                       review.dimensions, review.findings, review.rewrite_plan
                  from learning.essay_attempts attempt
                  join product.essay_task_versions task
                    on task.task_version_id = attempt.task_version_id
                   and task.task_key = attempt.task_key
                  left join lateral (
                    select candidate.*
                      from learning.essay_answer_versions candidate
                     where candidate.user_id = attempt.user_id
                       and candidate.attempt_id = attempt.attempt_id
                       and candidate.task_version_id = attempt.task_version_id
                       and not exists (
                         select 1 from learning.essay_answer_versions successor
                          where successor.supersedes_answer_version_id =
                                candidate.answer_version_id
                       )
                     order by candidate.version_no desc,
                              candidate.created_at desc
                     limit 1
                  ) answer on true
                  left join lateral (
                    select candidate.*
                      from learning.essay_review_versions candidate
                     where candidate.user_id = attempt.user_id
                       and candidate.attempt_id = attempt.attempt_id
                       and candidate.task_version_id = attempt.task_version_id
                       and candidate.status = 'completed'
                       and not exists (
                         select 1 from learning.essay_review_versions successor
                          where successor.supersedes_review_version_id =
                                candidate.review_version_id
                       )
                     order by candidate.generated_at desc
                     limit 1
                  ) review on true
                 where attempt.user_id = cast(:user_id as uuid)
                   and attempt.task_key = :question_id
                   and attempt.task_version_id = cast(:version as uuid)
                 order by attempt.submitted_at desc
                 limit 1
                """
                ),
                {
                    "user_id": user_id,
                    "question_id": question_id,
                    "version": essay_version,
                },
            )
            .mappings()
            .one_or_none()
        )
        if essay is None:
            return ()
        attempt_id = str(essay["attempt_id"])
        title = f"申论{str(essay['question_type'] or '题目')}"
        canonical_text = _join_context_text(
            ("题干", essay["prompt"]),
            ("字数限制", essay["word_limit"]),
            ("满分", essay["maximum_score"]),
            ("给定材料", essay["material_text"]),
        )
        answer_text = _join_context_text(
            ("考生答案", essay["answer_text"]),
            ("作答思路", essay["thinking_payload"]),
        )
        review_text = _join_context_text(
            (
                "训练估分",
                (
                    f"{essay['score_recommended']} / {essay['score_maximum']}"
                    if essay["score_recommended"] is not None
                    else None
                ),
            ),
            ("估分可信度", essay["score_confidence"]),
            ("评分维度", essay["dimensions"]),
            ("证据问题", essay["findings"]),
            ("改写计划", essay["rewrite_plan"]),
        )
        result: list[_Candidate] = []
        if canonical_text:
            result.append(
                context_candidate(
                    kind="essay_question",
                    source_id=f"essay-question:{question_id}:{version}",
                    version=version,
                    anchor="prompt-and-material",
                    title=f"{title} · 题目与材料",
                    text_value=canonical_text,
                    trust_scope="authorized_question",
                    source_weight=1.0,
                )
            )
        if answer_text:
            result.append(
                _Candidate(
                    reference=EvidenceReference(
                        kind="essay_attempt",
                        sourceId=f"essay-attempt:{attempt_id}",
                        version=version,
                        anchor="confirmed-answer",
                        title=f"{title} · 你的作答",
                    ),
                    text=answer_text,
                    trust_scope="owned_attempt",
                    source_weight=0.98,
                    untrusted_input=True,
                )
            )
        if review_text and essay["review_version_id"] is not None:
            result.append(
                context_candidate(
                    kind="essay_review",
                    source_id=f"essay-review:{essay['review_version_id']}",
                    version=version,
                    anchor="grounded-review",
                    title=f"{title} · 证据化评阅",
                    text_value=review_text,
                    trust_scope="authorized_essay_review",
                    source_weight=1.0,
                )
            )
        return tuple(result)

    def _confirmed_private_materials(self, *, user_id: str) -> tuple[_Candidate, ...]:
        rows = self._session.execute(
            text(
                """
                select source.source_id, source.source_version_id, source.title,
                       page.page_no, chunk.chunk_id, chunk.anchor, chunk.content
                from learning.private_knowledge_source_versions source
                join learning.private_knowledge_page_versions page
                  on page.source_version_id = source.source_version_id
                 and page.owner_user_id = source.owner_user_id
                join learning.private_knowledge_chunks chunk
                  on chunk.page_version_id = page.page_version_id
                 and chunk.owner_user_id = page.owner_user_id
                where source.owner_user_id = cast(:user_id as uuid)
                  and not exists (
                    select 1 from learning.private_knowledge_source_versions successor
                    where successor.supersedes_source_version_id =
                          source.source_version_id
                  )
                order by source.created_at desc, page.page_no, chunk.ordinal
                limit 120
                """
            ),
            {"user_id": user_id},
        ).mappings()
        return tuple(
            _Candidate(
                reference=EvidenceReference(
                    kind="user_material",
                    sourceId=f"private-source:{row['source_id']}",
                    version=str(row["source_version_id"]),
                    anchor=f"page:{row['page_no']}:chunk:{row['chunk_id']}",
                    title=str(row["title"]),
                ),
                text=str(row["content"]),
                trust_scope="owner_private_material",
                source_weight=0.85,
                untrusted_input=True,
            )
            for row in rows
            if str(row["content"]).strip()
        )

    def _projected_private_materials(
        self, *, user_id: str, question: str, maximum_sources: int
    ) -> tuple[_Candidate, ...]:
        if self._private_projection is None:
            raise RuntimeError("private projection is not configured")
        result = self._private_projection.search(
            owner_user_id=user_id,
            query=question,
            maximum_sources=maximum_sources,
        )
        candidates: list[_Candidate] = []
        seen_sources: set[UUID] = set()
        for citation in result.citations:
            if not citation.excerpt.strip() or citation.source_id in seen_sources:
                continue
            seen_sources.add(citation.source_id)
            candidates.append(
                _Candidate(
                    reference=EvidenceReference(
                        kind="user_material",
                        sourceId=f"private-source:{citation.source_id}",
                        version=str(citation.source_version_id),
                        anchor=(
                            f"page:{citation.page_no}:chunk:{citation.chunk_id}:{citation.anchor}"
                        ),
                        title=citation.title,
                    ),
                    text=citation.excerpt,
                    trust_scope="owner_private_material",
                    source_weight=0.85,
                    untrusted_input=True,
                    retrieval_score=citation.final_score,
                )
            )
        return tuple(candidates)


def context_candidate(
    *,
    kind: str,
    source_id: str,
    version: str,
    anchor: str,
    title: str,
    text_value: str,
    trust_scope: str,
    source_weight: float,
    display_summary: str | None = None,
) -> _Candidate:
    return _Candidate(
        reference=EvidenceReference(
            kind=kind,
            sourceId=source_id,
            version=version,
            anchor=anchor,
            title=title,
        ),
        text=text_value,
        trust_scope=trust_scope,
        source_weight=source_weight,
        untrusted_input=False,
        display_summary=display_summary,
    )


def _terms(value: str) -> set[str]:
    normalized = "".join(value.casefold().split())
    latin = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    chinese = "".join(re.findall(r"[\u3400-\u9fff]", normalized))
    grams = {chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))}
    return latin | grams


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _semantic_overlap(question: str, candidate: _Candidate) -> float:
    groups = (
        ("岗位", "资格", "报名", "目标分"),
        ("计划", "今天", "安排", "任务", "复测"),
        ("错因", "做错", "失分", "方法"),
        ("申论", "材料", "要点", "结构", "改写"),
        ("行测", "政治理论", "资料分析", "判断推理", "言语", "数量", "常识"),
    )
    question_groups = {
        index for index, group in enumerate(groups) if any(term in question for term in group)
    }
    text_groups = {
        index
        for index, group in enumerate(groups)
        if any(term in candidate.text or term in candidate.reference.title for term in group)
    }
    return _jaccard(
        {str(item) for item in question_groups},
        {str(item) for item in text_groups},
    )


def _mmr_select(
    ranked: list[tuple[float, _Candidate, float, float]],
    *,
    maximum_sources: int,
) -> list[tuple[float, _Candidate, float, float]]:
    selected: list[tuple[float, _Candidate, float, float]] = []
    remaining = list(ranked)
    while remaining and len(selected) < maximum_sources:
        choice = max(
            remaining,
            key=lambda item: (
                (0.75 * item[0])
                - (
                    0.25
                    * max(
                        (
                            _jaccard(
                                _terms(item[1].text),
                                _terms(existing[1].text),
                            )
                            for existing in selected
                        ),
                        default=0.0,
                    )
                ),
                item[1].reference.source_id,
            ),
        )
        selected.append(choice)
        remaining.remove(choice)
    return selected


def _excerpt(value: str) -> str:
    collapsed = " ".join(value.split())
    return collapsed[:2000]


def _uuid_or_none(value: str) -> str | None:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _join_context_text(*parts: tuple[str, object]) -> str:
    values: list[str] = []
    for label, value in parts:
        if value is None:
            continue
        if isinstance(value, str):
            rendered = value.strip()
        else:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if rendered and rendered not in {"{}", "[]", "null"}:
            values.append(f"{label}：{rendered}")
    return "\n".join(values)


def _selected_json(value: object, keys: tuple[str, ...]) -> object | None:
    if not isinstance(value, dict):
        return value
    selected = {key: value[key] for key in keys if key in value}
    return selected or None


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
