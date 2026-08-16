from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.domains.teaching_packages import (
    AttachStructuredVisualEvidenceInput,
    CompiledStructuredVisualSidecar,
    CompiledTeachingPackage,
    CompileTeachingPackageInput,
    ExamTargetScope,
    ReusableMethodAsset,
    StructuredVisualAttachReasonCode,
    StructuredVisualEvidenceReader,
    TeachingQuestionCandidate,
    attach_structured_visual_evidence,
    compile_teaching_package,
)

if TYPE_CHECKING:
    from app.domains.xingce_learning.rescue.repository import (
        RescueMethodCardRow,
        RescueNodeLookup,
        RescueRepository,
        RescueSimilarQuestionRow,
        RescueTargetProfile,
    )


class CoachBrickAssemblyReason(StrEnum):
    REVIEW_NOT_FOUND = "REVIEW_NOT_FOUND"
    SOURCE_NOT_IN_REVIEW = "SOURCE_NOT_IN_REVIEW"
    TARGET_SCOPE_UNAVAILABLE = "TARGET_SCOPE_UNAVAILABLE"
    KNOWLEDGE_RELEASE_UNAVAILABLE = "KNOWLEDGE_RELEASE_UNAVAILABLE"
    KNOWLEDGE_NODE_UNAVAILABLE = "KNOWLEDGE_NODE_UNAVAILABLE"
    METHOD_CARD_UNAVAILABLE = "METHOD_CARD_UNAVAILABLE"
    INSUFFICIENT_UNSEEN_QUESTIONS = "INSUFFICIENT_UNSEEN_QUESTIONS"
    TEACHING_PACKAGE_UNAVAILABLE = "TEACHING_PACKAGE_UNAVAILABLE"
    QUESTION_BINDING_UNAVAILABLE = "QUESTION_BINDING_UNAVAILABLE"
    VISUAL_EVIDENCE_UNAVAILABLE = "VISUAL_EVIDENCE_UNAVAILABLE"
    VISUAL_EVIDENCE_UNVERIFIED = "VISUAL_EVIDENCE_UNVERIFIED"
    VISUAL_EVIDENCE_VERSION_MISMATCH = "VISUAL_EVIDENCE_VERSION_MISMATCH"


@dataclass(frozen=True, slots=True)
class CoachBrickAssembly:
    """One immutable readiness result shared by preview and formal start."""

    reason: CoachBrickAssemblyReason | None
    target_scope: ExamTargetScope | None = None
    package: CompiledTeachingPackage | None = None
    visual_sidecar: CompiledStructuredVisualSidecar | None = None
    method: RescueMethodCardRow | None = None

    @property
    def available(self) -> bool:
        return (
            self.reason is None
            and self.target_scope is not None
            and self.package is not None
            and self.visual_sidecar is not None
            and self.method is not None
        )


