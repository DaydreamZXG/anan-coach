from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.onboarding.progress_repository import OnboardingProgressRepository
from app.domains.position_decision.learner_target_score import (
    HistoricalPositionSample,
    LearnerTargetProfile,
    MODEL_VERSION,
    PRIMARY_REFERENCE_YEAR,
    derive_learner_target_score,
)
from app.domains.position_decision.learner_target_schemas import (
    LearnerTargetScoreProjection,
    LearnerTargetScoreRange,
    ReferencePositionProjection,
)
from app.domains.qualification.compiler import certification_labels_from_text
from app.domains.qualification.repository import QualificationRepository


class LearnerTargetScoreRuntime:
    def __init__(
        self,
        *,
        qualification_repository: QualificationRepository | None = None,
        onboarding_repository: OnboardingProgressRepository | None = None,
    ) -> None:
        self._qualification = qualification_repository or QualificationRepository()
        self._onboarding = onboarding_repository or OnboardingProgressRepository()

    def projection(
        self,
        session: Session,
        *,
        user_id: str,
        reference_year: int = PRIMARY_REFERENCE_YEAR,
    ) -> LearnerTargetScoreProjection:
        profile_version = self._qualification.get_profile_version(session, user_id=user_id)
        facts = {
            fact.key: fact.value
            for fact in self._qualification.list_current_facts(session, user_id=user_id)
            if fact.verification_status != "conflicting"
        }
        preference = self._onboarding.current_position_preference(session, user_id=user_id)
        major = _string_fact(facts.get("major"))
        education = _string_fact(facts.get("education"))
        regions = tuple(preference.preferred_regions) if preference is not None else ()
        missing = [
            label
            for label, value in (("major", major), ("education", education), ("preferredRegions", regions))
            if not value
        ]
        if missing:
            return LearnerTargetScoreProjection(
                status="unavailable",
                reason_code="profile_incomplete",
                model_version=MODEL_VERSION,
                profile_version=profile_version,
                reference_year=reference_year,
                target_score=None,
                fallback_level=None,
                sample_count=0,
                reference_positions=[],
                limitations=[f"缺少目标分所需资料：{', '.join(missing)}。"],
            )

        major_family = self._major_family(session, major=major)
        profile = LearnerTargetProfile(
            profile_version=profile_version,
            major=major,
            major_family=major_family,
            education=education,
            preferred_regions=regions,
            gender=_gender_fact(facts.get("gender")),
            political_status=_optional_string_fact(facts.get("political_status")),
            hukou=_optional_string_fact(facts.get("hukou")),
            certifications=_string_list_fact(facts.get("certifications")),
            grassroots_years=_integer_fact(facts.get("grassroots_years")),
        )
        samples = self._historical_samples(
            session,
            reference_year=reference_year,
            profile=profile,
        )
        target = derive_learner_target_score(
            profile=profile,
            samples=samples,
            reference_year=reference_year,
        )
        if target is None:
            return LearnerTargetScoreProjection(
                status="unavailable",
                reason_code="insufficient_historical_samples",
                model_version=MODEL_VERSION,
                profile_version=profile_version,
                reference_year=reference_year,
                target_score=None,
                fallback_level=None,
                sample_count=0,
                reference_positions=[],
                limitations=["符合本人专业和明确资格条件的历史岗位不足 3 个，暂不伪造目标分。"],
            )
        return LearnerTargetScoreProjection(
            status="available",
            reason_code="historical_cohort_ready",
            model_version=target.model_version,
            profile_version=target.profile_version,
            reference_year=target.reference_year,
            target_score=LearnerTargetScoreRange(
                lower=target.lower,
                recommended=target.recommended,
                upper=target.upper,
            ),
            fallback_level=target.fallback_level,
            sample_count=target.sample_count,
            reference_positions=[
                ReferencePositionProjection(
                    position_id=item.position_id,
                    name=item.name,
                    department=item.department,
                    region=item.region,
                    score_year=item.score_year,
                    entry_score=item.entry_score,
                    matched_reasons=list(item.matched_reasons),
                )
                for item in target.reference_positions
            ],
            limitations=list(target.limitations),
            version_id=target.version_id,
        )

    def _major_family(self, session: Session, *, major: str) -> tuple[str, ...]:
        row = session.execute(
            text(
                """
                select category.name as category_name, sub.name as sub_name
                from product.major_catalog major
                join product.major_sub_categories sub on sub.code = major.sub_category_code
                join product.major_categories category on category.code = sub.category_code
                where major.name = :major or major.name = :major_without_suffix
                order by major.code
                limit 1
                """
            ),
            {"major": major, "major_without_suffix": major.removesuffix("专业")},
        ).mappings().one_or_none()
        if row is None:
            return ()
        return tuple(str(row[key]) for key in ("sub_name", "category_name") if row[key])

    def _historical_samples(
        self,
        session: Session,
        *,
        reference_year: int,
        profile: LearnerTargetProfile,
    ) -> list[HistoricalPositionSample]:
        major_terms = tuple(dict.fromkeys((profile.major, *profile.major_family)))
        major_patterns = [f"%{term.removesuffix('专业')}%" for term in major_terms if term]
        rows = session.execute(
            text(
                """
                with raw_scores as (
                  select link.position_id, evidence.year as score_year,
                         evidence.entry_score_min::numeric as entry_score
                  from product.position_entry_score_links link
                  join product.position_entry_score_evidence evidence
                    on evidence.evidence_id = link.evidence_id
                  union all
                  select link.position_id, history.year as score_year,
                         history.entry_score_min::numeric as entry_score
                  from product.position_score_links link
                  join product.position_score_histories history
                    on history.score_history_id = link.score_history_id
                  where history.entry_score_min is not null
                ), score_rows as (
                  select position_id, score_year, avg(entry_score) as entry_score
                  from raw_scores
                  where score_year <= :reference_year
                  group by position_id, score_year
                )
                select position.position_id::text as position_id,
                       position.year as position_year, score.score_year,
                       score.entry_score, position.name, position.department_full,
                       position.region, position.city, position.education_min,
                       position.major_required, position.political_req,
                       position.hukou_req, position.service_min_yrs, position.remark
                from product.positions position
                join score_rows score on score.position_id = position.position_id
                where position.year <= :reference_year
                  and position.major_required like any(cast(:major_patterns as text[]))
                order by score.score_year desc, position.position_id
                limit 5000
                """
            ),
            {"reference_year": reference_year, "major_patterns": major_patterns},
        ).mappings().all()
        return [
            HistoricalPositionSample(
                position_id=str(row["position_id"]),
                position_year=int(row["position_year"]),
                score_year=int(row["score_year"]),
                entry_score=_float(row["entry_score"]),
                name=str(row["name"]),
                department=str(row["department_full"]),
                region=str(row["region"]),
                city=str(row["city"]) if row["city"] else None,
                education=str(row["education_min"]) if row["education_min"] else None,
                major_requirement=(
                    str(row["major_required"]) if row["major_required"] else None
                ),
                political_requirement=(
                    str(row["political_req"]) if row["political_req"] else None
                ),
                hukou_requirement=str(row["hukou_req"]) if row["hukou_req"] else None,
                certifications=_certification_requirements(row["remark"]),
                gender_restriction=_gender_restriction(row["remark"]),
                grassroots_years=int(row["service_min_yrs"] or 0),
            )
            for row in rows
        ]


def _string_fact(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_string_fact(value: Any) -> str | None:
    normalized = _string_fact(value)
    return normalized or None


def _string_list_fact(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _integer_fact(value: Any) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _gender_fact(value: Any) -> str | None:
    return value if value in {"男", "女"} else None


def _gender_restriction(remark: Any) -> str:
    text_value = str(remark or "")
    has_male = "男性" in text_value
    has_female = "女性" in text_value
    if has_male == has_female:
        return "unrestricted"
    return "男" if has_male else "女"


def _certification_requirements(remark: Any) -> tuple[str, ...]:
    return certification_labels_from_text(str(remark or ""))


def _float(value: Any) -> float:
    return float(value) if isinstance(value, Decimal) else float(value)
