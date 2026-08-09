from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.knowledge.module_vocabulary import (
    ReleasedModuleVocabulary,
    load_current_module_vocabulary,
)
from app.domains.calibration.service import project_knowledge_point_weaknesses
from app.domains.knowledge.repository import KnowledgeRepository
from app.domains.planning.candidates import (
    ACTIONABLE_REFLECTION_STATES,
    ObjectivePlanningSnapshot,
)
from app.domains.planning.cause_training import (
    ConfirmedCauseDirection,
    direction_from_codes,
    leading_confirmed_cause_index,
)
from app.domains.planning.repository_support import (
    ActiveStrategyDecision,
    GoalAbilitySnapshot,
    _as_utc,
    _optional_float,
    _optional_str,
    _strict_cause_decision_version_id,
    _weakest_canonical_module,
)
from app.domains.task_runtime.xingce_attempt.contracts import (
    XINGCE_MAX_QUESTION_BUNDLE_SIZE,
    freeze_selection_intent,
)


class CandidateInputRepositoryMixin:
    def goal_and_ability(
        self,
        session: Session,
        *,
        user_id: str,
    ) -> GoalAbilitySnapshot:
        row = (
            session.execute(
                text(
                    """
                select
                  goal.goal_version_id,
                  case when position_snapshot.snapshot_id is not null
                    then position_snapshot.name else legacy_position.name
                  end as position_name,
                  case when position_snapshot.snapshot_id is not null
                    then position_snapshot.department_full
                    else legacy_position.department_full
                  end as department_name,
                  goal.target_score_lower,
                  goal.target_score_recommended,
                  goal.target_score_upper,
                  goal.target_score_evidence_level,
                  goal.target_score_valid_until,
                  estimate.estimate_version_id,
                  estimate.status as estimate_status,
                  estimate.xingce_lower,
                  estimate.xingce_recommended,
                  estimate.xingce_upper,
                  estimate.shenlun_lower,
                  estimate.shenlun_recommended,
                  estimate.shenlun_upper,
                  estimate.total_lower,
                  estimate.total_recommended,
                  estimate.total_upper,
                  estimate.confidence as estimate_confidence,
                  estimate.valid_until as estimate_valid_until,
                  estimate.evidence_summary
                from learner.users learner
                left join learner.current_primary_position_goals goal
                  on goal.user_id = learner.user_id
                left join product.position_import_batches import_batch
                  on import_batch.position_data_version
                       = goal.position_data_version
                left join product.position_snapshots position_snapshot
                  on position_snapshot.position_id = goal.position_id
                 and position_snapshot.position_data_version
                       = goal.position_data_version
                left join product.positions legacy_position
                  on legacy_position.position_id = goal.position_id
                 and import_batch.position_data_version is null
                left join lateral (
                  select current_estimate.*
                  from learning.ability_estimate_versions current_estimate
                  where current_estimate.user_id = learner.user_id
                    and not exists (
                      select 1
                      from learning.ability_estimate_versions successor
                      where successor.supersedes_estimate_version_id =
                            current_estimate.estimate_version_id
                    )
                  order by current_estimate.exam_year desc
                  limit 1
                ) estimate on true
                where learner.user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        return GoalAbilitySnapshot(
            goal_version_id=_optional_str(row["goal_version_id"]),
            position_name=row["position_name"],
            department_name=row["department_name"],
            target_lower=_optional_float(row["target_score_lower"]),
            target_recommended=_optional_float(row["target_score_recommended"]),
            target_upper=_optional_float(row["target_score_upper"]),
            target_evidence_level=row["target_score_evidence_level"],
            target_valid_until=row["target_score_valid_until"],
            estimate_version_id=_optional_str(row["estimate_version_id"]),
            estimate_status=row["estimate_status"],
            xingce_lower=_optional_float(row["xingce_lower"]),
            xingce_recommended=_optional_float(row["xingce_recommended"]),
            xingce_upper=_optional_float(row["xingce_upper"]),
            shenlun_lower=_optional_float(row["shenlun_lower"]),
            shenlun_recommended=_optional_float(row["shenlun_recommended"]),
            shenlun_upper=_optional_float(row["shenlun_upper"]),
            total_lower=_optional_float(row["total_lower"]),
            total_recommended=_optional_float(row["total_recommended"]),
            total_upper=_optional_float(row["total_upper"]),
            estimate_confidence=row["estimate_confidence"],
            estimate_valid_until=row["estimate_valid_until"],
            evidence_summary=(
                dict(row["evidence_summary"]) if row["evidence_summary"] is not None else None
            ),
        )

    def active_strategy_decisions(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime,
    ) -> list[ActiveStrategyDecision]:
        rows = session.execute(
            text(
                """
                select
                  decision.decision_version_id::text,
                  decision.decision_id::text,
                  decision.decision_type,
                  decision.scope_type,
                  decision.scope_key,
                  decision.rationale,
                  decision.evidence_refs,
                  decision.effective_from,
                  decision.effective_until,
                  decision.review_at,
                  decision.input_fingerprint
                from learning.strategy_decision_versions decision
                where decision.user_id = cast(:user_id as uuid)
                  and decision.status = 'active'
                  and decision.effective_from <= :now
                  and decision.effective_until > :now
                  and decision.review_at > :now
                  and not exists (
                    select 1
                    from learning.strategy_decision_versions successor
                    where successor.supersedes_decision_version_id =
                          decision.decision_version_id
                  )
                order by decision.decision_type, decision.scope_type,
                         decision.scope_key, decision.decision_version_id
                """
            ),
            {"user_id": user_id, "now": now},
        ).mappings()
        return [
            ActiveStrategyDecision(
                decision_version_id=str(row["decision_version_id"]),
                decision_id=str(row["decision_id"]),
                decision_type=str(row["decision_type"]),
                scope_type=str(row["scope_type"]),
                scope_key=str(row["scope_key"]),
                rationale=str(row["rationale"]),
                evidence_refs=tuple(dict(item) for item in row["evidence_refs"]),
                effective_from=_as_utc(row["effective_from"]),
                effective_until=_as_utc(row["effective_until"]),
                review_at=_as_utc(row["review_at"]),
                input_fingerprint=str(row["input_fingerprint"]),
            )
            for row in rows
        ]

    def _recent_confirmed_cause_direction(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime,
        vocabulary: ReleasedModuleVocabulary | None,
    ) -> tuple[ConfirmedCauseDirection | None, str | None, dict[str, Any] | None]:
        """读取近期已确认错因，解析出主导训练方向与对应的 canonical 模块。

        窗口为近 14 天（对齐复测到期节奏），只取 current head 且未被
        后继决定收回的确认错因。主导错因按 :func:`pick_leading_confirmed_cause`
        排序选出；主导错因所在题目的模块（canonical 化）作为针对性训练的
        模块。没有可识别方向或无法定位模块时，返回 (None, None)，
        由调用方回退到统计最弱模块。
        """

        causes = list(
            session.execute(
                text(
                    """
                    select cause.confirmed_cause_version_id,
                           cause.decision_version_id,
                           cause.candidate_version_id,
                           cause.cause_code, cause.replacement_code,
                           cause.session_id, candidate.payload as candidate_payload
                    from learning.xingce_confirmed_cause_versions cause
                    join learning.xingce_cause_candidate_versions candidate
                      on candidate.candidate_version_id = cause.candidate_version_id
                     and candidate.user_id = cause.user_id
                     and candidate.session_id = cause.session_id
                    where cause.user_id = cast(:user_id as uuid)
                      and cause.created_at >= :now - interval '14 days'
                      and not exists (
                        select 1
                        from learning.xingce_confirmed_cause_versions successor
                        where successor.supersedes_confirmed_cause_version_id =
                              cause.confirmed_cause_version_id
                      )
                      and not exists (
                        select 1
                        from learning.xingce_cause_decision_versions revoked_by
                        where revoked_by.supersedes_decision_version_id =
                              cause.decision_version_id
                      )
                    order by cause.created_at asc,
                             cause.confirmed_cause_version_id asc
                    """
                ),
                {"user_id": user_id, "now": now},
            )
            .mappings()
            .all()
        )
        if not causes:
            return None, None, None
        cause_pairs = [
            (_optional_str(row["cause_code"]), _optional_str(row["replacement_code"]))
            for row in causes
        ]
        leading_index = leading_confirmed_cause_index(cause_pairs)
        if leading_index is None:
            return None, None, None
        direction = direction_from_codes(*cause_pairs[leading_index])
        if direction is None:
            return None, None, None
        leading = causes[leading_index]
        module = self._cause_session_module(
            session,
            session_id=str(leading["session_id"]),
            vocabulary=vocabulary,
        )
        grounded = self._grounded_cause_selector(
            session,
            user_id=user_id,
            cause_row=leading,
            direction=direction,
            vocabulary=vocabulary,
            cause_module=module,
        )
        return direction, module, grounded

    def _grounded_cause_selector(
        self,
        session: Session,
        *,
        user_id: str,
        cause_row: Any,
        direction: ConfirmedCauseDirection,
        vocabulary: ReleasedModuleVocabulary | None,
        cause_module: str | None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "causeCode": direction.cause_code,
            "focus": direction.focus,
            "questionType": None,
            "microSkillNode": None,
            "confirmedCauseVersionId": str(
                cause_row["confirmed_cause_version_id"]
            ),
            "decisionVersionId": str(cause_row["decision_version_id"]),
            "candidateVersionId": str(cause_row["candidate_version_id"]),
        }
        # User replacements are grounded as a cause decision but cannot reuse
        # the AI candidate's per-question selector as if the user confirmed it.
        if cause_row["replacement_code"] is not None or vocabulary is None:
            return result
        payload = cause_row["candidate_payload"]
        candidates = payload.get("questionCandidates", []) if isinstance(payload, dict) else []
        matching = [
            item
            for item in candidates
            if isinstance(item, dict)
            and item.get("status") == "candidate"
            and item.get("causeCode") == direction.cause_code
            and item.get("questionId")
        ]
        if not matching:
            return result
        frozen_bundle = session.execute(
            text(
                """
                select safe_bundle
                from learning.xingce_task_sessions
                where user_id = cast(:user_id as uuid)
                  and session_id = cast(:session_id as uuid)
                """
            ),
            {"user_id": user_id, "session_id": str(cause_row["session_id"])},
        ).scalar_one_or_none()
        for item in matching:
            question_id = str(item["questionId"])
            frozen_question = (
                next(
                    (
                        question
                        for question in frozen_bundle.get("questions", [])
                        if isinstance(question, dict)
                        and question.get("questionId") == question_id
                    ),
                    None,
                )
                if isinstance(frozen_bundle, dict)
                else None
            )
            binding = session.execute(
                text(
                    """
                    select binding.canonical_module, node.node_id, node.node_type,
                           node.tag_type, node.tag_value
                    from product.knowledge_map_question_bindings binding
                    join product.knowledge_map_nodes node
                      on node.release_id = binding.release_id
                     and node.node_id = binding.node_id
                    where binding.release_id = cast(:release_id as uuid)
                      and binding.question_id = :question_id
                      and binding.delivery_eligible
                      and node.status = 'published'
                    order by (node.node_type = 'micro_skill') desc,
                             node.near5_weight desc nulls last, node.node_id
                    limit 1
                    """
                ),
                {"release_id": vocabulary.release_id, "question_id": question_id},
            ).mappings().one_or_none()
            if (
                binding is None
                or cause_module is None
                or vocabulary.canonicalize(str(binding["canonical_module"]))
                != cause_module
            ):
                continue
            question_type = (
                str(frozen_question["questionType"])
                if isinstance(frozen_question, dict)
                and frozen_question.get("questionType")
                else None
            )
            if (
                question_type is None
                and isinstance(frozen_question, dict)
                and frozen_question.get("contentVersionId")
            ):
                # Pre-Companion safe bundles did not expose questionType.
                # Recover it only from the exact immutable content version
                # already frozen into that bundle; never consult the live head.
                question_type = session.execute(
                    text(
                        """
                        select coalesce(
                          content.snapshot_payload ->> 'question_type',
                          content.snapshot_payload ->> 'questionType'
                        )
                        from product.question_content_versions content
                        where content.question_id = :question_id
                          and content.content_version_id = :content_version_id
                          and content.track = 'xingce'
                        """
                    ),
                    {
                        "question_id": question_id,
                        "content_version_id": str(
                            frozen_question["contentVersionId"]
                        ),
                    },
                ).scalar_one_or_none()
            if question_type:
                result["questionType"] = str(question_type)
            if binding["node_type"] == "micro_skill":
                result["microSkillNode"] = {
                    "releaseId": vocabulary.release_id,
                    "nodeId": str(binding["node_id"]),
                    "nodeType": str(binding["node_type"]),
                    "tagType": str(binding["tag_type"]),
                    "tagValue": str(binding["tag_value"]),
                }
            return result
        return result

    def _cause_session_module(
        self,
        session: Session,
        *,
        session_id: str,
        vocabulary: ReleasedModuleVocabulary | None,
    ) -> str | None:
        """把错因所在作答 session 的题目模块解析成 canonical 模块名。

        同一 session 内多题跨模块时取出现次数最多的模块；无法 canonical
        化或没有任何题目时返回 None。
        """

        rows = list(
            session.execute(
                text(
                    """
                    select question.module_name
                    from learning.xingce_assessments assessment
                    join product.questions question
                      on question.question_id = assessment.question_id
                    where assessment.session_id = cast(:session_id as uuid)
                      and question.module_name is not null
                    """
                ),
                {"session_id": session_id},
            )
            .mappings()
            .all()
        )
        counts: dict[str, int] = {}
        for row in rows:
            module_name = str(row["module_name"])
            canonical = vocabulary.canonicalize(module_name) if vocabulary is not None else None
            if canonical is None:
                continue
            counts[canonical] = counts.get(canonical, 0) + 1
        if not counts:
            return None
        return max(counts.items(), key=lambda item: (item[1], item[0]))[0]

    def objective_snapshot(
        self,
        session: Session,
        *,
        user_id: str,
        goal: GoalAbilitySnapshot,
        now: datetime,
        preferred_xingce_module: str | None = None,
    ) -> ObjectivePlanningSnapshot:
        module_assessments = (
            session.execute(
                text(
                    """
                select question.module_name,
                       count(*) as assessment_count,
                       count(*) filter (where assessment.correct) as correct_count
                from learning.xingce_assessments assessment
                join product.questions question
                  on question.question_id = assessment.question_id
                where assessment.user_id = cast(:user_id as uuid)
                  and assessment.created_at >= :now - interval '60 days'
                  and question.module_name is not null
                group by question.module_name
                order by question.module_name
                """
                ),
                {"user_id": user_id, "now": now},
            )
            .mappings()
            .all()
        )
        module_vocabulary = load_current_module_vocabulary(
            session,
            exam_scope="guokao",
            track="xingce",
            now=now,
        )
        weakest = preferred_xingce_module or _weakest_canonical_module(
            module_assessments,
            vocabulary=module_vocabulary,
        )
        cause_direction, cause_module, cause_context = self._recent_confirmed_cause_direction(
            session,
            user_id=user_id,
            now=now,
            vocabulary=module_vocabulary,
        )
        if (
            cause_direction is not None
            and preferred_xingce_module is None
            and cause_module is not None
        ):
            weakest = cause_module
        recent_assessment_count = int(
            session.execute(
                text(
                    """
                    select count(*)
                    from learning.xingce_assessments assessment
                    where assessment.user_id = cast(:user_id as uuid)
                      and assessment.created_at >= :now - interval '60 days'
                    """
                ),
                {"user_id": user_id, "now": now},
            ).scalar_one()
        )
        due_reflections = list(
            session.execute(
                text(
                    """
                    select review.review_version_id::text,
                           review.review_id::text,
                           review.session_id::text,
                           review.state
                    from learning.xingce_attempt_review_versions review
                    join learning.xingce_task_sessions task_session
                      on task_session.session_id = review.session_id
                     and task_session.user_id = review.user_id
                    where review.user_id = cast(:user_id as uuid)
                      and review.state = any(cast(:actionable_states as text[]))
                      and task_session.state = 'submitted'
                      and not exists (
                        select 1
                        from learning.xingce_attempt_review_versions successor
                        where successor.supersedes_review_version_id =
                              review.review_version_id
                      )
                    order by review.created_at, review.review_id
                    """
                ),
                {
                    "user_id": user_id,
                    "actionable_states": list(ACTIONABLE_REFLECTION_STATES),
                },
            ).mappings()
        )
        due_mastery = list(
            session.execute(
                text(
                    """
                    select mastery.mastery_id::text,
                           mastery.mastery_version_id::text,
                           mastery.module_name,
                           mastery.used_question_ids,
                           mastery.evidence_refs
                    from learning.xingce_mastery_state_versions mastery
                    where mastery.user_id = cast(:user_id as uuid)
                      and mastery.due_at is not null
                      and mastery.due_at <= :now
                      and not exists (
                        select 1
                        from learning.xingce_mastery_state_versions successor
                        where successor.supersedes_mastery_version_id =
                              mastery.mastery_version_id
                      )
                    order by mastery.due_at, mastery.created_at, mastery.mastery_id
                    """
                ),
                {"user_id": user_id, "now": now},
            ).mappings()
        )
        latest_mastery_version_id = session.execute(
            text(
                """
                select mastery.mastery_version_id::text
                from learning.xingce_mastery_state_versions mastery
                where mastery.user_id = cast(:user_id as uuid)
                  and not exists (
                    select 1
                    from learning.xingce_mastery_state_versions successor
                    where successor.supersedes_mastery_version_id =
                          mastery.mastery_version_id
                  )
                order by mastery.created_at desc, mastery.mastery_version_id desc
                limit 1
                """
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        first_reflection = due_reflections[0] if due_reflections else None
        first_mastery = due_mastery[0] if due_mastery else None
        formal_xingce_estimate_unavailable = (
            goal.estimate_version_id is None
            or goal.xingce_recommended is None
            or goal.estimate_valid_until is None
            or _as_utc(goal.estimate_valid_until) <= _as_utc(now)
        )
        # 岗位分差仍只认正式双科 ability estimate；但日常行测训练
        # 不能因申论估分尚缺失而无限重复摸底。一个完整受控题组的
        # 真实 assessments 已足以让规划转入复盘和针对训练。
        baseline_required = (
            formal_xingce_estimate_unavailable
            and recent_assessment_count < XINGCE_MAX_QUESTION_BUNDLE_SIZE
            and preferred_xingce_module is None
        )
        selection_intent = None
        if not baseline_required and module_vocabulary is not None:
            selection_intent = self._build_selection_intent(
                session,
                user_id=user_id,
                now=now,
                vocabulary=module_vocabulary,
                target_module=str(weakest) if weakest else None,
                cause_module=cause_module,
                cause_context=cause_context,
                has_recent_assessments=recent_assessment_count > 0,
            )
            if selection_intent is not None:
                weakest = str(selection_intent["targetModule"])
        baseline_question_count = 0
        targeted_question_count = 0
        verification_question_count = 0
        if baseline_required:
            baseline_question_count = len(
                self._question_repository.select_frozen_questions(
                    session,
                    module_name=None,
                    limit=XINGCE_MAX_QUESTION_BUNDLE_SIZE,
                    user_id=user_id,
                )
            )
        else:
            targeted_selection = (
                self._question_repository.select_frozen_questions_for_intent(
                    session,
                    intent=selection_intent,
                    limit=3,
                    user_id=user_id,
                    now=now,
                )
                if selection_intent is not None
                else self._question_repository.select_targeted_frozen_questions(
                    session,
                    module_name=str(weakest) if weakest else None,
                    limit=3,
                    user_id=user_id,
                    now=now,
                )
            )
            if targeted_selection is not None:
                # A learning target is grounded at micro-skill level, while a
                # full-time practice block may draw across that canonical
                # module instead of shrinking the whole day to one 3-question
                # node.
                targeted_question_count = len(
                    self._question_repository.select_frozen_questions(
                        session,
                        module_name=targeted_selection.module_name,
                        limit=XINGCE_MAX_QUESTION_BUNDLE_SIZE,
                        user_id=user_id,
                    )
                )
        if first_mastery is not None:
            verification_question_count = len(
                self._question_repository.select_frozen_questions(
                    session,
                    module_name=(
                        str(first_mastery["module_name"]) if first_mastery["module_name"] else None
                    ),
                    limit=XINGCE_MAX_QUESTION_BUNDLE_SIZE,
                    user_id=user_id,
                    excluded_question_ids=[
                        str(question_id) for question_id in first_mastery["used_question_ids"]
                    ],
                )
            )
        source_version_id = (
            str(first_mastery["mastery_version_id"])
            if first_mastery is not None
            else (
                str(first_reflection["review_version_id"])
                if first_reflection is not None
                else (
                    str(latest_mastery_version_id)
                    if latest_mastery_version_id is not None
                    else goal.estimate_version_id
                )
            )
        )
        return ObjectivePlanningSnapshot(
            baseline_required=baseline_required,
            due_reflection_count=len(due_reflections),
            due_verification_count=len(due_mastery),
            weakest_module=str(weakest) if weakest else None,
            source_version_id=source_version_id,
            closure_readiness="available",
            closure_unavailable_reason=None,
            due_reflection_session_id=(
                str(first_reflection["session_id"]) if first_reflection is not None else None
            ),
            due_reflection_review_id=(
                str(first_reflection["review_id"]) if first_reflection is not None else None
            ),
            due_reflection_version_id=(
                str(first_reflection["review_version_id"]) if first_reflection is not None else None
            ),
            due_verification_mastery_id=(
                str(first_mastery["mastery_id"]) if first_mastery is not None else None
            ),
            due_verification_version_id=(
                str(first_mastery["mastery_version_id"]) if first_mastery is not None else None
            ),
            due_verification_decision_version_id=(
                _strict_cause_decision_version_id(first_mastery["evidence_refs"])
                if first_mastery is not None
                else None
            ),
            baseline_question_count=baseline_question_count,
            targeted_question_count=targeted_question_count,
            verification_question_count=verification_question_count,
            due_reflection_state=(
                str(first_reflection["state"]) if first_reflection is not None else None
            ),
            caused_training_direction=cause_direction,
            selection_intent=selection_intent,
        )

    def _build_selection_intent(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime,
        vocabulary: ReleasedModuleVocabulary,
        target_module: str | None,
        cause_module: str | None,
        cause_context: dict[str, Any] | None,
        has_recent_assessments: bool,
    ) -> dict[str, Any] | None:
        weaknesses, weakness_fingerprint = project_knowledge_point_weaknesses(
            session,
            user_id=user_id,
        )
        canonical_weaknesses = [
            item
            for item in weaknesses
            if vocabulary.canonicalize(item.module_name) is not None
        ]
        if target_module is None:
            if canonical_weaknesses:
                target_module = str(
                    vocabulary.canonicalize(canonical_weaknesses[0].module_name)
                )
            else:
                # Cold-start target is the module of the strongest published
                # high-frequency supply node.  It is explicitly a policy
                # choice, not a fabricated learner weakness.
                knowledge_repository = KnowledgeRepository(session)
                supply_nodes = [
                    node
                    for module_name in vocabulary.canonical_names
                    for node in knowledge_repository.list_knowledge_map_nodes_for_release(
                        release_id=vocabulary.release_id,
                        node_types=("micro_skill", "knowledge_point"),
                        module_name=module_name,
                    )
                    if node.support_status == "text_supported"
                    and node.near5_occurrence_count > 0
                ]
                supply_nodes.sort(
                    key=lambda node: (
                        -node.near5_weight,
                        -node.near5_occurrence_count,
                        node.module_name,
                        node.node_id,
                    )
                )
                if supply_nodes:
                    target_module = supply_nodes[0].module_name
        if target_module is None:
            return None
        target_module = str(vocabulary.canonicalize(target_module) or target_module)
        module_weaknesses = sorted(
            (
                item
                for item in canonical_weaknesses
                if vocabulary.canonicalize(item.module_name) == target_module
            ),
            key=lambda item: (
                float(item.accuracy if item.accuracy is not None else 1.0),
                -item.sample_size,
                item.tag_value,
            ),
        )
        weakest_point = module_weaknesses[0] if module_weaknesses else None
        knowledge_node = None
        if weakest_point is not None:
            knowledge_node = KnowledgeRepository(session).get_knowledge_map_node_by_tag(
                exam_scope="guokao",
                track="xingce",
                module_name=target_module,
                tag_type="knowledge_point",
                tag_value=weakest_point.tag_value,
                now=now,
            )

        grounded_cause = (
            cause_context
            if cause_context is not None
            and cause_module == target_module
            and (
                cause_context.get("questionType") is not None
                or cause_context.get("microSkillNode") is not None
            )
            else None
        )
        shadow = session.execute(
            text(
                """
                select shadow_estimate_id::text, theta, insufficient_sample,
                       algorithm_version
                from learning.ability_estimate_v2_shadow
                where user_id = cast(:user_id as uuid)
                  and subject = 'xingce'
                order by computed_at desc, shadow_estimate_id desc
                limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()
        if (
            shadow is not None
            and not bool(shadow["insufficient_sample"])
            and shadow["theta"] is not None
        ):
            difficulty_policy: dict[str, Any] = {
                "state": "known",
                "targetTheta": float(shadow["theta"]),
                "sourceVersionId": str(shadow["shadow_estimate_id"]),
                "algorithmVersion": str(shadow["algorithm_version"]),
            }
        else:
            difficulty_policy = {
                "state": "unknown",
                "reason": (
                    "insufficient_shadow_sample" if shadow is not None else "missing_shadow_estimate"
                ),
                "sourceVersionId": (
                    str(shadow["shadow_estimate_id"]) if shadow is not None else None
                ),
                "algorithmVersion": "ability-estimate-v2-shadow-1",
            }

        if weakest_point is not None and knowledge_node is None:
            fallback_reason = "knowledge_node_unavailable"
        elif knowledge_node is None and grounded_cause is None:
            fallback_reason = (
                "insufficient_knowledge_point_evidence"
                if has_recent_assessments
                else "cold_start_no_grounded_evidence"
            )
        else:
            fallback_reason = None
        refs = [
            "selection-policy:xingce-selection-intent@v1",
            f"module-vocabulary-release:{vocabulary.release_id}",
            f"knowledge-point-weakness-projection:{weakness_fingerprint}",
        ]
        if shadow is not None:
            refs.append(f"ability-shadow:{shadow['shadow_estimate_id']}")
        if grounded_cause is not None:
            refs.extend(
                (
                    f"confirmed-cause-version:{grounded_cause['confirmedCauseVersionId']}",
                    f"cause-decision-version:{grounded_cause['decisionVersionId']}",
                    f"cause-candidate-version:{grounded_cause['candidateVersionId']}",
                )
            )
        return freeze_selection_intent(
            {
                "contractVersion": "xingce-selection-intent@v1",
                "knowledgeRelease": {
                    "releaseId": vocabulary.release_id,
                    "releaseVersion": vocabulary.release_version,
                    "sourceDataVersion": vocabulary.source_data_version,
                    "vocabVersion": vocabulary.vocab_version,
                    "freshUntil": vocabulary.fresh_until.isoformat(),
                },
                "targetModule": target_module,
                "targetKnowledgeNode": (
                    self._selection_node_payload(knowledge_node)
                    if knowledge_node is not None
                    else None
                ),
                "targetMicroSkillNode": (
                    grounded_cause["microSkillNode"]
                    if grounded_cause is not None
                    else None
                ),
                "confirmedCauseFocus": (
                    {
                        key: grounded_cause[key]
                        for key in (
                            "causeCode",
                            "focus",
                            "questionType",
                            "confirmedCauseVersionId",
                            "decisionVersionId",
                            "candidateVersionId",
                        )
                    }
                    if grounded_cause is not None
                    else None
                ),
                "difficultyPolicy": difficulty_policy,
                "noveltyPolicy": {
                    "answered": "exclude",
                    "exposed": "exclude",
                    "scope": "current_user",
                },
                "fallbackReason": fallback_reason,
                "evidenceRefs": refs,
            }
        )

    @staticmethod
    def _selection_node_payload(node: Any) -> dict[str, str]:
        return {
            "releaseId": str(node.release_id),
            "nodeId": str(node.node_id),
            "nodeType": str(node.node_type),
            "tagType": str(node.tag_type),
            "tagValue": str(node.tag_value),
        }


    def weekly_mock_due(
        self,
        session: Session,
        *,
        user_id: str,
        week_start: date,
    ) -> bool:
        count = session.execute(
            text(
                """
                select count(*)
                from learner.mock_exam_sessions
                where user_id = cast(:user_id as uuid)
                  and exam_scope = 'guokao'
                  and track = 'xingce'
                  and status in ('submitted', 'reviewed')
                  and submitted_at >= :week_start
                  and submitted_at < :week_start + interval '7 days'
                """
            ),
            {"user_id": user_id, "week_start": week_start},
        ).scalar_one()
        return int(count) == 0
