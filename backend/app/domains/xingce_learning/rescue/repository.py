from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class RescueCandidateCause:
    """一道错题 + 它对应的可救场错因码。"""

    question_id: str
    cause_code: str


@dataclass(frozen=True, slots=True)
class RescueNodeLookup:
    """错题在知识树上命中的考点节点。"""

    node_id: str
    display_path: str
    module_name: str
    method_card_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RescueMethodCardRow:
    card_id: str
    title: str
    module_name: str
    tag_path: str
    recognition: str
    method_steps: tuple[str, ...]
    pitfall: str | None
    fix_action: str
    time_budget_sec: int


@dataclass(frozen=True, slots=True)
class RescueSimilarQuestionRow:
    """从题库按考点选出的同类题冻结内容。"""

    question_id: str
    content_version_id: str
    module_name: str
    stem: str
    options: tuple[dict[str, str], ...]
    correct_option_ids: tuple[str, ...]
    explanation: str | None


class RescueRepository:
    """错题救场的只读数据访问。

    一切读取都基于当前 fresh active 知识图谱发布（与 mock_exam / 选题同源），
    查询失败（发布过期、图谱不唯一）时返回 None，由 service 决定兜底。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def current_review(
        self,
        *,
        user_id: str,
        review_id: str,
    ) -> RowMapping | None:
        return (
            self._session.execute(
                text(
                    """
                    select review.*
                    from learning.xingce_attempt_review_versions review
                    where review.review_id = cast(:review_id as uuid)
                      and review.user_id = cast(:user_id as uuid)
                      and not exists (
                        select 1 from learning.xingce_attempt_review_versions successor
                        where successor.supersedes_review_version_id =
                              review.review_version_id
                      )
                    """
                ),
                {"user_id": user_id, "review_id": review_id},
            )
            .mappings()
            .one_or_none()
        )

    def confirmed_cause_codes(
        self,
        *,
        user_id: str,
        review_id: str,
    ) -> tuple[str, ...]:
        """当前生效的已确认错因码（会话级）。

        AI 确认用 cause_code；用户结构化改写用 replacement_code。旧确认
        （仅 user_replacement 桶、无结构化码）没有可识别错因，跳过。
        """
        rows = (
            self._session.execute(
                text(
                    """
                    select cause.cause_code, cause.replacement_code
                    from learning.xingce_confirmed_cause_versions cause
                    join learning.xingce_attempt_review_versions review
                      on review.cause_decision_version_id = cause.decision_version_id
                     and review.user_id = cause.user_id
                     and review.review_id = cause.review_id
                     and review.session_id = cause.session_id
                    where cause.user_id = cast(:user_id as uuid)
                      and cause.review_id = cast(:review_id as uuid)
                      and not exists (
                        select 1 from learning.xingce_attempt_review_versions successor
                        where successor.supersedes_review_version_id = review.review_version_id
                      )
                    order by cause.created_at, cause.confirmed_cause_version_id
                    """
                ),
                {"user_id": user_id, "review_id": review_id},
            )
            .mappings()
            .all()
        )
        codes: list[str] = []
        for row in rows:
            code = str(row["replacement_code"] or "") or str(row["cause_code"] or "")
            if code:
                codes.append(code)
        return tuple(codes)

    def per_question_candidate_codes(
        self,
        *,
        candidate_version_id: str | None,
    ) -> dict[str, str]:
        """候选 payload 里的逐题错因码（若有）。

        契约增量 questionCandidates 提供逐题 causeCode；旧候选没有该字段时
        返回空，回退到会话级已确认错因。
        """
        if candidate_version_id is None:
            return {}
        row = (
            self._session.execute(
                text(
                    """
                    select payload
                    from learning.xingce_cause_candidate_versions
                    where candidate_version_id = cast(:id as uuid)
                    """
                ),
                {"id": candidate_version_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return {}
        candidates = dict(row["payload"] or {}).get("questionCandidates") or []
        result: dict[str, str] = {}
        for item in candidates:
            if not isinstance(item, dict):
                continue
            question_id = str(item.get("questionId") or "")
            code = str(item.get("causeCode") or "")
            if question_id and code and item.get("status") == "candidate":
                result[question_id] = code
        return result

    def wrong_question_ids_for_review(
        self,
        *,
        user_id: str,
        session_id: str,
    ) -> tuple[str, ...]:
        """该复盘会话里客观答错的题（含超时/蒙错），用于兜底定位错题考点。"""
        rows = (
            self._session.execute(
                text(
                    """
                    select assessment.question_id
                    from learning.xingce_assessments assessment
                    where assessment.user_id = cast(:user_id as uuid)
                      and assessment.session_id = cast(:session_id as uuid)
                      and assessment.result_code in ('incorrect', 'timeout')
                    order by assessment.created_at, assessment.question_id
                    """
                ),
                {"user_id": user_id, "session_id": session_id},
            )
            .mappings()
            .all()
        )
        return tuple(str(row["question_id"]) for row in rows)

    def resolve_fresh_release(
        self,
    ) -> dict[str, Any] | None:
        """当前唯一 fresh active 知识图谱发布的标识参数。"""
        row = (
            self._session.execute(
                text(
                    """
                    with active_releases as (
                      select
                        release.release_id,
                        release.release_version,
                        release.source_data_version,
                        release.source_fingerprint,
                        release.vocab_version,
                        release.generator_version,
                        release.generated_at,
                        release.activated_at,
                        release.fresh_until,
                        count(*) over () as active_release_count
                      from product.knowledge_map_releases release
                      where release.exam_scope = 'guokao'
                        and release.track = 'xingce'
                        and release.status = 'active'
                    )
                    select
                      release_id,
                      release_version,
                      source_data_version,
                      source_fingerprint,
                      vocab_version,
                      generator_version,
                      generated_at,
                      activated_at,
                      fresh_until,
                      statement_timestamp() as resolved_at
                    from active_releases
                    where active_release_count = 1
                      and fresh_until > statement_timestamp()
                    """
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return {
            "release_id": str(row["release_id"]),
            "release_version": str(row["release_version"]),
            "source_data_version": str(row["source_data_version"]),
            "source_fingerprint": str(row["source_fingerprint"]),
            "vocab_version": str(row["vocab_version"]),
            "generator_version": str(row["generator_version"]),
            "generated_at": row["generated_at"],
            "activated_at": row["activated_at"],
            "fresh_until": row["fresh_until"],
            "resolved_at": row["resolved_at"],
        }

    def knowledge_nodes_for_questions(
        self,
        *,
        question_ids: list[str],
        release: dict[str, Any],
    ) -> dict[str, RescueNodeLookup]:
        """错题 -> 命中考点节点（method_card_ids 一并带回）。

        同一题可能命中多个节点，按发布权重取最靠前的一个；取不到时该题跳过。
        """
        if not question_ids:
            return {}
        rows = (
            self._session.execute(
                text(
                    """
                    with asked as (
                      select value as question_id, ordinality
                      from jsonb_array_elements_text(cast(:question_ids as jsonb))
                        with ordinality
                    ), ranked as (
                      select
                        asked.question_id,
                        asked.ordinality,
                        kmn.node_id,
                        kmn.display_path,
                        kmn.module_name,
                        kmn.method_card_ids,
                        row_number() over (
                          partition by asked.question_id
                          order by
                            kmn.near5_weight desc nulls last,
                            kmn.near5_occurrence_count desc nulls last,
                            kmn.node_id
                        ) as position
                      from asked
                      join product.knowledge_map_releases release
                        on release.release_id = cast(:release_id as uuid)
                       and release.release_version = :release_version
                       and release.exam_scope = 'guokao'
                       and release.track = 'xingce'
                       and release.source_data_version = :source_data_version
                       and release.source_fingerprint = :source_fingerprint
                       and release.vocab_version = :vocab_version
                       and release.generator_version = :generator_version
                       and release.generated_at = :generated_at
                       and release.activated_at = :activated_at
                       and release.fresh_until = :fresh_until
                      join product.knowledge_map_question_bindings binding
                        on binding.release_id = release.release_id
                       and binding.question_id = asked.question_id
                       and binding.delivery_eligible
                      join product.knowledge_map_nodes kmn
                        on kmn.release_id = binding.release_id
                       and kmn.node_id = binding.node_id
                       and kmn.exam_scope = 'guokao'
                       and kmn.track = 'xingce'
                       and kmn.status = 'published'
                       and kmn.support_status = 'text_supported'
                    )
                    select question_id, node_id, display_path, module_name,
                           method_card_ids
                    from ranked
                    where position = 1
                    order by ordinality
                    """
                ),
                {
                    "question_ids": _json(question_ids),
                    **_release_params(release),
                },
            )
            .mappings()
            .all()
        )
        return {
            str(row["question_id"]): RescueNodeLookup(
                node_id=str(row["node_id"]),
                display_path=str(row["display_path"]),
                module_name=str(row["module_name"]),
                method_card_ids=tuple(
                    str(item) for item in (row["method_card_ids"] or [])
                ),
            )
            for row in rows
        }

    def method_cards_by_ids(
        self,
        card_ids: list[str],
    ) -> dict[str, RescueMethodCardRow]:
        if not card_ids:
            return {}
        rows = (
            self._session.execute(
                text(
                    """
                    select id, title, module_name, tag_path, recognition,
                           method_steps, pitfall, fix_action, time_budget_sec
                    from product.method_cards
                    where id = any(cast(:card_ids as text[]))
                      and status = 'published'
                      and track = 'xingce'
                    """
                ),
                {"card_ids": card_ids},
            )
            .mappings()
            .all()
        )
        return {
            str(row["id"]): RescueMethodCardRow(
                card_id=str(row["id"]),
                title=str(row["title"]),
                module_name=str(row["module_name"]),
                tag_path=str(row["tag_path"]),
                recognition=str(row["recognition"]),
                method_steps=tuple(str(item) for item in (row["method_steps"] or [])),
                pitfall=(str(row["pitfall"]) if row["pitfall"] else None),
                fix_action=str(row["fix_action"]),
                time_budget_sec=int(row["time_budget_sec"]),
            )
            for row in rows
        }

    def select_similar_question(
        self,
        *,
        user_id: str,
        node_id: str,
        module_name: str,
        release: dict[str, Any],
        excluded_question_ids: list[str],
        limit: int,
    ) -> list[RescueSimilarQuestionRow]:
        """按考点节点从题库选同类题（排除本组已做过的题）。

        与正式选题同源：只挑 delivery_eligible 且未被该用户答过/暴露过的题。
        """
        if limit <= 0:
            return []
        rows = (
            self._session.execute(
                text(
                    """
                    with candidate_ids as materialized (
                      select
                        binding.question_id,
                        digest(binding.question_id, 'sha256') as selection_key
                      from product.knowledge_map_releases release
                      join product.knowledge_map_question_bindings binding
                        on binding.release_id = release.release_id
                       and binding.node_id = cast(:node_id as text)
                       and binding.canonical_module = :module_name
                       and binding.delivery_eligible
                      join product.knowledge_map_nodes kmn
                        on kmn.release_id = binding.release_id
                       and kmn.node_id = binding.node_id
                       and kmn.status = 'published'
                       and kmn.support_status = 'text_supported'
                       and kmn.near5_occurrence_count > 0
                      where release.release_id = cast(:release_id as uuid)
                       and release.release_version = :release_version
                       and release.exam_scope = 'guokao'
                       and release.track = 'xingce'
                       and release.source_data_version = :source_data_version
                       and release.source_fingerprint = :source_fingerprint
                       and release.vocab_version = :vocab_version
                       and release.generator_version = :generator_version
                       and release.generated_at = :generated_at
                       and release.activated_at = :activated_at
                       and release.fresh_until = :fresh_until
                       and not (
                         binding.question_id =
                           any(cast(:excluded_question_ids as text[]))
                       )
                       and not exists (
                         select 1
                         from learning.xingce_assessments assessment
                         where assessment.user_id = cast(:user_id as uuid)
                           and assessment.question_id = binding.question_id
                       )
                       and not exists (
                         select 1
                         from learning.xingce_question_exposures exposure
                         where exposure.user_id = cast(:user_id as uuid)
                           and exposure.question_id = binding.question_id
                       )
                    )
                    select
                      binding.question_id,
                      min(binding.content_version_id) as content_version_id,
                      min(binding.canonical_module) as canonical_module,
                      bool_and(binding.visual_required) as visual_required,
                      bool_and(binding.shared_context_required)
                        as shared_context_required,
                      (
                        count(distinct binding.content_version_id) = 1
                        and count(distinct binding.canonical_module) = 1
                        and count(distinct binding.visual_required) = 1
                        and count(
                          distinct binding.shared_context_required
                        ) = 1
                      ) as binding_unambiguous,
                      content.snapshot_payload
                    from candidate_ids candidate
                    join product.knowledge_map_question_bindings binding
                      on binding.release_id = cast(:release_id as uuid)
                     and binding.question_id = candidate.question_id
                    join product.question_content_versions content
                      on content.question_id = binding.question_id
                     and content.content_version_id =
                         binding.content_version_id
                     and content.track = 'xingce'
                    group by
                      binding.question_id,
                      candidate.selection_key,
                      content.snapshot_payload
                    order by candidate.selection_key, binding.question_id
                    limit :limit
                    """
                ),
                {
                    "node_id": node_id,
                    "module_name": module_name,
                    "excluded_question_ids": excluded_question_ids,
                    "limit": limit,
                    "user_id": user_id,
                    **_release_params(release),
                },
            )
            .mappings()
            .all()
        )
        result: list[RescueSimilarQuestionRow] = []
        for row in rows:
            if not bool(row["binding_unambiguous"]):
                continue
            if bool(row["visual_required"]) or bool(row["shared_context_required"]):
                # 救场同类题必须是单题可独立作答的客观题；
                # 需要共享材料/图片的题不适合内嵌展示，跳过。
                continue
            snapshot = dict(row["snapshot_payload"] or {})
            options = snapshot.get("options")
            if not isinstance(options, list):
                continue
            option_rows: list[dict[str, str]] = []
            for option in options:
                if not isinstance(option, dict):
                    continue
                label = str(option.get("label", "")).strip()
                text_value = str(option.get("option_text", "")).strip()
                if not label or not text_value:
                    continue
                option_rows.append({"label": label, "option_text": text_value})
            correct = _correct_option_ids(snapshot)
            if not correct or not option_rows:
                continue
            stem = str(snapshot.get("stem") or "").strip()
            if not stem:
                continue
            result.append(
                RescueSimilarQuestionRow(
                    question_id=str(row["question_id"]),
                    content_version_id=str(row["content_version_id"]),
                    module_name=str(row["canonical_module"]),
                    stem=stem,
                    options=tuple(option_rows),
                    correct_option_ids=correct,
                    explanation=_published_explanation(snapshot),
                )
            )
            if len(result) >= limit:
                break
        return result


def _correct_option_ids(snapshot: dict[str, Any]) -> tuple[str, ...]:
    from app.domains.task_content.service import answer_options

    return tuple(answer_options(str(snapshot.get("answer") or "")))


def _published_explanation(snapshot: dict[str, Any]) -> str | None:
    from app.domains.xingce_learning.explanation_policy import displayable_explanation

    explanation, _source = displayable_explanation(snapshot)
    return explanation


def _release_params(release: dict[str, Any]) -> dict[str, Any]:
    return {
        "release_id": release["release_id"],
        "release_version": release["release_version"],
        "source_data_version": release["source_data_version"],
        "source_fingerprint": release["source_fingerprint"],
        "vocab_version": release["vocab_version"],
        "generator_version": release["generator_version"],
        "generated_at": release["generated_at"],
        "activated_at": release["activated_at"],
        "fresh_until": release["fresh_until"],
    }


def _json(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
