from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.domains.external_ingestion.matching.authorization import SQLMatchAuthorization
from app.domains.external_ingestion.matching.catalog import SQLQuestionContentCatalog
from app.domains.external_ingestion.matching.models import MatchingPolicy
from app.domains.external_ingestion.matching.repository import (
    SQLContentMatchRepository,
    SQLRecognitionAuthority,
)
from app.domains.external_ingestion.matching.service import MatchingService


@dataclass(frozen=True)
class MatchingRuntimeConfig:
    policy_version: str
    similar_threshold: float
    ambiguity_margin: float
    text_weight: float
    visual_weight: float
    essay_min_prompt_chars: int
    essay_require_material: bool
    maximum_candidates: int
    sql_prefilter_threshold: float
    sql_prefetch_factor: int
    sql_maximum_prefetch: int

    def policy(self) -> MatchingPolicy:
        return MatchingPolicy(
            policyVersion=self.policy_version,
            similarThreshold=self.similar_threshold,
            ambiguityMargin=self.ambiguity_margin,
            textWeight=self.text_weight,
            visualWeight=self.visual_weight,
            essayMinPromptChars=self.essay_min_prompt_chars,
            essayRequireMaterial=self.essay_require_material,
            maximumCandidates=self.maximum_candidates,
        )


@dataclass(frozen=True)
class MatchingRuntime:
    service: MatchingService
    catalog: SQLQuestionContentCatalog
    authorization: SQLMatchAuthorization


DEFAULT_MATCHING_RUNTIME_CONFIG = MatchingRuntimeConfig(
    policy_version="external-mixed-match@2026-07-15.1",
    similar_threshold=0.82,
    ambiguity_margin=0.025,
    text_weight=0.75,
    visual_weight=0.25,
    essay_min_prompt_chars=40,
    essay_require_material=True,
    maximum_candidates=10,
    sql_prefilter_threshold=0.05,
    sql_prefetch_factor=16,
    sql_maximum_prefetch=500,
)


def build_matching_runtime(
    *,
    session: Session,
    config: MatchingRuntimeConfig = DEFAULT_MATCHING_RUNTIME_CONFIG,
    clock: Callable[[], datetime] | None = None,
) -> MatchingRuntime:
    policy = config.policy()
    catalog = SQLQuestionContentCatalog(
        session,
        clock=clock,
        sql_prefilter_threshold=config.sql_prefilter_threshold,
        prefetch_factor=config.sql_prefetch_factor,
        maximum_prefetch=config.sql_maximum_prefetch,
    )
    authorization = SQLMatchAuthorization(session, clock=clock)
    service = MatchingService(
        repository=SQLContentMatchRepository(session),
        catalog=catalog,
        authorization=authorization,
        recognition_authority=SQLRecognitionAuthority(session),
        policy=policy,
        clock=clock,
    )
    return MatchingRuntime(
        service=service,
        catalog=catalog,
        authorization=authorization,
    )


def build_matching_service(*, session: Session) -> MatchingService:
    """Stable API/workspace composition entry for the approved matching release."""

    return build_matching_runtime(session=session).service