_PROVINCE_CODES: tuple[tuple[str, str], ...] = (
    ("内蒙古", "inner_mongolia"),
    ("黑龙江", "heilongjiang"),
    ("北京", "beijing"),
    ("天津", "tianjin"),
    ("上海", "shanghai"),
    ("重庆", "chongqing"),
    ("河北", "hebei"),
    ("山西", "shanxi"),
    ("辽宁", "liaoning"),
    ("吉林", "jilin"),
    ("江苏", "jiangsu"),
    ("浙江", "zhejiang"),
    ("安徽", "anhui"),
    ("福建", "fujian"),
    ("江西", "jiangxi"),
    ("山东", "shandong"),
    ("河南", "henan"),
    ("湖北", "hubei"),
    ("湖南", "hunan"),
    ("广东", "guangdong"),
    ("广西", "guangxi"),
    ("海南", "hainan"),
    ("四川", "sichuan"),
    ("贵州", "guizhou"),
    ("云南", "yunnan"),
    ("陕西", "shaanxi"),
    ("甘肃", "gansu"),
    ("青海", "qinghai"),
    ("宁夏", "ningxia"),
    ("新疆", "xinjiang"),
    ("西藏", "tibet"),
    ("台湾", "taiwan"),
    ("香港", "hong_kong"),
    ("澳门", "macau"),
)
_SCOPE_TOKEN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class CoachTeachingBrickAssembler:
    """Compile all read-only method/question/evidence bricks exactly once."""

    def __init__(
        self,
        *,
        rescue_repository: type[RescueRepository] | None = None,
        visual_reader: StructuredVisualEvidenceReader | None = None,
    ) -> None:
        if rescue_repository is None:
            from app.domains.xingce_learning.rescue.repository import RescueRepository

            rescue_repository = RescueRepository
        self._rescue_repository = rescue_repository
        self._visual_reader = visual_reader or StructuredVisualEvidenceReader()

    def assemble(
        self,
        session: Session,
        *,
        user_id: str,
        review_id: str,
        source_question_id: str,
        now: datetime | None = None,
    ) -> CoachBrickAssembly:
        recorded_at = now or datetime.now(UTC)
        repository = self._rescue_repository(session)
        review = repository.current_review(user_id=user_id, review_id=review_id)
        if review is None:
            return _unavailable(CoachBrickAssemblyReason.REVIEW_NOT_FOUND)

        wrong_ids = list(
            repository.wrong_question_ids_for_review(
                user_id=user_id,
                session_id=str(review["session_id"]),
            )
        )
        if source_question_id not in wrong_ids:
            return _unavailable(CoachBrickAssemblyReason.SOURCE_NOT_IN_REVIEW)

        release = repository.resolve_fresh_release()
        if release is None:
            return _unavailable(CoachBrickAssemblyReason.KNOWLEDGE_RELEASE_UNAVAILABLE)
        profile = repository.target_profile(user_id=user_id)
        target_scope = project_exam_target_scope(profile, year=recorded_at.year)
        if target_scope is None and (profile is None or not (profile.target_exam or "").strip()):
            # A new learner may not have chosen an exam yet. The task itself is
            # already bound to one active release, so that release is a truthful
            # temporary target for this review instead of another onboarding gate.
            target_scope = _release_scope(release, year=recorded_at.year)
        if target_scope is None:
            return _unavailable(CoachBrickAssemblyReason.TARGET_SCOPE_UNAVAILABLE)
        nodes = repository.knowledge_nodes_for_questions(
            question_ids=[source_question_id],
            release=release,
        )
        node = nodes.get(source_question_id)
        if node is None:
            return _unavailable(CoachBrickAssemblyReason.KNOWLEDGE_NODE_UNAVAILABLE)
        if not node.method_card_ids:
            return _unavailable(CoachBrickAssemblyReason.METHOD_CARD_UNAVAILABLE)

        cards = repository.method_cards_by_ids(list(node.method_card_ids))
        method = cards.get(node.method_card_ids[0])
        if method is None:
            return _unavailable(CoachBrickAssemblyReason.METHOD_CARD_UNAVAILABLE)

        questions = repository.select_similar_question(
            user_id=user_id,
            node_id=node.node_id,
            module_name=node.module_name,
            release=release,
            excluded_question_ids=wrong_ids,
            limit=12,
        )
        if len(questions) < 2:
            return _unavailable(CoachBrickAssemblyReason.INSUFFICIENT_UNSEEN_QUESTIONS)

        source_scope_key = _release_source_scope_key(release, year=recorded_at.year)
        compiled = compile_teaching_package(
            CompileTeachingPackageInput(
                targetScopeKey=target_scope.scope_key,
                microSkillId=node.node_id,
                sourceQuestionRef=source_question_id,
                methodAsset=_method_asset(method, node),
                questionCandidates=tuple(
                    _question_candidate(
                        row,
                        node,
                        source_scope_key=source_scope_key,
                    )
                    for row in questions
                ),
            )
        )
        if compiled.state != "teachable":
            return _unavailable(CoachBrickAssemblyReason.TEACHING_PACKAGE_UNAVAILABLE)
        package = compiled.package_candidates[0]

        by_id = {row.question_id: row for row in questions}
        selected = (
            by_id[package.student_question_ref],
            by_id[package.user_transfer_question_ref],
        )
        bindings = tuple(
            self._visual_reader.binding_for_question(
                session,
                question_ref=row.question_id,
                visual_required=row.visual_required,
            )
            for row in selected
        )
        if any(binding is None for binding in bindings):
            return _unavailable(CoachBrickAssemblyReason.QUESTION_BINDING_UNAVAILABLE)

        attached = attach_structured_visual_evidence(
            AttachStructuredVisualEvidenceInput(
                package=package,
                bindings=bindings,  # type: ignore[arg-type]
            )
        )
        if attached.state != "attachable":
            return _unavailable(_visual_reason(attached.reason_codes))
        return CoachBrickAssembly(
            reason=None,
            target_scope=target_scope,
            package=package,
            visual_sidecar=attached.package_candidates[0],
            method=method,
        )


def project_exam_target_scope(
    profile: RescueTargetProfile | Any | None,
    *,
    year: int,
) -> ExamTargetScope | None:
    """Project explicit profile facts without silently pretending unknown is Guokao."""

    if profile is None:
        return None
    exam_code = _exam_code(profile.target_exam)
    if exam_code is None:
        return None
    return ExamTargetScope(
        examCode=exam_code,
        regionCode=_region_code(exam_code, profile.target_region),
        year=year,
        categoryCode=_category_code(exam_code, profile.position_group),
        subjectCode="zhice" if exam_code == "sydw" else "xingce",
    )


