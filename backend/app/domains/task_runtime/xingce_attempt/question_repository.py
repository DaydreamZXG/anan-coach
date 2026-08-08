from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.knowledge.module_vocabulary import load_current_module_vocabulary
from app.domains.knowledge.repository import KnowledgeRepository
from app.domains.task_content.service import (
    TaskContentContractError,
    validate_learner_question,
)

_MIN_QUESTION_CANDIDATE_POOL_SIZE = 24
_QUESTION_CANDIDATE_POOL_MULTIPLIER = 8
_MAX_QUESTION_CANDIDATE_BATCH_SIZE = 96
_QUESTION_SELECTION_SALT = "xingce-question-selection@v1"
_QUESTION_SELECTION_LOCK_NAMESPACE = "xingce-question-selection"


@dataclass(frozen=True)
class TargetedQuestionSelection:
    questions: tuple[dict[str, Any], ...]
    release_id: str
    release_version: str
    source_data_version: str
    vocab_version: str
    fresh_until: datetime
    node_id: str
    node_type: str
    module_name: str


def _bounded_question_candidate_batch_size(limit: int) -> int:
    diversified_size = max(
        _MIN_QUESTION_CANDIDATE_POOL_SIZE,
        limit * _QUESTION_CANDIDATE_POOL_MULTIPLIER,
    )
    return min(_MAX_QUESTION_CANDIDATE_BATCH_SIZE, diversified_size)


def _stable_question_rank(
    *,
    release_id: str,
    user_id: str,
    question_id: str,
) -> bytes:
    seed = "\x1f".join(
        (
            _QUESTION_SELECTION_SALT,
            release_id,
            user_id,
            question_id,
        )
    )
    return sha256(seed.encode("utf-8")).digest()


def _stable_question_distribution_key(question_id: str) -> bytes:
    """Mirror the indexed digest expression in migration 20260730_77."""
    return sha256(question_id.encode("utf-8")).digest()


def _stable_question_selection_cursor(
    *,
    release_id: str,
    user_id: str,
) -> bytes:
    seed = "\x1f".join(
        (
            _QUESTION_SELECTION_SALT,
            "cursor",
            release_id,
            user_id,
        )
    )
    return sha256(seed.encode("utf-8")).digest()


