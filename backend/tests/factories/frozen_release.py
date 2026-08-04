from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.external_ingestion.matching.catalog import (
    SQLQuestionContentCatalog,
)
from app.domains.knowledge.module_vocabulary import (
    load_current_module_vocabulary,
)
from app.domains.xingce_learning.real_attempt.repository import (
    XingceAttemptRepository,
)
from tests.knowledge_map_vocabulary_helpers import (
    activate_knowledge_release,
    bind_question_to_release_node,
    publish_legacy_question_content,
    seed_released_module_vocabulary,
)


_VOCAB_VERSION = "vocab@canonical-frozen-release-test-v1"
_DEFAULT_NODE_ID = "canonical-verbal-practice"
_DEFAULT_CANONICAL_MODULE = "言语理解与表达"
_DEFAULT_RAW_MODULE = "言语理解"
_OPTION_LABELS = ("A", "B", "C", "D")


@dataclass(frozen=True, slots=True)
class FrozenKnowledgeNodeSeed:
    node_id: str = _DEFAULT_NODE_ID
    module_name: str = _DEFAULT_CANONICAL_MODULE
    node_type: str = "knowledge_point"
    tag_type: str = "knowledge_point"
    tag_value: str = "言语理解基础"
    near5_weight: float = 1.0
    near5_occurrence_count: int = 100


@dataclass(frozen=True, slots=True)
class FrozenQuestionSeed:
    question_id: str
    stem: str
    answer: str
    node_id: str = _DEFAULT_NODE_ID
    module_name: str = _DEFAULT_RAW_MODULE
    question_type: str = "single_choice"
    option_labels: tuple[str, ...] = _OPTION_LABELS
    material_text: str | None = None
    shared_context_required: bool = False
    visual_required: bool = False
    image_src: str | None = None
    occurrence_count: int = 1
    latest_year: int | None = 2026
    explanation: str | None = None


@dataclass(frozen=True, slots=True)
class CanonicalFrozenRelease:
    release_id: str
    release_version: str
    source_data_version: str
    vocab_version: str
    source_fingerprint: str
    question_ids: tuple[str, ...]
    content_version_ids: tuple[str, ...]
    reference_time: datetime


class NegativeFrozenReleaseScenario(StrEnum):
    MISSING = "missing"
    STALE = "stale"


def seed_canonical_frozen_release(
    session: Session,
    *,
    release_key: str,
    questions: tuple[FrozenQuestionSeed, ...],
    nodes: tuple[FrozenKnowledgeNodeSeed, ...] = (FrozenKnowledgeNodeSeed(),),
    reference_time: datetime | None = None,
    legacy_visual_schema: bool = False,
) -> CanonicalFrozenRelease:
    now = reference_time or datetime.now(UTC)
    release = _seed_frozen_release_storage(
        session,
        release_key=release_key,
        questions=questions,
        nodes=nodes,
        reference_time=now,
        fresh_until=now + timedelta(days=365),
        legacy_visual_schema=legacy_visual_schema,
    )
    if not legacy_visual_schema:
        assert_canonical_frozen_release(session, release)
    return release


def seed_negative_frozen_release(
    session: Session,
    *,
    scenario: NegativeFrozenReleaseScenario,
    release_key: str,
    questions: tuple[FrozenQuestionSeed, ...],
    nodes: tuple[FrozenKnowledgeNodeSeed, ...] = (FrozenKnowledgeNodeSeed(),),
    reference_time: datetime | None = None,
) -> CanonicalFrozenRelease | None:
    now = reference_time or datetime.now(UTC)
    if scenario is NegativeFrozenReleaseScenario.MISSING:
        _insert_questions(session, questions)
        _assert_no_current_release(session, reference_time=now)
        return None
    if scenario is NegativeFrozenReleaseScenario.STALE:
        release = _seed_frozen_release_storage(
            session,
            release_key=release_key,
            questions=questions,
            nodes=nodes,
            reference_time=now,
            fresh_until=now - timedelta(minutes=1),
            activated_at=now - timedelta(minutes=2),
        )
        _assert_stored_release_contract(session, release)
        _assert_no_current_release(session, reference_time=now)
        return release
    raise AssertionError(f"unsupported negative frozen-release scenario: {scenario}")


