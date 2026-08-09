from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.ability_estimation.shadow_v2 import aggregate_question_difficulty
from app.domains.knowledge.module_vocabulary import (
    ReleasedModuleVocabulary,
    load_current_module_vocabulary,
)
from app.domains.knowledge.repository import KnowledgeRepository
from app.domains.task_content.service import (
    TaskContentContractError,
    validate_learner_question,
)
from app.domains.task_runtime.xingce_attempt.contracts import (
    XINGCE_SELECTION_RESULT_V1,
    selection_intent_fingerprint,
    validate_selection_intent,
)

_MIN_QUESTION_CANDIDATE_POOL_SIZE = 24
_QUESTION_CANDIDATE_POOL_MULTIPLIER = 8
_MAX_QUESTION_CANDIDATE_BATCH_SIZE = 96
_QUESTION_SELECTION_SALT = "xingce-question-selection@v1"
_QUESTION_SELECTION_LOCK_NAMESPACE = "xingce-question-selection"
_DATA_ANALYSIS_MODULE = "资料分析"
_DATA_ANALYSIS_GROUP_SIZE = 5


@dataclass(frozen=True)
class TargetedQuestionSelection:
    questions: tuple[dict[str, Any], ...]
    release_id: str
    release_version: str
    source_data_version: str
    vocab_version: str
    fresh_until: datetime
    node_id: str | None
    node_type: str | None
    module_name: str
    selection_result: dict[str, Any] | None = None


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
    selection_identity: str | None = None,
) -> bytes:
    seed = "\x1f".join(
        (
            _QUESTION_SELECTION_SALT,
            selection_identity or release_id,
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
    selection_identity: str | None = None,
) -> bytes:
    seed = "\x1f".join(
        (
            _QUESTION_SELECTION_SALT,
            "cursor",
            selection_identity or release_id,
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

    def select_frozen_questions_for_intent(
        self,
        session: Session,
        *,
        intent: object,
        limit: int,
        user_id: str,
        now: datetime,
    ) -> TargetedQuestionSelection | None:
        """Consume one frozen Planning intent through an immutable tier order."""

        frozen = validate_selection_intent(intent)
        if limit <= 0:
            return None
        release = frozen["knowledgeRelease"]
        vocabulary = self._current_module_vocabulary(session, now=now)
        frozen_fresh_until = datetime.fromisoformat(str(release["freshUntil"]))
        if (
            vocabulary is None
            or vocabulary.release_id != release["releaseId"]
            or vocabulary.release_version != release["releaseVersion"]
            or vocabulary.source_data_version != release["sourceDataVersion"]
            or vocabulary.vocab_version != release["vocabVersion"]
            or vocabulary.fresh_until != frozen_fresh_until
            or vocabulary.canonicalize(str(frozen["targetModule"]))
            != frozen["targetModule"]
        ):
            return None
        release_id = str(release["releaseId"])
        module_name = str(frozen["targetModule"])
        knowledge = frozen["targetKnowledgeNode"]
        micro = frozen["targetMicroSkillNode"]
        cause = frozen["confirmedCauseFocus"]
        tiers: list[
            tuple[
                str,
                str | None,
                tuple[str, ...] | None,
                str | None,
                str,
            ]
        ] = []
        if knowledge is not None and cause is not None and (
            micro is not None or cause["questionType"] is not None
        ):
            tiers.append(
                (
                    "exact_knowledge_and_cause",
                    None,
                    tuple(
                        item
                        for item in (
                            str(knowledge["nodeId"]),
                            str(micro["nodeId"]) if micro is not None else None,
                        )
                        if item is not None
                    ),
                    str(cause["questionType"]) if cause["questionType"] is not None else None,
                    "insufficient_eligible_questions",
                )
            )
        if knowledge is not None:
            tiers.append(
                (
                    "same_knowledge",
                    str(knowledge["nodeId"]),
                    None,
                    None,
                    "insufficient_eligible_questions",
                )
            )
        if micro is not None:
            tiers.append(
                (
                    "same_micro_skill",
                    str(micro["nodeId"]),
                    None,
                    None,
                    "insufficient_eligible_questions",
                )
            )
        if (
            knowledge is None
            and micro is None
            and cause is not None
            and cause["questionType"] is not None
        ):
            tiers.append(
                (
                    "module",
                    None,
                    None,
                    str(cause["questionType"]),
                    "cause_question_type_supply_insufficient",
                )
            )
        if frozen["fallbackReason"] in {
            "cold_start_no_grounded_evidence",
            "insufficient_knowledge_point_evidence",
        }:
            tiers.append(
                (
                    "high_frequency_cold_start",
                    None,
                    None,
                    None,
                    "insufficient_eligible_questions",
                )
            )
        else:
            tiers.append(
                (
                    "module",
                    None,
                    None,
                    None,
                    "insufficient_eligible_questions",
                )
            )

        skipped: list[dict[str, Any]] = []
        for (
            tier,
            node_id,
            required_node_ids,
            question_type,
            insufficient_reason,
        ) in tiers:
            canonical_modules = (module_name,)
            pool = self._select_eligible_questions(
                session,
                release_id=release_id,
                node_id=node_id,
                canonical_modules=canonical_modules,
                limit=_MAX_QUESTION_CANDIDATE_BATCH_SIZE,
                user_id=user_id,
                excluded_question_ids=None,
                required_node_ids=required_node_ids,
                question_type=question_type,
                selection_identity=str(frozen["intentId"]),
            )
            ranked, ranking_evidence = self._rank_intent_candidates(
                session,
                questions=pool,
                difficulty_policy=frozen["difficultyPolicy"],
                limit=limit,
            )
            if len(ranked) < limit:
                skipped.append(
                    {
                        "tier": tier,
                        "reason": insufficient_reason,
                        "eligibleCount": len(ranked),
                    }
                )
                continue
            hit_reason = (
                "all_required_selectors_matched"
                if tier == "exact_knowledge_and_cause"
                else "first_tier_with_sufficient_novel_supply"
            )
            result = {
                "contractVersion": XINGCE_SELECTION_RESULT_V1,
                "intentId": selection_intent_fingerprint(frozen),
                "hitTier": tier,
                "hitReason": hit_reason,
                "fallbackReason": frozen["fallbackReason"],
                "skippedTiers": skipped,
                "difficultyPolicy": frozen["difficultyPolicy"],
                "questionRankingEvidence": ranking_evidence,
                "questionIds": [str(item["question_id"]) for item in ranked],
            }
            selected_node = knowledge if knowledge is not None else micro
            return TargetedQuestionSelection(
                questions=tuple(ranked),
                release_id=release_id,
                release_version=str(release["releaseVersion"]),
                source_data_version=str(release["sourceDataVersion"]),
                vocab_version=str(release["vocabVersion"]),
                fresh_until=datetime.fromisoformat(str(release["freshUntil"])),
                node_id=(str(selected_node["nodeId"]) if selected_node is not None else None),
                node_type=(str(selected_node["nodeType"]) if selected_node is not None else None),
                module_name=module_name,
                selection_result=result,
            )
        return None

    @staticmethod
    def _current_module_vocabulary(
        session: Session,
        *,
        now: datetime,
    ) -> ReleasedModuleVocabulary | None:
        return load_current_module_vocabulary(
            session,
            exam_scope="guokao",
            track="xingce",
            now=now,
        )

    @staticmethod
    def _rank_intent_candidates(
        session: Session,
        *,
        questions: list[dict[str, Any]],
        difficulty_policy: dict[str, Any],
        limit: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        question_ids = [str(item["question_id"]) for item in questions]
        occurrence_rows = session.execute(
            text(
                """
                select question_id, occurrence_correct_ratio, occurrence_sample_size
                from product.question_occurrences
                where question_id = any(cast(:question_ids as text[]))
                order by question_id, occurrence_id
                """
            ),
            {"question_ids": question_ids},
        ).mappings().all() if question_ids else []
        occurrences: dict[str, list[tuple[float | None, int | None]]] = {}
        for row in occurrence_rows:
            occurrences.setdefault(str(row["question_id"]), []).append(
                (
                    float(row["occurrence_correct_ratio"])
                    if row["occurrence_correct_ratio"] is not None
                    else None,
                    int(row["occurrence_sample_size"])
                    if row["occurrence_sample_size"] is not None
                    else None,
                )
            )
        target_theta = (
            float(difficulty_policy["targetTheta"])
            if difficulty_policy["state"] == "known"
            else None
        )
        enriched: list[tuple[dict[str, Any], int, float | None, float | None]] = []
        for stable_rank, question in enumerate(questions):
            question_id = str(question["question_id"])
            difficulty = aggregate_question_difficulty(occurrences.get(question_id, []))
            distance = (
                abs(difficulty - target_theta)
                if difficulty is not None and target_theta is not None
                else None
            )
            enriched.append((question, stable_rank, difficulty, distance))
        if target_theta is not None:
            enriched.sort(
                key=lambda item: (
                    item[3] is None,
                    item[3] if item[3] is not None else 0.0,
                    item[1],
                    str(item[0]["question_id"]),
                )
            )
        selected = enriched[:limit]
        evidence = [
            {
                "questionId": str(question["question_id"]),
                "rank": rank,
                "anchorState": "known" if difficulty is not None else "unknown",
                "difficultyB": difficulty,
                "distanceToTargetTheta": distance,
                "stablePoolRank": stable_rank,
            }
            for rank, (question, stable_rank, difficulty, distance) in enumerate(
                selected,
                start=1,
            )
        ]
        return [item[0] for item in selected], evidence

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
        required_node_ids: tuple[str, ...] | None = None,
        question_type: str | None = None,
        selection_identity: str | None = None,
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
            "required_node_ids": list(required_node_ids or ()),
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
        if required_node_ids:
            candidate_conditions.append(
                """
                (
                  select count(distinct required_binding.node_id)
                  from product.knowledge_map_question_bindings required_binding
                  where required_binding.release_id = cast(:release_id as uuid)
                    and required_binding.question_id = binding.question_id
                    and required_binding.delivery_eligible
                    and required_binding.node_id = any(cast(:required_node_ids as text[]))
                ) = cardinality(cast(:required_node_ids as text[]))
                """
            )
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
                selection_identity=selection_identity,
            )
            if user_id is not None
            else bytes(sha256().digest_size)
        )
        data_analysis_only = canonical_modules == (_DATA_ANALYSIS_MODULE,)
        effective_limit = (
            max(
                _DATA_ANALYSIS_GROUP_SIZE,
                ((limit + _DATA_ANALYSIS_GROUP_SIZE - 1) // _DATA_ANALYSIS_GROUP_SIZE)
                * _DATA_ANALYSIS_GROUP_SIZE,
            )
            if data_analysis_only
            else limit
        )
        candidate_batch_size = _bounded_question_candidate_batch_size(effective_limit)
        required_candidate_pool_size = (
            _MAX_QUESTION_CANDIDATE_BATCH_SIZE
            if data_analysis_only
            else effective_limit
        )
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
                if question_type is not None and str(
                    question.get("question_type", question.get("questionType", ""))
                ) != question_type:
                    continue
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
            if len(questions_by_id) >= required_candidate_pool_size:
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
                        selection_identity=selection_identity,
                    )
                    if user_id is not None
                    else str(item[0]["question_id"]).encode()
                ),
                str(item[0]["question_id"]),
            ),
        )
        ranked = [question for question, _year, _count in ranked_questions]
        if data_analysis_only:
            return self._select_complete_data_analysis_groups(
                session,
                release_id=release_id,
                seed_questions=ranked,
                limit=effective_limit,
                user_id=user_id,
                excluded_question_ids=excluded_question_ids,
            )
        return ranked[:effective_limit]

    def _select_complete_data_analysis_groups(
        self,
        session: Session,
        *,
        release_id: str,
        seed_questions: list[dict[str, Any]],
        limit: int,
        user_id: str | None,
        excluded_question_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        """Return only intact five-question shared-material groups.

        Canonical questions may choose representatives from different regional
        papers, so ``product.questions.material_id`` is not a stable group key.
        The occurrence paper plus the exact published material is authoritative:
        one valid unit must contain five consecutive question numbers.  Missing
        material, partial novelty, ambiguous content versions, and split groups
        are rejected as a whole.
        """

        seed_ids = [str(item["question_id"]) for item in seed_questions]
        if not seed_ids or limit < _DATA_ANALYSIS_GROUP_SIZE:
            return []
        rows = session.execute(
            text(
                """
                with seed_questions as (
                  select seed.question_id, seed.ordinality::integer as seed_rank
                  from unnest(cast(:seed_question_ids as text[]))
                       with ordinality as seed(question_id, ordinality)
                ),
                candidate_group_keys as (
                  select
                    coalesce(
                      nullif(btrim(occurrence.occurrence_paper_title), ''),
                      nullif(btrim(occurrence.paper_title), ''),
                      nullif(btrim(occurrence.source_path), '')
                    ) as paper_key,
                    digest(question.material_text, 'sha256') as material_key,
                    min(seed.seed_rank) as seed_rank
                  from seed_questions seed
                  join product.questions question
                    on question.question_id = seed.question_id
                  join product.question_occurrences occurrence
                    on occurrence.question_id = question.question_id
                  where question.module_name = '资料分析'
                    and nullif(btrim(coalesce(question.material_text, '')), '') is not null
                    and occurrence.number is not null
                    and coalesce(
                          nullif(btrim(occurrence.occurrence_paper_title), ''),
                          nullif(btrim(occurrence.paper_title), ''),
                          nullif(btrim(occurrence.source_path), '')
                        ) is not null
                  group by
                    coalesce(
                      nullif(btrim(occurrence.occurrence_paper_title), ''),
                      nullif(btrim(occurrence.paper_title), ''),
                      nullif(btrim(occurrence.source_path), '')
                    ),
                    digest(question.material_text, 'sha256')
                ),
                release_binding as (
                  select
                    binding.question_id,
                    min(binding.content_version_id) as content_version_id,
                    bool_and(binding.shared_context_required) as shared_context_required,
                    bool_and(binding.visual_required) as visual_required,
                    max(binding.latest_year) as latest_year,
                    max(binding.occurrence_count) as occurrence_count,
                    (
                      count(distinct binding.content_version_id) = 1
                      and count(distinct binding.canonical_module) = 1
                      and count(distinct binding.shared_context_required) = 1
                      and count(distinct binding.visual_required) = 1
                    ) as binding_unambiguous
                  from product.knowledge_map_question_bindings binding
                  where binding.release_id = cast(:release_id as uuid)
                    and binding.delivery_eligible
                    and binding.canonical_module = '资料分析'
                  group by binding.question_id
                ),
                group_members as (
                  select
                    group_key.paper_key,
                    group_key.material_key,
                    group_key.seed_rank,
                    question.question_id,
                    min(occurrence.number) as question_number,
                    binding.content_version_id,
                    binding.shared_context_required,
                    binding.visual_required,
                    binding.latest_year,
                    binding.occurrence_count,
                    binding.binding_unambiguous,
                    content.snapshot_payload
                  from candidate_group_keys group_key
                  join product.question_occurrences occurrence
                    on coalesce(
                         nullif(btrim(occurrence.occurrence_paper_title), ''),
                         nullif(btrim(occurrence.paper_title), ''),
                         nullif(btrim(occurrence.source_path), '')
                       ) = group_key.paper_key
                  join product.questions question
                    on question.question_id = occurrence.question_id
                   and question.module_name = '资料分析'
                   and digest(question.material_text, 'sha256') = group_key.material_key
                  join release_binding binding
                    on binding.question_id = question.question_id
                  left join product.question_content_versions content
                    on content.question_id = question.question_id
                   and content.content_version_id = binding.content_version_id
                   and content.track = 'xingce'
                  where occurrence.number is not null
                    and not (question.question_id = any(cast(:excluded_question_ids as text[])))
                    and (
                      cast(:user_id as text) is null
                      or (
                        not exists (
                          select 1 from learning.xingce_assessments assessment
                          where assessment.user_id = cast(:user_id as uuid)
                            and assessment.question_id = question.question_id
                        )
                        and not exists (
                          select 1 from learning.xingce_question_exposures exposure
                          where exposure.user_id = cast(:user_id as uuid)
                            and exposure.question_id = question.question_id
                        )
                      )
                    )
                  group by
                    group_key.paper_key,
                    group_key.material_key,
                    group_key.seed_rank,
                    question.question_id,
                    binding.content_version_id,
                    binding.shared_context_required,
                    binding.visual_required,
                    binding.latest_year,
                    binding.occurrence_count,
                    binding.binding_unambiguous,
                    content.snapshot_payload
                ),
                valid_groups as (
                  select paper_key, material_key, min(seed_rank) as seed_rank
                  from group_members
                  group by paper_key, material_key
                  having count(*) = 5
                     and count(distinct question_number) = 5
                     and max(question_number) - min(question_number) = 4
                     and bool_and(binding_unambiguous)
                     and bool_and(shared_context_required)
                     and bool_and(snapshot_payload is not null)
                     and bool_and(
                       nullif(btrim(coalesce(snapshot_payload ->> 'material_text', '')), '')
                       is not null
                     )
                )
                select
                  valid_group.seed_rank,
                  member.paper_key,
                  member.material_key,
                  member.question_number,
                  member.question_id,
                  member.content_version_id,
                  member.visual_required,
                  member.shared_context_required,
                  member.latest_year,
                  member.occurrence_count,
                  member.snapshot_payload
                from valid_groups valid_group
                join group_members member
                  on member.paper_key = valid_group.paper_key
                 and member.material_key = valid_group.material_key
                order by
                  valid_group.seed_rank,
                  member.paper_key,
                  member.question_number,
                  member.question_id
                """
            ),
            {
                "release_id": release_id,
                "seed_question_ids": seed_ids,
                "user_id": user_id,
                "excluded_question_ids": excluded_question_ids or [],
            },
        ).mappings().all()
        groups: dict[tuple[str, bytes], list[dict[str, Any]]] = {}
        group_order: list[tuple[str, bytes]] = []
        for row in rows:
            key = (str(row["paper_key"]), bytes(row["material_key"]))
            if key not in groups:
                groups[key] = []
                group_order.append(key)
            question = dict(row["snapshot_payload"])
            question["question_id"] = str(row["question_id"])
            question["visual_required"] = bool(row["visual_required"])
            question["shared_context_required"] = bool(row["shared_context_required"])
            question["_content_version_id"] = str(row["content_version_id"])
            try:
                validate_learner_question(question)
            except TaskContentContractError:
                groups[key] = []
                continue
            groups[key].append(question)

        selected: list[dict[str, Any]] = []
        for key in group_order:
            group = groups[key]
            if len(group) != _DATA_ANALYSIS_GROUP_SIZE:
                continue
            if len(selected) + _DATA_ANALYSIS_GROUP_SIZE > limit:
                break
            selected.extend(group)
            if len(selected) == limit:
                break
        return selected