def assembly_reason_message(reason: CoachBrickAssemblyReason | None) -> str | None:
    if reason is None:
        return None
    if reason in {
        CoachBrickAssemblyReason.VISUAL_EVIDENCE_UNAVAILABLE,
        CoachBrickAssemblyReason.VISUAL_EVIDENCE_UNVERIFIED,
        CoachBrickAssemblyReason.VISUAL_EVIDENCE_VERSION_MISMATCH,
    }:
        return "这类图片题先用原图和官方解析练习，可靠图片证据齐了再开放讲给岸岸。"
    if reason == CoachBrickAssemblyReason.TARGET_SCOPE_UNAVAILABLE:
        return "请先确认当前备考类型，岸岸才能选对迁移题。"
    if reason in {
        CoachBrickAssemblyReason.INSUFFICIENT_UNSEEN_QUESTIONS,
        CoachBrickAssemblyReason.TEACHING_PACKAGE_UNAVAILABLE,
    }:
        return "这个考点还缺两道完整的未见迁移题，先看方法卡和原解析。"
    return "这个考点的教学积木还没齐，先使用原题、方法卡和官方解析。"


def _unavailable(reason: CoachBrickAssemblyReason) -> CoachBrickAssembly:
    return CoachBrickAssembly(reason=reason)


def _exam_code(value: str | None) -> str | None:
    normalized = (value or "").strip().casefold().replace("-", "_")
    if not normalized:
        return None
    if normalized in {"sydw", "shiye", "shiye_danwei"} or any(
        marker in normalized for marker in ("事业单位", "事业编", "职测")
    ):
        return "sydw"
    if normalized in {"shengkao", "provincial"} or "省考" in normalized:
        return "shengkao"
    if normalized in {"guokao", "national"} or any(
        marker in normalized for marker in ("国考", "国家公务员")
    ):
        return "guokao"
    return None


def _region_code(exam_code: str, value: str | None) -> str:
    if exam_code == "guokao":
        return "national"
    rendered = (value or "").strip()
    for name, code in _PROVINCE_CODES:
        if name in rendered:
            return code
    normalized = rendered.casefold().replace(" ", "_").replace("-", "_")
    if _SCOPE_TOKEN.fullmatch(normalized):
        return normalized
    return "unspecified"


def _category_code(exam_code: str, value: str | None) -> str:
    rendered = (value or "").strip().casefold()
    markers = (
        ("行政执法", "administrative_enforcement"),
        ("副省", "subprovincial"),
        ("地市", "prefectural"),
        ("市地", "prefectural"),
        ("县乡", "county_township"),
        ("乡镇", "township"),
    )
    for marker, code in markers:
        if marker in rendered:
            return code
    if exam_code == "sydw":
        match = re.search(r"(?:职测|综合应用能力)?\s*([abcde])\s*类", rendered)
        if match:
            return f"category_{match.group(1)}"
    return "general"


def _release_source_scope_key(release: dict[str, object], *, year: int) -> str:
    return _release_scope(release, year=year).scope_key


def _release_scope(release: dict[str, object], *, year: int) -> ExamTargetScope:
    exam_scope = str(release.get("exam_scope") or "guokao")
    track = str(release.get("track") or "xingce")
    generated_at = release.get("generated_at")
    source_year = generated_at.year if isinstance(generated_at, datetime) else year
    scope = ExamTargetScope(
        examCode=exam_scope,
        regionCode="national" if exam_scope == "guokao" else "unspecified",
        year=source_year,
        categoryCode="general",
        subjectCode="zhice" if track == "zhice" else "xingce",
    )
    return scope


def _method_asset(
    method: RescueMethodCardRow | Any,
    node: RescueNodeLookup | Any,
) -> ReusableMethodAsset:
    return ReusableMethodAsset(
        methodCardRef=method.card_id,
        excludedExamCodes=(),
        microSkillId=node.node_id,
        title=method.title,
        recognition=method.recognition,
        methodSteps=method.method_steps,
        applicability=(node.display_path,),
        exclusions=(),
        pitfalls=((method.pitfall,) if method.pitfall else ()),
    )


def _question_candidate(
    row: RescueSimilarQuestionRow | Any,
    node: RescueNodeLookup | Any,
    *,
    source_scope_key: str,
) -> TeachingQuestionCandidate:
    return TeachingQuestionCandidate(
        questionRef=row.question_id,
        sourceScopeKey=source_scope_key,
        microSkillId=node.node_id,
        exposureState="unseen",
        stem=row.stem,
        options=tuple(f"{item['label']}. {item['option_text']}" for item in row.options),
        answerKey=row.correct_option_ids,
        explanation=row.explanation or "",
    )


def _visual_reason(
    reasons: tuple[StructuredVisualAttachReasonCode, ...],
) -> CoachBrickAssemblyReason:
    if StructuredVisualAttachReasonCode.VISUAL_EVIDENCE_UNAVAILABLE in reasons:
        return CoachBrickAssemblyReason.VISUAL_EVIDENCE_UNAVAILABLE
    if StructuredVisualAttachReasonCode.VISUAL_EVIDENCE_UNVERIFIED in reasons:
        return CoachBrickAssemblyReason.VISUAL_EVIDENCE_UNVERIFIED
    return CoachBrickAssemblyReason.VISUAL_EVIDENCE_VERSION_MISMATCH