def seed_planning_shared_frozen_release(
    session: Session,
    *,
    count: int = 6,
    canonical_module: str = _DEFAULT_CANONICAL_MODULE,
    raw_module: str = _DEFAULT_RAW_MODULE,
) -> CanonicalFrozenRelease:
    node_id = f"canonical-{sha256(canonical_module.encode('utf-8')).hexdigest()[:12]}-practice"
    questions = tuple(
        FrozenQuestionSeed(
            question_id=f"planning-shared-question-{index}",
            stem=f"计划供给验收题 {index}",
            answer=_OPTION_LABELS[(index - 1) % len(_OPTION_LABELS)],
            node_id=node_id,
            module_name=raw_module,
            occurrence_count=100 - index,
            latest_year=2025,
        )
        for index in range(1, count + 1)
    )
    return seed_canonical_frozen_release(
        session,
        release_key="planning-shared",
        questions=questions,
        nodes=(
            FrozenKnowledgeNodeSeed(
                node_id=node_id,
                module_name=canonical_module,
                tag_value=f"{canonical_module}基础",
            ),
        ),
    )


def seed_route_frozen_release(
    factory: sessionmaker[Session],
    *,
    count: int = 3,
) -> CanonicalFrozenRelease:
    questions = tuple(
        FrozenQuestionSeed(
            question_id=f"xingce-route-question-{index}",
            stem=f"行测路由验收题 {index}",
            answer=_OPTION_LABELS[(index - 1) % len(_OPTION_LABELS)],
            material_text="路由验收共用材料" if index == 1 else None,
            shared_context_required=index == 1,
            occurrence_count=100 - index,
            explanation=f"解析 {index}",
        )
        for index in range(1, count + 1)
    )
    with factory.begin() as session:
        return seed_canonical_frozen_release(
            session,
            release_key="xingce-route",
            questions=questions,
        )


def assert_canonical_frozen_release(
    session: Session,
    release: CanonicalFrozenRelease,
) -> None:
    vocabulary = load_current_module_vocabulary(
        session,
        exam_scope="guokao",
        track="xingce",
        now=release.reference_time,
    )
    if vocabulary is None or vocabulary.release_id != release.release_id:
        raise AssertionError("canonical frozen release is not the sole fresh authority")
    if vocabulary.canonicalize(_DEFAULT_RAW_MODULE) != _DEFAULT_CANONICAL_MODULE:
        raise AssertionError("canonical frozen release vocabulary is incomplete")
    _assert_stored_release_contract(session, release)

    selected = XingceAttemptRepository().select_frozen_questions(
        session,
        module_name=None,
        limit=len(release.question_ids),
    )
    selected_ids = tuple(str(item["question_id"]) for item in selected)
    if len(selected_ids) != len(release.question_ids) or set(selected_ids) != set(
        release.question_ids
    ):
        raise AssertionError(
            "canonical frozen release is not delivery-eligible through production selection"
        )


