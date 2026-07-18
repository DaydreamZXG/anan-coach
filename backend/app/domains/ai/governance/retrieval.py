from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.ai.governance.contracts import EvidenceReference, GlobalAskContext
from app.domains.ai.governance.routing import prompt_injection_detected


RETRIEVAL_POLICY_VERSION = "authorized-hybrid-retrieval@v1"


class RetrievalModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class RetrievedSource(RetrievalModel):
    reference: EvidenceReference
    trust_scope: str = Field(alias="trustScope", min_length=1, max_length=80)
    excerpt: str = Field(min_length=1, max_length=2000)
    lexical_score: float = Field(alias="lexicalScore", ge=0, le=1)
    semantic_score: float = Field(alias="semanticScore", ge=0, le=1)
    source_weight: float = Field(alias="sourceWeight", ge=0, le=1)
    final_score: float = Field(alias="finalScore", ge=0, le=1)
    untrusted_input: bool = Field(alias="untrustedInput")


class RetrievalBundle(RetrievalModel):
    bundle_id: UUID = Field(alias="bundleId")
    policy_version: str = Field(default=RETRIEVAL_POLICY_VERSION, alias="policyVersion")
    query_fingerprint: str = Field(alias="queryFingerprint", pattern=r"^[0-9a-f]{64}$")
    sources: tuple[RetrievedSource, ...] = Field(max_length=12)
    blocked_source_refs: tuple[EvidenceReference, ...] = Field(
        default=(), alias="blockedSourceRefs", max_length=50
    )
    security_decision: str = Field(alias="securityDecision")

    @model_validator(mode="after")
    def has_unique_sources(self) -> "RetrievalBundle":
        identities = [item.reference.source_id for item in self.sources]
        if len(identities) != len(set(identities)):
            raise ValueError("retrieval sources must be deduplicated")
        return self

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.model_dump(by_alias=True, mode="json"))


class ObjectAuthorizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Candidate:
    reference: EvidenceReference
    text: str
    trust_scope: str
    source_weight: float
    untrusted_input: bool


