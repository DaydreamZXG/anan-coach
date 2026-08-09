from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.planning.candidates import EssayPlanningSnapshot
from app.domains.planning.repository_support import (
    GoalAbilitySnapshot,
    _as_utc,
    _essay_closure_context_gap,
    _essay_closure_gap,
    _optional_str,
    _review_is_learning_eligible,
    _weakest_dimension,
    _weakest_dimension_key,
)


class EssaySnapshotRepositoryMixin:
    def essay_snapshot(
        self,
        session: Session,
        *,
        user_id: str,
        goal: GoalAbilitySnapshot,
    ) -> EssayPlanningSnapshot:
        published = (
            session.execute(
                text(
                    """
                select
                  task.task_version_id::text,
                  task.question_type,
                  task.word_limit,
                  task.maximum_score,
                  material.material_bundle_version_id::text,
                  material.version_no as material_version_no,
                  rubric.rubric_version_id::text
                from product.essay_task_versions task
                join product.essay_material_bundle_versions material
                  on material.task_version_id = task.task_version_id
                 and material.completeness = 'complete'
                left join lateral (
                  select item.rubric_version_id
                  from product.essay_rubric_versions item
                  where item.task_version_id = task.task_version_id
                    and item.material_bundle_version_id =
                        material.material_bundle_version_id
                    and item.state = 'published'
                    and not exists (
                      select 1 from product.essay_rubric_versions successor
                      where successor.task_version_id = item.task_version_id
                        and successor.material_bundle_version_id =
                            item.material_bundle_version_id
                        and successor.version_no > item.version_no
                        and successor.state = 'published'
                    )
                  order by item.created_at desc, item.rubric_version_id
                  limit 1
                ) rubric on true
                where task.state = 'published'
                  and task.task_key like 'guokao%'
                  and not exists (
                    select 1 from product.essay_task_versions successor
                    where successor.task_key = task.task_key
                      and successor.version_no > task.version_no
                      and successor.state = 'published'
                  )
                  and not exists (
                    select 1 from product.essay_material_bundle_versions successor
                    where successor.task_version_id = material.task_version_id
                      and successor.version_no > material.version_no
                      and successor.completeness = 'complete'
                  )
                  and exists (
                    select 1
                    from product.essay_material_segments segment
                    where segment.material_bundle_version_id =
                          material.material_bundle_version_id
                  )
                order by (rubric.rubric_version_id is not null) desc,
                         exists (
                           select 1
                           from product.essay_material_segments source_segment
                           where source_segment.material_bundle_version_id =
                                 material.material_bundle_version_id
                             and (
                               coalesce(source_segment.source_metadata ->> 'paperTitle', '')
                                 like '%国考%'
                               or coalesce(source_segment.source_metadata ->> 'paperTitle', '')
                                 like '%国家公务员%'
                             )
                         ) desc,
                         task.version_no desc, material.version_no desc,
                         task.created_at desc, task.task_version_id
                limit 1
                """
                )
            )
            .mappings()
            .one_or_none()
        )
        if published is None:
            return EssayPlanningSnapshot("content_unavailable", None)
        latest = (
            session.execute(
                text(
                    """
                select
                  attempt.attempt_id::text,
                  attempt.task_version_id::text,
                  attempt.material_bundle_version_id::text,
                  attempt.rubric_version_id::text,
                  attempt.submitted_at,
                  task.question_type,
                  task.word_limit,
                  task.maximum_score,
                  answer.answer_version_id::text,
                  review.review_version_id::text,
                  review.status as review_status,
                  review.grounding_contract_version,
                  review.grounding_report,
                  review.findings,
                  review.rewrite_plan,
                  rewrite.rewrite_version_id::text,
                  transfer.verification_version_id::text,
                  transfer.status as transfer_status
                from learning.essay_attempts attempt
                join product.essay_task_versions task
                  on task.task_version_id = attempt.task_version_id
                join product.essay_rubric_versions bound_rubric
                  on bound_rubric.rubric_version_id = attempt.rubric_version_id
                 and bound_rubric.task_version_id = attempt.task_version_id
                 and bound_rubric.material_bundle_version_id =
                     attempt.material_bundle_version_id
                left join lateral (
                  select item.*
                  from learning.essay_answer_versions item
                  where item.user_id = attempt.user_id
                    and item.attempt_id = attempt.attempt_id
                    and item.task_version_id = attempt.task_version_id
                    and not exists (
                      select 1 from learning.essay_answer_versions successor
                      where successor.supersedes_answer_version_id =
                            item.answer_version_id
                    )
                  order by item.version_no desc, item.answer_version_id
                  limit 1
                ) answer on true
                left join lateral (
                  select item.*
                  from learning.essay_review_versions item
                  where item.attempt_id = attempt.attempt_id
                    and item.answer_version_id = answer.answer_version_id
                    and item.execution_fingerprint is not null
                    and not exists (
                      select 1 from learning.essay_review_versions successor
                      where successor.supersedes_review_version_id = item.review_version_id
                    )
                  order by item.generated_at desc, item.review_version_id
                  limit 1
                ) review on true
                left join lateral (
                  select item.*
                  from learning.essay_rewrite_versions item
                  where item.user_id = attempt.user_id
                    and item.base_review_version_id = review.review_version_id
                    and not exists (
                      select 1 from learning.essay_rewrite_versions successor
                      where successor.supersedes_rewrite_version_id = item.rewrite_version_id
                    )
                  order by item.created_at desc, item.rewrite_version_id
                  limit 1
                ) rewrite on true
                left join lateral (
                  select item.*
                  from learning.essay_transfer_verification_versions item
                  where item.user_id = attempt.user_id
                    and item.source_review_version_id = review.review_version_id
                    and not exists (
                      select 1
                      from learning.essay_transfer_verification_versions successor
                      where successor.supersedes_verification_version_id =
                            item.verification_version_id
                    )
                  order by item.generated_at desc, item.verification_version_id
                  limit 1
                ) transfer on true
                where attempt.user_id = cast(:user_id as uuid)
                order by attempt.submitted_at desc, attempt.attempt_id
                limit 1
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one_or_none()
        )
        closure_snapshot = self._essay_mastery_snapshot(
            session,
            user_id=user_id,
            latest_attempt=latest,
        )
        if closure_snapshot is not None:
            return closure_snapshot
        artifacts = (
            session.execute(
                text(
                    """
                select distinct on (artifact.artifact_type)
                       artifact.artifact_type,
                       artifact.artifact_version_id::text
                from learning.essay_learning_artifact_versions artifact
                where artifact.user_id = cast(:user_id as uuid)
                  and artifact.task_version_id = cast(:task_version_id as uuid)
                  and artifact.material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and artifact.artifact_type in (
                    'material_annotation', 'point_extraction', 'outline'
                  )
                  and not exists (
                    select 1 from learning.essay_learning_artifact_versions successor
                    where successor.supersedes_artifact_version_id =
                          artifact.artifact_version_id
                  )
                order by artifact.artifact_type, artifact.created_at desc,
                         artifact.artifact_version_id desc
                """
                ),
                {
                    "user_id": user_id,
                    "task_version_id": str(published["task_version_id"]),
                    "material_bundle_version_id": str(published["material_bundle_version_id"]),
                },
            )
            .mappings()
            .all()
        )
        artifact_versions = {
            str(item["artifact_type"]): str(item["artifact_version_id"]) for item in artifacts
        }
        if latest is None:
            material_annotation_id = artifact_versions.get("material_annotation")
            point_extraction_id = artifact_versions.get("point_extraction")
            outline_id = artifact_versions.get("outline")
            rubric_id = _optional_str(published["rubric_version_id"])
            if rubric_id is None:
                next_stage = "evaluation_pending"
                pending_reason = (
                    "这道题的评分标准还没准备好，先不硬排；岸岸会优先挑可以直接作答的题，"
                    "等标准就绪马上给你安排上。"
                )
            elif material_annotation_id is None:
                # 首次接触申论：先完整学一遍「材料阅读 → 要点提取 → 提纲 → 作答」，
                # 之后的推进才以「作答 → 评阅」为主、薄弱环节按需补。
                next_stage = "baseline_due"
                pending_reason = None
            elif point_extraction_id is None:
                next_stage = "point_extraction_due"
                pending_reason = None
            elif outline_id is None:
                next_stage = "outline_due"
                pending_reason = None
            else:
                next_stage = "drafting_due"
                pending_reason = None
            return EssayPlanningSnapshot(
                next_stage,  # type: ignore[arg-type]
                str(published["task_version_id"]),
                material_bundle_version_id=str(published["material_bundle_version_id"]),
                rubric_version_id=rubric_id,
                question_type=str(published["question_type"]),
                word_limit=int(published["word_limit"]),
                maximum_score=float(published["maximum_score"]),
                material_annotation_version_id=material_annotation_id,
                point_extraction_version_id=point_extraction_id,
                outline_version_id=outline_id,
                pending_reason=pending_reason,
            )
        task_id = str(latest["task_version_id"] or published["task_version_id"])
        material_bundle_version_id = str(latest["material_bundle_version_id"])
        rubric_version_id = str(latest["rubric_version_id"])
        question_type = str(latest["question_type"])
        word_limit = int(latest["word_limit"])
        maximum_score = float(latest["maximum_score"])
        review_acknowledgement_id = None
        if latest["review_version_id"] is not None:
            review_acknowledgement_id = session.execute(
                text(
                    """
                    select artifact.artifact_version_id::text
                    from learning.essay_learning_artifact_versions artifact
                    where artifact.user_id = cast(:user_id as uuid)
                      and artifact.task_version_id = cast(:task_version_id as uuid)
                      and artifact.artifact_type = 'review_acknowledgement'
                      and artifact.review_version_id = cast(:review_version_id as uuid)
                      and not exists (
                        select 1 from learning.essay_learning_artifact_versions successor
                        where successor.supersedes_artifact_version_id =
                              artifact.artifact_version_id
                      )
                    order by artifact.created_at desc, artifact.artifact_version_id
                    limit 1
                    """
                ),
                {
                    "user_id": user_id,
                    "task_version_id": task_id,
                    "review_version_id": str(latest["review_version_id"]),
                },
            ).scalar_one_or_none()
        pending_reason = None
        if latest["answer_version_id"] is None:
            stage = "drafting_due"
        elif latest["review_version_id"] is None:
            stage = "evaluation_pending"
            pending_reason = (
                "你的申论原稿已经交上去啦，评阅还在路上；等评阅出来，岸岸马上提醒你查看。"
            )
        elif not _review_is_learning_eligible(latest):
            stage = "evaluation_pending"
            pending_reason = (
                "这次的申论评阅还没通过，先不进入下一步；你的作答都还在，"
                "等重新评阅完，岸岸马上接着帮你排。"
            )
        elif review_acknowledgement_id is None:
            stage = "review_due"
        elif latest["rewrite_version_id"] is None:
            stage = "rewrite_due"
        elif (
            latest["verification_version_id"] is None or latest["transfer_status"] == "inconclusive"
        ):
            fresh_task = (
                session.execute(
                    text(
                        """
                    select
                      task.task_version_id::text,
                      task.question_type,
                      task.word_limit,
                      task.maximum_score,
                      material.material_bundle_version_id::text,
                      material.version_no as material_version_no,
                      rubric.rubric_version_id::text
                    from product.essay_task_versions task
                    join product.essay_material_bundle_versions material
                      on material.task_version_id = task.task_version_id
                     and material.completeness = 'complete'
                    join product.essay_rubric_versions rubric
                      on rubric.task_version_id = task.task_version_id
                     and rubric.material_bundle_version_id =
                         material.material_bundle_version_id
                     and rubric.state = 'published'
                    where task.state = 'published'
                      and task.task_key like 'guokao%'
                      and task.task_version_id <> cast(:source_task_id as uuid)
                      and not exists (
                        select 1 from product.essay_task_versions successor
                        where successor.task_key = task.task_key
                          and successor.version_no > task.version_no
                          and successor.state = 'published'
                      )
                      and not exists (
                        select 1 from product.essay_material_bundle_versions successor
                        where successor.task_version_id = material.task_version_id
                          and successor.version_no > material.version_no
                          and successor.completeness = 'complete'
                      )
                      and not exists (
                        select 1 from product.essay_rubric_versions successor
                        where successor.task_version_id = rubric.task_version_id
                          and successor.material_bundle_version_id =
                              rubric.material_bundle_version_id
                          and successor.version_no > rubric.version_no
                          and successor.state = 'published'
                      )
                      and exists (
                        select 1 from product.essay_material_segments segment
                        where segment.material_bundle_version_id =
                              material.material_bundle_version_id
                      )
                    order by exists (
                               select 1
                               from product.essay_material_segments source_segment
                               where source_segment.material_bundle_version_id =
                                     material.material_bundle_version_id
                                 and (
                                   coalesce(source_segment.source_metadata ->> 'paperTitle', '')
                                     like '%国考%'
                                   or coalesce(source_segment.source_metadata ->> 'paperTitle', '')
                                     like '%国家公务员%'
                                 )
                             ) desc,
                             task.version_no desc, material.version_no desc,
                             task.created_at desc, task.task_version_id
                    limit 1
                    """
                    ),
                    {"source_task_id": task_id},
                )
                .mappings()
                .one_or_none()
            )
            if fresh_task is not None:
                stage = "transfer_due"
                task_id = str(fresh_task["task_version_id"])
                material_bundle_version_id = str(fresh_task["material_bundle_version_id"])
                rubric_version_id = str(fresh_task["rubric_version_id"])
                question_type = str(fresh_task["question_type"])
                word_limit = int(fresh_task["word_limit"])
                maximum_score = float(fresh_task["maximum_score"])
            else:
                stage = "rewrite_due"
        elif latest["transfer_status"] == "not_verified":
            stage = "rewrite_due"
        else:
            stage = "practice_due"
        return EssayPlanningSnapshot(
            stage,  # type: ignore[arg-type]
            task_id,
            material_bundle_version_id=material_bundle_version_id,
            rubric_version_id=rubric_version_id,
            question_type=question_type,
            word_limit=word_limit,
            maximum_score=maximum_score,
            attempt_id=str(latest["attempt_id"]),
            answer_version_id=_optional_str(latest["answer_version_id"]),
            review_version_id=_optional_str(latest["review_version_id"]),
            rewrite_version_id=_optional_str(latest["rewrite_version_id"]),
            transfer_verification_version_id=_optional_str(latest["verification_version_id"]),
            review_acknowledgement_version_id=_optional_str(review_acknowledgement_id),
            pending_reason=pending_reason,
            weakest_dimension=(
                _weakest_dimension(latest["findings"], latest["rewrite_plan"])
                if _review_is_learning_eligible(latest)
                else None
            ),
            weakest_dimension_key=(
                _weakest_dimension_key(latest["findings"], latest["rewrite_plan"])
                if _review_is_learning_eligible(latest)
                else None
            ),
        )
    def _essay_mastery_snapshot(
        self,
        session: Session,
        *,
        user_id: str,
        latest_attempt: RowMapping | None,
    ) -> EssayPlanningSnapshot | None:
        heads = list(
            session.execute(
                text(
                    """
                    select mastery.*
                    from learning.essay_mastery_state_versions mastery
                    where mastery.user_id = cast(:user_id as uuid)
                      and mastery.state <> 'withdrawn'
                      and not exists (
                        select 1 from learning.essay_mastery_state_versions successor
                        where successor.supersedes_mastery_version_id =
                              mastery.mastery_version_id
                      )
                    order by mastery.due_at nulls last, mastery.created_at,
                             mastery.finding_decision_artifact_version_id,
                             mastery.finding_id
                    """
                ),
                {"user_id": user_id},
            ).mappings()
        )
        if not heads:
            return None
        due_heads = [
            item
            for item in heads
            if item["state"] in {"rewrite_due", "transfer_due"}
            and item["due_at"] is not None
            and _as_utc(item["due_at"]) <= datetime.now(UTC)
        ]
        if not due_heads:
            scheduled = [item for item in heads if item["state"] in {"rewrite_due", "transfer_due"}]
            if scheduled:
                earliest = min(scheduled, key=lambda item: item["due_at"])
                return EssayPlanningSnapshot(
                    "content_unavailable",
                    None,
                    pending_reason=(
                        "这篇申论刚到稳定掌握，还没到重写或迁移验证的时候，先不急着重复练。"
                    ),
                    mastery_state=str(earliest["state"]),  # type: ignore[arg-type]
                    mastery_version_ids=tuple(
                        str(item["mastery_version_id"]) for item in scheduled
                    ),
                    mastery_ids=tuple(str(item["mastery_id"]) for item in scheduled),
                )
            consumed_attempt_ids = {str(item["source_attempt_id"]) for item in heads}
            transfer_version_ids = [
                str(item["transfer_verification_version_id"])
                for item in heads
                if item["transfer_verification_version_id"] is not None
            ]
            for transfer_version_id in transfer_version_ids:
                transfer_attempt_id = session.execute(
                    text(
                        "select transfer_attempt_id::text "
                        "from learning.essay_transfer_verification_versions "
                        "where verification_version_id = cast(:version_id as uuid) "
                        "and user_id = cast(:user_id as uuid)"
                    ),
                    {"version_id": transfer_version_id, "user_id": user_id},
                ).scalar_one_or_none()
                if transfer_attempt_id is not None:
                    consumed_attempt_ids.add(str(transfer_attempt_id))
            if (
                latest_attempt is not None
                and str(latest_attempt["attempt_id"]) not in consumed_attempt_ids
            ):
                return None
            return EssayPlanningSnapshot(
                "content_unavailable",
                None,
                pending_reason=(
                    "这篇申论已经稳定掌握啦，在出现新的作答之前，先不重复练同样的内容。"
                ),
                mastery_state="stable_mastered",
                mastery_version_ids=tuple(str(item["mastery_version_id"]) for item in heads),
                mastery_ids=tuple(str(item["mastery_id"]) for item in heads),
            )

        decision_id = str(due_heads[0]["finding_decision_artifact_version_id"])
        group = [
            item
            for item in heads
            if str(item["finding_decision_artifact_version_id"]) == decision_id
        ]
        states = {str(item["state"]) for item in group}
        if "stable_mastered" in states or not states.issubset({"rewrite_due", "transfer_due"}):
            return _essay_closure_gap(
                "申论掌握度 current head 状态互相冲突，无法确定唯一可写闭环。"
            )
        identity = {
            (
                str(item["source_task_version_id"]),
                str(item["source_attempt_id"]),
                str(item["source_answer_version_id"]),
                str(item["source_review_version_id"]),
            )
            for item in group
        }
        if len(identity) != 1:
            return _essay_closure_gap("申论掌握度 current head 的来源题目、作答或评阅不一致。")
        rewrite_ids = {
            str(item["rewrite_version_id"])
            for item in group
            if item["rewrite_version_id"] is not None
        }
        if len(rewrite_ids) > 1:
            return _essay_closure_gap("申论掌握度 current head 绑定了不同 source rewrite 版本。")
        context = (
            session.execute(
                text(
                    """
                select
                  mastery.mastery_version_id,
                  source_attempt.attempt_id::text as source_attempt_id,
                  source_attempt.task_version_id::text as source_task_version_id,
                  source_attempt.material_bundle_version_id::text
                    as source_material_bundle_version_id,
                  source_attempt.rubric_version_id::text as source_rubric_version_id,
                  source_task.question_type, source_task.word_limit,
                  source_task.maximum_score, source_task.state as source_task_state,
                  source_material.completeness as source_material_completeness,
                  source_rubric.state as source_rubric_state,
                  source_answer.answer_version_id::text as source_answer_version_id,
                  current_answer.answer_version_id::text as current_answer_version_id,
                  source_review.review_version_id::text as source_review_version_id,
                  source_review.status as source_review_status,
                  source_review.grounding_contract_version,
                  source_review.grounding_report,
                  source_review.execution_fingerprint,
                  source_review.findings,
                  source_review.rewrite_plan,
                  decision.artifact_version_id::text as decision_artifact_version_id,
                  decision.artifact_type as decision_artifact_type,
                  decision.review_version_id::text as decision_review_version_id,
                  decision.task_version_id::text as decision_task_version_id,
                  decision.material_bundle_version_id::text
                    as decision_material_bundle_version_id,
                  decision.rubric_version_id::text as decision_rubric_version_id,
                  rewrite.rewrite_version_id::text,
                  rewrite.writer_contract_version as rewrite_contract_version,
                  rewrite.writer_policy_version as rewrite_policy_version,
                  rewrite.analysis_payload as rewrite_analysis_payload,
                  transfer.verification_version_id::text
                    as transfer_verification_version_id
                from learning.essay_mastery_state_versions mastery
                left join learning.essay_attempts source_attempt
                  on source_attempt.attempt_id = mastery.source_attempt_id
                 and source_attempt.user_id = mastery.user_id
                 and source_attempt.task_version_id = mastery.source_task_version_id
                left join product.essay_task_versions source_task
                  on source_task.task_version_id = source_attempt.task_version_id
                left join product.essay_material_bundle_versions source_material
                  on source_material.material_bundle_version_id =
                     source_attempt.material_bundle_version_id
                 and source_material.task_version_id = source_attempt.task_version_id
                left join product.essay_rubric_versions source_rubric
                  on source_rubric.rubric_version_id = source_attempt.rubric_version_id
                 and source_rubric.task_version_id = source_attempt.task_version_id
                 and source_rubric.material_bundle_version_id =
                     source_attempt.material_bundle_version_id
                left join learning.essay_answer_versions source_answer
                  on source_answer.answer_version_id = mastery.source_answer_version_id
                 and source_answer.attempt_id = mastery.source_attempt_id
                 and source_answer.user_id = mastery.user_id
                 and source_answer.task_version_id = mastery.source_task_version_id
                left join lateral (
                  select answer.*
                  from learning.essay_answer_versions answer
                  where answer.attempt_id = mastery.source_attempt_id
                    and answer.user_id = mastery.user_id
                    and answer.task_version_id = mastery.source_task_version_id
                    and not exists (
                      select 1 from learning.essay_answer_versions successor
                      where successor.supersedes_answer_version_id =
                            answer.answer_version_id
                    )
                  order by answer.version_no desc, answer.created_at desc
                  limit 1
                ) current_answer on true
                left join learning.essay_review_versions source_review
                  on source_review.review_version_id = mastery.source_review_version_id
                 and source_review.attempt_id = mastery.source_attempt_id
                 and source_review.user_id = mastery.user_id
                 and source_review.task_version_id = mastery.source_task_version_id
                 and source_review.answer_version_id = mastery.source_answer_version_id
                left join learning.essay_learning_artifact_versions decision
                  on decision.artifact_version_id =
                     mastery.finding_decision_artifact_version_id
                 and decision.user_id = mastery.user_id
                 and not exists (
                   select 1 from learning.essay_learning_artifact_versions successor
                   where successor.supersedes_artifact_version_id =
                         decision.artifact_version_id
                 )
                left join learning.essay_rewrite_versions rewrite
                  on rewrite.rewrite_version_id = mastery.rewrite_version_id
                 and rewrite.user_id = mastery.user_id
                 and rewrite.base_review_version_id = mastery.source_review_version_id
                 and rewrite.finding_decision_artifact_version_id =
                     mastery.finding_decision_artifact_version_id
                 and not exists (
                   select 1 from learning.essay_rewrite_versions successor
                   where successor.supersedes_rewrite_version_id =
                         rewrite.rewrite_version_id
                 )
                left join learning.essay_transfer_verification_versions transfer
                  on transfer.verification_version_id =
                     mastery.transfer_verification_version_id
                 and transfer.user_id = mastery.user_id
                where mastery.mastery_version_id = cast(:mastery_version_id as uuid)
                  and mastery.user_id = cast(:user_id as uuid)
                """
                ),
                {
                    "mastery_version_id": str(group[0]["mastery_version_id"]),
                    "user_id": user_id,
                },
            )
            .mappings()
            .one()
        )
        gap = _essay_closure_context_gap(context, decision_id=decision_id)
        if gap is not None:
            return _essay_closure_gap(gap)
        expected_rewrite_id = next(iter(rewrite_ids), None)
        if expected_rewrite_id != _optional_str(context["rewrite_version_id"]):
            return _essay_closure_gap("申论掌握度 head 引用的 rewrite 不是当前 owned writer 版本。")
        if expected_rewrite_id is not None and (
            context["rewrite_contract_version"] != "essay-rewrite-writer@v2"
            or context["rewrite_policy_version"] != "essay-mastery@v1"
        ):
            return _essay_closure_gap("申论掌握度 head 引用了非 canonical essay rewrite writer。")
        transfer_ids = {
            str(item["transfer_verification_version_id"])
            for item in group
            if item["transfer_verification_version_id"] is not None
        }
        if len(transfer_ids) > 1 or next(iter(transfer_ids), None) != _optional_str(
            context["transfer_verification_version_id"]
        ):
            return _essay_closure_gap("申论掌握度 head 引用的 transfer verification 不一致。")

        stage = "rewrite_due" if "rewrite_due" in states else "transfer_due"
        rewrite_version_id = _optional_str(context["rewrite_version_id"])
        if stage == "transfer_due":
            if (
                rewrite_version_id is None
                or context["rewrite_contract_version"] != "essay-rewrite-writer@v2"
                or context["rewrite_policy_version"] != "essay-mastery@v1"
                or not isinstance(context["rewrite_analysis_payload"], dict)
                or context["rewrite_analysis_payload"].get("status") != "passed"
            ):
                return _essay_closure_gap(
                    "申论 transfer_due head 缺少当前、已通过的 essay-rewrite-writer@v2 源稿。"
                )
            target = self._essay_fresh_transfer_content(
                session,
                source_task_version_id=str(context["source_task_version_id"]),
                source_material_bundle_version_id=str(context["source_material_bundle_version_id"]),
            )
            if target is None:
                return _essay_closure_gap(
                    "申论迁移验证缺少不同题目和材料的已发布任务，essay-transfer writer 当前不可执行。"
                )
            task_version_id = str(target["task_version_id"])
            material_bundle_version_id = str(target["material_bundle_version_id"])
            rubric_version_id = str(target["rubric_version_id"])
            question_type = str(target["question_type"])
            word_limit = int(target["word_limit"])
            maximum_score = float(target["maximum_score"])
        else:
            task_version_id = str(context["source_task_version_id"])
            material_bundle_version_id = str(context["source_material_bundle_version_id"])
            rubric_version_id = str(context["source_rubric_version_id"])
            question_type = str(context["question_type"])
            word_limit = int(context["word_limit"])
            maximum_score = float(context["maximum_score"])
        ordered = sorted(group, key=lambda item: str(item["finding_id"]))
        return EssayPlanningSnapshot(
            stage,  # type: ignore[arg-type]
            task_version_id,
            material_bundle_version_id=material_bundle_version_id,
            rubric_version_id=rubric_version_id,
            question_type=question_type,
            word_limit=word_limit,
            maximum_score=maximum_score,
            attempt_id=str(context["source_attempt_id"]),
            answer_version_id=str(context["current_answer_version_id"]),
            review_version_id=str(context["source_review_version_id"]),
            rewrite_version_id=rewrite_version_id,
            transfer_verification_version_id=_optional_str(
                context["transfer_verification_version_id"]
            ),
            review_acknowledgement_version_id=decision_id,
            weakest_dimension=_weakest_dimension(
                context["findings"],
                context["rewrite_plan"],
            ),
            weakest_dimension_key=_weakest_dimension_key(
                context["findings"],
                context["rewrite_plan"],
            ),
            mastery_state=stage,
            mastery_version_ids=tuple(str(item["mastery_version_id"]) for item in ordered),
            mastery_ids=tuple(str(item["mastery_id"]) for item in ordered),
            source_task_version_id=str(context["source_task_version_id"]),
            source_material_bundle_version_id=str(context["source_material_bundle_version_id"]),
            source_answer_version_id=str(context["source_answer_version_id"]),
            finding_decision_artifact_version_id=decision_id,
        )

    def _essay_fresh_transfer_content(
        self,
        session: Session,
        *,
        source_task_version_id: str,
        source_material_bundle_version_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select task.task_version_id::text, task.question_type,
                       task.word_limit, task.maximum_score,
                       material.material_bundle_version_id::text,
                       rubric.rubric_version_id::text
                from product.essay_task_versions task
                join product.essay_material_bundle_versions material
                  on material.task_version_id = task.task_version_id
                 and material.completeness = 'complete'
                join product.essay_rubric_versions rubric
                  on rubric.task_version_id = task.task_version_id
                 and rubric.material_bundle_version_id =
                     material.material_bundle_version_id
                 and rubric.state = 'published'
                where task.state = 'published'
                  and task.task_key like 'guokao%'
                  and task.task_version_id <> cast(:source_task_id as uuid)
                  and material.material_bundle_version_id <>
                      cast(:source_material_id as uuid)
                  and not exists (
                    select 1 from product.essay_task_versions successor
                    where successor.task_key = task.task_key
                      and successor.version_no > task.version_no
                      and successor.state = 'published'
                  )
                  and not exists (
                    select 1 from product.essay_material_bundle_versions successor
                    where successor.task_version_id = material.task_version_id
                      and successor.version_no > material.version_no
                      and successor.completeness = 'complete'
                  )
                  and not exists (
                    select 1 from product.essay_rubric_versions successor
                    where successor.task_version_id = rubric.task_version_id
                      and successor.material_bundle_version_id =
                          rubric.material_bundle_version_id
                      and successor.version_no > rubric.version_no
                      and successor.state = 'published'
                  )
                  and exists (
                    select 1 from product.essay_material_segments segment
                    where segment.material_bundle_version_id =
                          material.material_bundle_version_id
                  )
                order by exists (
                           select 1
                           from product.essay_material_segments source_segment
                           where source_segment.material_bundle_version_id =
                                 material.material_bundle_version_id
                             and (
                               coalesce(source_segment.source_metadata ->> 'paperTitle', '')
                                 like '%国考%'
                               or coalesce(source_segment.source_metadata ->> 'paperTitle', '')
                                 like '%国家公务员%'
                             )
                         ) desc,
                         task.created_at desc, task.task_version_id
                limit 1
                """
                ),
                {
                    "source_task_id": source_task_version_id,
                    "source_material_id": source_material_bundle_version_id,
                },
            )
            .mappings()
            .one_or_none()
        )