def _seed_frozen_release_storage(
    session: Session,
    *,
    release_key: str,
    questions: tuple[FrozenQuestionSeed, ...],
    nodes: tuple[FrozenKnowledgeNodeSeed, ...],
    reference_time: datetime,
    fresh_until: datetime,
    activated_at: datetime | None = None,
    legacy_visual_schema: bool = False,
) -> CanonicalFrozenRelease:
    _validate_seed(release_key=release_key, questions=questions, nodes=nodes)
    release_id = str(uuid5(NAMESPACE_URL, f"gongkao-test-frozen-release:{release_key}"))
    release_version = f"knowledge-map@test:{release_key}:v1"
    source_data_version = f"question-bank@test:{release_key}:v1"
    source_fingerprint = _fixture_fingerprint(
        release_key=release_key,
        questions=questions,
        nodes=nodes,
    )
    existing = session.execute(
        text(
            """
            select status
            from product.knowledge_map_releases
            where release_id = cast(:release_id as uuid)
            """
        ),
        {"release_id": release_id},
    ).scalar_one_or_none()
    if existing is not None:
        release = _load_existing_release(
            session,
            release_id=release_id,
            reference_time=reference_time,
        )
        if (
            release.release_version != release_version
            or release.source_data_version != source_data_version
            or release.source_fingerprint != source_fingerprint
            or release.question_ids != tuple(item.question_id for item in questions)
        ):
            raise AssertionError("same-key frozen-release replay changed fixture semantics")
        return release

    seed_released_module_vocabulary(
        session,
        version=_VOCAB_VERSION,
        aliases={_DEFAULT_RAW_MODULE: "verbal_comprehension"},
    )
    generated_at = reference_time - timedelta(days=1)
    session.execute(
        text(
            """
            insert into product.knowledge_map_releases (
              release_id, release_version, exam_scope, track,
              source_data_version, vocab_version, generator_version,
              source_fingerprint, generated_at, fresh_until, status,
              node_count, activated_at
            ) values (
              cast(:release_id as uuid), :release_version, 'guokao', 'xingce',
              :source_data_version, :vocab_version,
              'canonical_frozen_release_test_factory@v1',
              :source_fingerprint, :generated_at, :fresh_until, 'draft', 0, null
            )
            """
        ),
        {
            "release_id": release_id,
            "release_version": release_version,
            "source_data_version": source_data_version,
            "vocab_version": _VOCAB_VERSION,
            "source_fingerprint": source_fingerprint,
            "generated_at": generated_at,
            "fresh_until": fresh_until,
        },
    )
    for node in nodes:
        _insert_node(session, release_id=release_id, node=node)
    _insert_questions(session, questions)
    content_version_ids: list[str] = []
    for question in questions:
        legacy_content_version_id = (
            publish_legacy_question_content(session, question_id=question.question_id)
            if legacy_visual_schema
            else None
        )
        content_version_ids.append(
            bind_question_to_release_node(
                session,
                release_id=release_id,
                node_id=question.node_id,
                question_id=question.question_id,
                latest_recent_exam_year=question.latest_year,
                recent_occurrence_count=max(question.occurrence_count, 1),
                content_version_id=legacy_content_version_id,
            )
        )
    activate_knowledge_release(
        session,
        release_id=release_id,
        activated_at=activated_at or reference_time,
    )
    return CanonicalFrozenRelease(
        release_id=release_id,
        release_version=release_version,
        source_data_version=source_data_version,
        vocab_version=_VOCAB_VERSION,
        source_fingerprint=source_fingerprint,
        question_ids=tuple(item.question_id for item in questions),
        content_version_ids=tuple(content_version_ids),
        reference_time=reference_time,
    )


def _insert_node(
    session: Session,
    *,
    release_id: str,
    node: FrozenKnowledgeNodeSeed,
) -> None:
    session.execute(
        text(
            """
            insert into product.knowledge_map_nodes (
              release_id, node_id, exam_scope, track, module_name,
              node_type, tag_type, tag_value, display_path,
              support_status, near5_window_start_year,
              near5_window_end_year, near5_weight,
              near5_occurrence_count, stable_for_old_questions,
              status
            ) values (
              cast(:release_id as uuid), :node_id, 'guokao', 'xingce',
              :module_name, :node_type, :tag_type, :tag_value,
              :display_path, 'text_supported', 2022, 2026, :near5_weight,
              :near5_occurrence_count, true, 'published'
            )
            """
        ),
        {
            "release_id": release_id,
            "node_id": node.node_id,
            "module_name": node.module_name,
            "node_type": node.node_type,
            "tag_type": node.tag_type,
            "tag_value": node.tag_value,
            "display_path": f"{node.module_name}/{node.tag_value}",
            "near5_weight": node.near5_weight,
            "near5_occurrence_count": node.near5_occurrence_count,
        },
    )