class AuthorizedHybridRetriever:
    """Owner-scoped full-text + structured-semantic retrieval with MMR deduplication.

    Canonical content remains in its owning domain. This component reads current
    immutable versions and emits a disposable retrieval bundle; it never creates a
    second source of truth or lets retrieved text alter system permissions.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def retrieve(
        self,
        *,
        user_id: str,
        question: str,
        context: GlobalAskContext,
        context_sources: tuple[_Candidate, ...] = (),
        maximum_sources: int = 8,
    ) -> RetrievalBundle:
        if not 1 <= maximum_sources <= 12:
            raise ValueError("maximum_sources must be between 1 and 12")
        self._authorize_object_refs(user_id=user_id, context=context)
        candidates = [
            *context_sources,
            *self._confirmed_private_materials(user_id=user_id),
        ]
        query_terms = _terms(question)
        ranked: list[tuple[float, _Candidate, float, float]] = []
        blocked: list[EvidenceReference] = []
        for candidate in candidates:
            if candidate.untrusted_input and prompt_injection_detected(candidate.text):
                blocked.append(candidate.reference)
                continue
            lexical = _jaccard(query_terms, _terms(candidate.text))
            semantic = _semantic_overlap(question, candidate)
            score = min(
                1.0,
                (0.55 * lexical) + (0.25 * semantic) + (0.20 * candidate.source_weight),
            )
            if score > 0.20 or candidate.trust_scope in {
                "learning_brain",
                "target_position",
            }:
                ranked.append((score, candidate, lexical, semantic))
        ranked.sort(key=lambda item: (-item[0], item[1].reference.source_id))
        selected = _mmr_select(ranked, maximum_sources=maximum_sources)
        sources = tuple(
            RetrievedSource(
                reference=candidate.reference,
                trustScope=candidate.trust_scope,
                excerpt=_excerpt(candidate.text),
                lexicalScore=round(lexical, 6),
                semanticScore=round(semantic, 6),
                sourceWeight=candidate.source_weight,
                finalScore=round(score, 6),
                untrustedInput=candidate.untrusted_input,
            )
            for score, candidate, lexical, semantic in selected
        )
        return RetrievalBundle(
            bundleId=uuid4(),
            queryFingerprint=_fingerprint({"question": question}),
            sources=sources,
            blockedSourceRefs=tuple(blocked),
            securityDecision=(
                "sources_blocked"
                if blocked and sources
                else "all_sources_blocked"
                if blocked
                else "clear"
            ),
        )

    def _authorize_object_refs(self, *, user_id: str, context: GlobalAskContext) -> None:
        for item in context.object_refs:
            if not self._object_ref_owned(
                user_id=user_id,
                object_type=item.type,
                object_id=item.id,
                version=item.version,
            ):
                raise ObjectAuthorizationError(
                    f"unauthorized_{item.type}_reference"
                )

    def _object_ref_owned(
        self,
        *,
        user_id: str,
        object_type: str,
        object_id: str,
        version: str,
    ) -> bool:
        statements = {
            "position": """
                select exists (
                  select 1 from learner.position_goal_versions goal
                  where goal.user_id = cast(:user_id as uuid)
                    and goal.position_id = cast(:object_id as uuid)
                    and goal.goal_version_id = cast(:version as uuid)
                )
            """,
            "attempt": """
                select exists (
                  select 1 from learning.xingce_attempt_records attempt
                  where attempt.user_id = cast(:user_id as uuid)
                    and attempt.attempt_record_id = cast(:object_id as uuid)
                    and attempt.content_version_id = :version
                  union all
                  select 1 from learning.essay_attempts attempt
                  where attempt.user_id = cast(:user_id as uuid)
                    and attempt.attempt_id = cast(:object_id as uuid)
                    and attempt.task_version_id = cast(:version as uuid)
                )
            """,
            "plan": """
                select exists (
                  select 1 from learning.daily_plan_versions plan
                  where plan.user_id = cast(:user_id as uuid)
                    and plan.plan_version_id = cast(:object_id as uuid)
                    and plan.version_no::text = :version
                  union all
                  select 1 from learning.weekly_plan_versions plan
                  where plan.user_id = cast(:user_id as uuid)
                    and plan.weekly_plan_version_id = cast(:object_id as uuid)
                    and plan.version_no::text = :version
                )
            """,
            "report": """
                select exists (
                  select 1 from learning.report_projection_versions report
                  where report.user_id = cast(:user_id as uuid)
                    and report.projection_version_id = cast(:object_id as uuid)
                    and report.input_fingerprint = :version
                )
            """,
            "material": """
                select exists (
                  select 1 from learning.source_artifact_versions artifact
                  where artifact.owner_user_id = cast(:user_id as uuid)
                    and artifact.artifact_id = cast(:object_id as uuid)
                    and artifact.artifact_version_id = cast(:version as uuid)
                    and artifact.state = 'available'
                  union all
                  select 1 from learning.essay_attempts attempt
                  where attempt.user_id = cast(:user_id as uuid)
                    and attempt.material_bundle_version_id = cast(:object_id as uuid)
                    and attempt.task_version_id = cast(:version as uuid)
                )
            """,
            "question": """
                select exists (
                  select 1 from learning.xingce_attempt_records attempt
                  where attempt.user_id = cast(:user_id as uuid)
                    and attempt.question_id = :object_id
                    and attempt.content_version_id = :version
                  union all
                  select 1 from learning.essay_attempts attempt
                  where attempt.user_id = cast(:user_id as uuid)
                    and attempt.task_key = :object_id
                    and attempt.task_version_id = cast(:version as uuid)
                )
            """,
        }
        statement = statements[object_type]
        try:
            return bool(
                self._session.execute(
                    text(statement),
                    {
                        "user_id": user_id,
                        "object_id": object_id,
                        "version": version,
                    },
                ).scalar_one()
            )
        except Exception as exc:
            raise ObjectAuthorizationError(
                f"malformed_{object_type}_reference"
            ) from exc

    def _confirmed_private_materials(self, *, user_id: str) -> tuple[_Candidate, ...]:
        rows = self._session.execute(
            text(
                """
                select recognition.recognition_revision_id,
                       recognition.artifact_version_id,
                       recognition.recognition_kind,
                       recognition.candidate_payload,
                       artifact.source_kind,
                       artifact.content_sha256
                from learning.recognition_revisions recognition
                join learning.source_artifact_versions artifact
                  on artifact.artifact_version_id = recognition.artifact_version_id
                 and artifact.owner_user_id = recognition.owner_user_id
                where recognition.owner_user_id = cast(:user_id as uuid)
                  and recognition.state = 'confirmed'
                  and artifact.state = 'available'
                  and not exists (
                    select 1 from learning.recognition_revisions successor
                    where successor.supersedes_recognition_revision_id =
                          recognition.recognition_revision_id
                  )
                order by recognition.created_at desc
                limit 40
                """
            ),
            {"user_id": user_id},
        ).mappings()
        candidates: list[_Candidate] = []
        for row in rows:
            payload = dict(row["candidate_payload"])
            spans = payload.get("textSpans")
            if not isinstance(spans, list):
                continue
            texts = [
                str(item.get("text", "")).strip()
                for item in spans
                if isinstance(item, dict) and str(item.get("text", "")).strip()
            ]
            value = "\n".join(texts)
            if not value:
                continue
            recognition_id = str(row["recognition_revision_id"])
            candidates.append(
                _Candidate(
                    reference=EvidenceReference(
                        kind="user_material",
                        sourceId=f"recognition:{recognition_id}",
                        version=recognition_id,
                        anchor="text-spans",
                        title=_private_material_title(
                            source_kind=str(row["source_kind"]),
                            recognition_kind=str(row["recognition_kind"]),
                        ),
                    ),
                    text=value,
                    trust_scope="owner_private_material",
                    source_weight=0.85,
                    untrusted_input=True,
                )
            )
        return tuple(candidates)


def context_candidate(
    *,
    kind: str,
    source_id: str,
    version: str,
    anchor: str,
    title: str,
    text_value: str,
    trust_scope: str,
    source_weight: float,
) -> _Candidate:
    return _Candidate(
        reference=EvidenceReference(
            kind=kind,
            sourceId=source_id,
            version=version,
            anchor=anchor,
            title=title,
        ),
        text=text_value,
        trust_scope=trust_scope,
        source_weight=source_weight,
        untrusted_input=False,
    )


def _terms(value: str) -> set[str]:
    normalized = "".join(value.casefold().split())
    latin = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    chinese = "".join(re.findall(r"[\u3400-\u9fff]", normalized))
    grams = {chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))}
    return latin | grams


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _semantic_overlap(question: str, candidate: _Candidate) -> float:
    groups = (
        ("岗位", "资格", "报名", "目标分"),
        ("计划", "今天", "安排", "任务", "复测"),
        ("错因", "做错", "失分", "方法"),
        ("申论", "材料", "要点", "结构", "改写"),
        ("行测", "政治理论", "资料分析", "判断推理", "言语", "数量", "常识"),
    )
    question_groups = {
        index for index, group in enumerate(groups) if any(term in question for term in group)
    }
    text_groups = {
        index
        for index, group in enumerate(groups)
        if any(term in candidate.text or term in candidate.reference.title for term in group)
    }
    return _jaccard(
        {str(item) for item in question_groups},
        {str(item) for item in text_groups},
    )


def _mmr_select(
    ranked: list[tuple[float, _Candidate, float, float]],
    *,
    maximum_sources: int,
) -> list[tuple[float, _Candidate, float, float]]:
    selected: list[tuple[float, _Candidate, float, float]] = []
    remaining = list(ranked)
    while remaining and len(selected) < maximum_sources:
        choice = max(
            remaining,
            key=lambda item: (
                (0.75 * item[0])
                - (
                    0.25
                    * max(
                        (
                            _jaccard(
                                _terms(item[1].text),
                                _terms(existing[1].text),
                            )
                            for existing in selected
                        ),
                        default=0.0,
                    )
                ),
                item[1].reference.source_id,
            ),
        )
        selected.append(choice)
        remaining.remove(choice)
    return selected


def _excerpt(value: str) -> str:
    collapsed = " ".join(value.split())
    return collapsed[:2000]


def _private_material_title(*, source_kind: str, recognition_kind: str) -> str:
    source_label = {
        "camera_image": "拍摄资料",
        "image_import": "图片资料",
        "pdf_upload": "PDF 资料",
        "answer_sheet": "答题材料",
        "notes_app_export": "笔记资料",
    }.get(source_kind, "个人资料")
    recognition_label = {
        "printed_ocr": "印刷文字",
        "handwriting_ocr": "手写文字",
        "exam_segmentation": "试卷分段",
        "answer_sheet_omr": "答题卡识别",
    }.get(recognition_kind, "识别内容")
    return f"{source_label} · {recognition_label}"


def _fingerprint(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