class XingceQuestionRepository:
    """Neutral frozen-question selector shared by planning and Xingce runtime."""

    def select_frozen_questions(
        self,
        session: Session,
        *,
        module_name: str | None,
        limit: int,
        user_id: str | None = None,
        excluded_question_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        vocabulary = load_current_module_vocabulary(
            session,
            exam_scope="guokao",
            track="xingce",
        )
        if vocabulary is None:
            return []
        canonical_module = (
            vocabulary.canonicalize(module_name) if module_name is not None else None
        )
        if module_name is not None and canonical_module is None:
            return []
        return self._select_eligible_questions(
            session,
            release_id=vocabulary.release_id,
            node_id=None,
            canonical_modules=(
                (canonical_module,) if canonical_module is not None else None
            ),
            limit=limit,
            user_id=user_id,
            excluded_question_ids=excluded_question_ids,
        )

    def select_targeted_frozen_questions(
        self,
        session: Session,
        *,
        module_name: str | None,
        limit: int,
        user_id: str,
        now: datetime,
    ) -> TargetedQuestionSelection | None:
        normalized_module = module_name.strip() if module_name is not None else None
        if limit <= 0 or normalized_module == "":
            return None

        vocabulary = load_current_module_vocabulary(
            session,
            exam_scope="guokao",
            track="xingce",
            now=now,
        )
        if vocabulary is None:
            return None
        canonical_module = (
            vocabulary.canonicalize(normalized_module)
            if normalized_module is not None
            else None
        )
        if normalized_module is not None and canonical_module is None:
            return None

        repository = KnowledgeRepository(session)
        if canonical_module is None:
            nodes = []
            for module_name_candidate in vocabulary.canonical_names:
                nodes.extend(
                    repository.list_knowledge_map_nodes_for_release(
                        release_id=vocabulary.release_id,
                        node_types=("micro_skill", "knowledge_point"),
                        module_name=module_name_candidate,
                    )
                )
            nodes.sort(
                key=lambda node: (
                    ("micro_skill", "knowledge_point").index(node.node_type),
                    -node.near5_weight,
                    -node.near5_occurrence_count,
                    node.module_name,
                    node.tag_type or "",
                    node.tag_value,
                    node.node_id,
                )
            )
        else:
            nodes = repository.list_knowledge_map_nodes_for_release(
                release_id=vocabulary.release_id,
                node_types=("micro_skill", "knowledge_point"),
                module_name=canonical_module,
            )
        for node in nodes:
            if (
                node.support_status != "text_supported"
                or node.tag_type is None
                or not node.tag_type.strip()
                or not node.tag_value.strip()
                or node.near5_occurrence_count <= 0
            ):
                continue
            questions = self._select_eligible_questions(
                session,
                release_id=node.release_id,
                node_id=node.node_id,
                canonical_modules=(node.module_name,),
                limit=limit,
                user_id=user_id,
                excluded_question_ids=None,
            )
            if len(questions) < limit:
                continue
            return TargetedQuestionSelection(
                questions=tuple(questions),
                release_id=node.release_id,
                release_version=node.release_version,
                source_data_version=node.source_data_version,
                vocab_version=vocabulary.vocab_version,
                fresh_until=node.fresh_until,
                node_id=node.node_id,
                node_type=node.node_type,
                module_name=node.module_name,
            )
        return None

    def _select_eligible_questions(
        self,
        session: Session,
        *,
        release_id: str,
        node_id: str | None,
        canonical_modules: tuple[str, ...] | None,
        limit: int,
        user_id: str | None,
        excluded_question_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        if user_id is not None:
            session.execute(
                text(
                    """
                    select pg_advisory_xact_lock(
                      hashtextextended(:selection_lock_key, 0)
                    )
                    """
                ),
                {
                    "selection_lock_key": (
                        f"{_QUESTION_SELECTION_LOCK_NAMESPACE}:{user_id}"
                    )
                },
            )
        query_parameters: dict[str, Any] = {
            "release_id": release_id,
            "node_id": node_id,
            "canonical_modules": (
                list(canonical_modules) if canonical_modules is not None else None
            ),
            "user_id": user_id,
            "excluded_question_ids": excluded_question_ids or [],
        }
        binding_scope_conditions = [
            "binding.release_id = cast(:release_id as uuid)",
            "binding.delivery_eligible",
        ]
        if node_id is not None:
            binding_scope_conditions.append(
                "binding.node_id = cast(:node_id as text)"
            )
        if canonical_modules is not None:
            if len(canonical_modules) == 1:
                binding_scope_conditions.append(
                    "binding.canonical_module = cast(:canonical_module as text)"
                )
                query_parameters["canonical_module"] = canonical_modules[0]
            else:
                binding_scope_conditions.append(
                    "binding.canonical_module = any(cast(:canonical_modules as text[]))"
                )
        if excluded_question_ids:
            binding_scope_conditions.append(
                "not (binding.question_id = any(cast(:excluded_question_ids as text[])))"
            )
        candidate_conditions = list(binding_scope_conditions)
        if user_id is not None:
            candidate_conditions.extend(
                (
                    """
                    not exists (
                      select 1
                      from learning.xingce_assessments assessment
                      where assessment.user_id = cast(:user_id as uuid)
                        and assessment.question_id = binding.question_id
                    )
                    """,
                    """
                    not exists (
                      select 1
                      from learning.xingce_question_exposures exposure
                      where exposure.user_id = cast(:user_id as uuid)
                        and exposure.question_id = binding.question_id
                    )
                    """,
                )
            )
        binding_scope_sql = "\n                    and ".join(
            condition.strip() for condition in binding_scope_conditions
        )
        candidate_scope_sql = "\n                    and ".join(
            condition.strip() for condition in candidate_conditions
        )
        selection_cursor = (
            _stable_question_selection_cursor(
                release_id=release_id,
                user_id=user_id,
            )
            if user_id is not None
            else bytes(sha256().digest_size)
        )
        candidate_batch_size = _bounded_question_candidate_batch_size(limit)
        questions_by_id: dict[str, tuple[dict[str, Any], int | None, int]] = {}
        examined_question_ids: set[str] = set()
        cursor_selection_key: bytes | None = None
        cursor_question_id: str | None = None
        wrapped = False

        while True:
            if wrapped:
                range_condition = (
                    "digest(binding.question_id, 'sha256') "
                    "< cast(:selection_cursor as bytea)"
                )
                if cursor_selection_key is not None:
                    range_condition += """
                      and (
                        digest(binding.question_id, 'sha256'),
                        binding.question_id
                      ) > (
                        cast(:cursor_selection_key as bytea),
                        cast(:cursor_question_id as text)
                      )
                    """
            elif cursor_selection_key is None:
                range_condition = (
                    "digest(binding.question_id, 'sha256') "
                    ">= cast(:selection_cursor as bytea)"
                )
            else:
                range_condition = """
                  (
                    digest(binding.question_id, 'sha256'),
                    binding.question_id
                  ) > (
                    cast(:cursor_selection_key as bytea),
                    cast(:cursor_question_id as text)
                  )
                """
            rows = list(
                session.execute(
                    text(
                        f"""
                with candidate_ids as materialized (
                  select
                    binding.question_id,
                    digest(binding.question_id, 'sha256') as selection_key
                  from product.knowledge_map_question_bindings binding
                  where {candidate_scope_sql}
                    and {range_condition}
                  group by
                    binding.question_id,
                    digest(binding.question_id, 'sha256')
                  order by
                    digest(binding.question_id, 'sha256'),
                    binding.question_id
                  limit :candidate_batch_size
                ),
                frozen_candidates as (
                  select
                    binding.question_id,
                    candidate_id.selection_key,
                    min(binding.content_version_id) as content_version_id,
                    min(binding.canonical_module) as canonical_module,
                    bool_and(binding.visual_required) as visual_required,
                    bool_and(
                      binding.shared_context_required
                    ) as shared_context_required,
                    max(binding.latest_year) as latest_year,
                    max(binding.occurrence_count) as occurrence_count,
                    (
                      count(distinct binding.content_version_id) = 1
                      and count(distinct binding.canonical_module) = 1
                      and count(distinct binding.visual_required) = 1
                      and count(
                        distinct binding.shared_context_required
                      ) = 1
                    ) as binding_unambiguous
                  from candidate_ids candidate_id
                  join product.knowledge_map_question_bindings binding
                    on binding.release_id = cast(:release_id as uuid)
                   and binding.question_id = candidate_id.question_id
                  where {binding_scope_sql}
                  group by binding.question_id, candidate_id.selection_key
                )
                select
                  candidate.question_id,
                  candidate.selection_key,
                  candidate.content_version_id,
                  candidate.canonical_module,
                  candidate.shared_context_required,
                  candidate.visual_required,
                  candidate.latest_year,
                  candidate.occurrence_count,
                  candidate.binding_unambiguous,
                  content.snapshot_payload
                from frozen_candidates candidate
                left join product.question_content_versions content
                  on content.question_id = candidate.question_id
                 and content.content_version_id =
                     candidate.content_version_id
                 and content.track = 'xingce'
                order by
                  candidate.selection_key,
                  candidate.question_id
                """
                    ),
                    {
                        **query_parameters,
                        "selection_cursor": selection_cursor,
                        "cursor_selection_key": cursor_selection_key,
                        "cursor_question_id": cursor_question_id,
                        "candidate_batch_size": candidate_batch_size,
                    },
                )
                .mappings()
                .all()
            )
            if not rows:
                if wrapped:
                    break
                wrapped = True
                cursor_selection_key = None
                cursor_question_id = None
                continue
            cursor_selection_key = bytes(rows[-1]["selection_key"])
            cursor_question_id = str(rows[-1]["question_id"])
            for row in rows:
                question_id = str(row["question_id"])
                if question_id in examined_question_ids:
                    continue
                examined_question_ids.add(question_id)
                if (
                    not bool(row["binding_unambiguous"])
                    or row["snapshot_payload"] is None
                ):
                    continue
                question = dict(row["snapshot_payload"])
                question["question_id"] = question_id
                question["visual_required"] = bool(row["visual_required"])
                question["shared_context_required"] = bool(
                    row["shared_context_required"]
                )
                question["_content_version_id"] = str(row["content_version_id"])
                try:
                    validate_learner_question(question)
                except TaskContentContractError:
                    continue
                questions_by_id[question_id] = (
                    question,
                    (
                        int(row["latest_year"])
                        if row["latest_year"] is not None
                        else None
                    ),
                    int(row["occurrence_count"]),
                )
            if len(questions_by_id) >= limit:
                break
            if len(rows) < candidate_batch_size and not wrapped:
                wrapped = True
                cursor_selection_key = None
                cursor_question_id = None

        ranked_questions = sorted(
            questions_by_id.values(),
            key=lambda item: (
                -(item[1] if item[1] is not None else -1),
                -item[2],
                (
                    _stable_question_rank(
                        release_id=release_id,
                        user_id=user_id,
                        question_id=str(item[0]["question_id"]),
                    )
                    if user_id is not None
                    else str(item[0]["question_id"]).encode()
                ),
                str(item[0]["question_id"]),
            ),
        )
        return [question for question, _year, _count in ranked_questions[:limit]]