def _insert_questions(
    session: Session,
    questions: tuple[FrozenQuestionSeed, ...],
) -> None:
    for question in questions:
        session.execute(
            text(
                """
                insert into product.questions (
                  question_id, domain, status, module_name, question_type,
                  stem, answer, material_text, shared_context_required,
                  visual_required, occurrence_count, latest_year
                ) values (
                  :question_id, 'objective', 'canonical', :module_name,
                  :question_type, :stem, :answer, :material_text,
                  :shared_context_required, :visual_required,
                  :occurrence_count, :latest_year
                )
                on conflict (question_id) do nothing
                """
            ),
            {
                "question_id": question.question_id,
                "module_name": question.module_name,
                "question_type": question.question_type,
                "stem": question.stem,
                "answer": question.answer,
                "material_text": question.material_text,
                "shared_context_required": question.shared_context_required,
                "visual_required": question.visual_required,
                "occurrence_count": question.occurrence_count,
                "latest_year": question.latest_year,
            },
        )
        for option_index, label in enumerate(question.option_labels, start=1):
            session.execute(
                text(
                    """
                    insert into product.question_options (
                      question_id, option_index, label, option_text
                    ) values (
                      :question_id, :option_index, :label, :option_text
                    )
                    on conflict (question_id, option_index) do nothing
                    """
                ),
                {
                    "question_id": question.question_id,
                    "option_index": option_index,
                    "label": label,
                    "option_text": f"选项 {label}",
                },
            )
        if question.explanation is not None:
            session.execute(
                text(
                    """
                    insert into product.question_explanations (
                      question_id, preferred_analysis, analysis_source,
                      analysis_status
                    ) values (
                      :question_id, :analysis, 'own_bank', 'verified'
                    )
                    on conflict (question_id) do nothing
                    """
                ),
                {
                    "question_id": question.question_id,
                    "analysis": question.explanation,
                },
            )
        if question.visual_required and question.image_src is not None:
            session.execute(
                text(
                    """
                    insert into product.question_assets (
                      asset_id, question_id, part_kind, part_index,
                      image_ordinal, image_src, object_key,
                      is_representative_asset
                    ) values (
                      :asset_id, :question_id, 'stem', 0, 0,
                      :image_src, :object_key, true
                    )
                    on conflict (asset_id) do nothing
                    """
                ),
                {
                    "asset_id": f"asset-{question.question_id}",
                    "question_id": question.question_id,
                    "image_src": question.image_src,
                    "object_key": f"questions/{question.question_id}.png",
                },
            )
        _assert_question_matches_seed(session, question)


def _assert_question_matches_seed(
    session: Session,
    question: FrozenQuestionSeed,
) -> None:
    row = (
        session.execute(
            text(
                """
                select module_name, question_type, stem, answer, material_text,
                       shared_context_required, visual_required,
                       occurrence_count, latest_year
                from product.questions
                where question_id = :question_id
                """
            ),
            {"question_id": question.question_id},
        )
        .mappings()
        .one()
    )
    expected = {
        "module_name": question.module_name,
        "question_type": question.question_type,
        "stem": question.stem,
        "answer": question.answer,
        "material_text": question.material_text,
        "shared_context_required": question.shared_context_required,
        "visual_required": question.visual_required,
        "occurrence_count": question.occurrence_count,
        "latest_year": question.latest_year,
    }
    if dict(row) != expected:
        raise AssertionError("same-id question replay changed fixture semantics")


