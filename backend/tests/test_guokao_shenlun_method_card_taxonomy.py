from __future__ import annotations

import re
from typing import get_args

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.guokao_shenlun import (
    GUOKAO_SHENLUN_APPLICABILITY_TAXONOMY_VERSION,
    GUOKAO_SHENLUN_GENERAL_METHOD_VALIDATION_POLICY_VERSION,
    GUOKAO_SHENLUN_QUESTION_TYPES,
    guokao_shenlun_category,
    guokao_shenlun_category_from_catalog_text,
)
from app.domains.community_method_cards.composition import (
    SqlValidationBindingAuthority,
)
from app.domains.community_method_cards.schemas import DraftCreate, Track
from app.domains.community_method_cards.service import CommunityMethodCardError
from app.domains.essay_intelligence.schemas import QuestionType


def test_shenlun_taxonomy_matches_the_public_model_and_database_constraint(
    db_session: Session,
) -> None:
    assert set(get_args(QuestionType)) == set(GUOKAO_SHENLUN_QUESTION_TYPES)
    definitions = db_session.execute(
        text(
            """
            select pg_get_constraintdef(constraint_row.oid)
            from pg_constraint constraint_row
            where constraint_row.conrelid =
                  'product.essay_task_versions'::regclass
              and constraint_row.contype = 'c'
            """
        )
    ).scalars()
    question_type_definition = next(
        definition for definition in definitions if "question_type" in definition
    )
    assert set(re.findall(r"'([^']+)'::text", question_type_definition)) == set(
        GUOKAO_SHENLUN_QUESTION_TYPES
    )


def test_shenlun_taxonomy_reuses_existing_catalog_semantics() -> None:
    proposal = guokao_shenlun_category("proposal")
    composition = guokao_shenlun_category("大作文")
    implementation = guokao_shenlun_category_from_catalog_text("申论 事务性文书")

    assert proposal is not None and proposal.code == "countermeasure"
    assert composition is not None and composition.code == "composition"
    assert implementation is not None and implementation.code == "implementation"


def test_essay_general_method_binds_one_versioned_taxonomy_category() -> None:
    binding = SqlValidationBindingAuthority._essay_general_method_binding(
        ["归纳概括", "概括题"]
    )

    assert binding.validation_scope == "general_method"
    assert binding.knowledge_node_version_ref == (
        f"{GUOKAO_SHENLUN_APPLICABILITY_TAXONOMY_VERSION}/summary"
    )
    assert (
        binding.validation_policy_version
        == GUOKAO_SHENLUN_GENERAL_METHOD_VALIDATION_POLICY_VERSION
    )
    assert binding.canonical_task_version_ref is None
    assert binding.answer_evidence_refs == ()
    assert binding.general_method_outcome == "needs_revision"


@pytest.mark.parametrize(
    "values",
    [
        [],
        ["未知申论题型"],
        ["归纳概括", "综合分析"],
        ["归纳概括", "未知申论题型"],
    ],
)
def test_essay_general_method_fails_closed_for_missing_or_ambiguous_taxonomy(
    values: list[str],
) -> None:
    with pytest.raises(CommunityMethodCardError) as captured:
        SqlValidationBindingAuthority._essay_general_method_binding(values)

    assert captured.value.code == "knowledge_binding_ambiguous"


def test_public_draft_contract_keeps_the_essay_track() -> None:
    draft = DraftCreate.model_validate(
        {
            "sourceType": "text",
            "sourceText": "先归纳对象，再按材料层次合并同义要点。",
            "track": "essay",
            "applicability": ["归纳概括"],
            "idempotencyKey": "essay-taxonomy-public-contract",
        }
    )

    assert draft.track == Track.ESSAY