def _assert_stored_release_contract(
    session: Session,
    release: CanonicalFrozenRelease,
) -> None:
    row = (
        session.execute(
            text(
                """
                select
                  release_version, source_data_version, vocab_version,
                  source_fingerprint, status, question_binding_count,
                  question_binding_fingerprint
                from product.knowledge_map_releases
                where release_id = cast(:release_id as uuid)
                """
            ),
            {"release_id": release.release_id},
        )
        .mappings()
        .one()
    )
    manifest = (
        session.execute(
            text(
                """
                select binding_count, binding_fingerprint
                from product.knowledge_map_question_binding_manifest(
                  cast(:release_id as uuid)
                )
                """
            ),
            {"release_id": release.release_id},
        )
        .mappings()
        .one()
    )
    if (
        str(row["release_version"]) != release.release_version
        or str(row["source_data_version"]) != release.source_data_version
        or str(row["vocab_version"]) != release.vocab_version
        or str(row["source_fingerprint"]) != release.source_fingerprint
        or str(row["status"]) != "active"
        or int(row["question_binding_count"]) != len(release.question_ids)
        or int(manifest["binding_count"]) != len(release.question_ids)
        or str(row["question_binding_fingerprint"]) != str(manifest["binding_fingerprint"])
    ):
        raise AssertionError("frozen-release manifest does not match stored authority")

    bindings = list(
        session.execute(
            text(
                """
                select
                  binding.question_id,
                  binding.content_version_id,
                  binding.delivery_eligible,
                  content.track
                from product.knowledge_map_question_bindings binding
                join product.question_content_versions content
                  on content.content_version_id = binding.content_version_id
                 and content.question_id = binding.question_id
                where binding.release_id = cast(:release_id as uuid)
                order by binding.question_id
                """
            ),
            {"release_id": release.release_id},
        ).mappings()
    )
    by_question_id = {str(item["question_id"]): item for item in bindings}
    catalog = SQLQuestionContentCatalog(session)
    for question_id, content_version_id in zip(
        release.question_ids,
        release.content_version_ids,
        strict=True,
    ):
        binding = by_question_id.get(question_id)
        content = catalog.get(content_version_id=content_version_id)
        if (
            binding is None
            or str(binding["content_version_id"]) != content_version_id
            or binding["delivery_eligible"] is not True
            or str(binding["track"]) != "xingce"
            or content is None
            or content.content_version_id != content_version_id
            or not content.prompt_text.strip()
        ):
            raise AssertionError("frozen question binding or immutable snapshot is incomplete")


def _load_existing_release(
    session: Session,
    *,
    release_id: str,
    reference_time: datetime,
) -> CanonicalFrozenRelease:
    release = (
        session.execute(
            text(
                """
                select
                  release_version, source_data_version, vocab_version,
                  source_fingerprint
                from product.knowledge_map_releases
                where release_id = cast(:release_id as uuid)
                """
            ),
            {"release_id": release_id},
        )
        .mappings()
        .one()
    )
    bindings = list(
        session.execute(
            text(
                """
                select question_id, content_version_id
                from product.knowledge_map_question_bindings
                where release_id = cast(:release_id as uuid)
                order by latest_year desc nulls last,
                         occurrence_count desc,
                         question_id
                """
            ),
            {"release_id": release_id},
        ).mappings()
    )
    return CanonicalFrozenRelease(
        release_id=release_id,
        release_version=str(release["release_version"]),
        source_data_version=str(release["source_data_version"]),
        vocab_version=str(release["vocab_version"]),
        source_fingerprint=str(release["source_fingerprint"]),
        question_ids=tuple(str(item["question_id"]) for item in bindings),
        content_version_ids=tuple(str(item["content_version_id"]) for item in bindings),
        reference_time=reference_time,
    )


def _assert_no_current_release(
    session: Session,
    *,
    reference_time: datetime,
) -> None:
    if (
        load_current_module_vocabulary(
            session,
            exam_scope="guokao",
            track="xingce",
            now=reference_time,
        )
        is not None
    ):
        raise AssertionError("negative frozen-release fixture unexpectedly became active")


def _validate_seed(
    *,
    release_key: str,
    questions: tuple[FrozenQuestionSeed, ...],
    nodes: tuple[FrozenKnowledgeNodeSeed, ...],
) -> None:
    if not release_key.strip() or not questions or not nodes:
        raise AssertionError("frozen-release fixture requires key, questions, and nodes")
    question_ids = tuple(item.question_id for item in questions)
    node_ids = tuple(item.node_id for item in nodes)
    if (
        len(question_ids) != len(set(question_ids))
        or len(node_ids) != len(set(node_ids))
        or any(item.node_id not in node_ids for item in questions)
        or any(item.answer not in item.option_labels for item in questions)
    ):
        raise AssertionError("frozen-release fixture contains ambiguous seed authority")


def _fixture_fingerprint(
    *,
    release_key: str,
    questions: tuple[FrozenQuestionSeed, ...],
    nodes: tuple[FrozenKnowledgeNodeSeed, ...],
) -> str:
    payload = {
        "contractVersion": "canonical-frozen-release-test-factory@v1",
        "releaseKey": release_key,
        "questions": [asdict(item) for item in questions],
        "nodes": [asdict(item) for item in nodes],
    }
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
