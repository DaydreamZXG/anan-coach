from __future__ import annotations

from hashlib import sha256
import json
import math
from typing import Any

from sqlalchemy.orm import Session

from app.domains.math_ink.provider import (
    FORMULA_CANDIDATE_MEDIUM_THRESHOLD,
    FORMULA_CONFIDENCE_POLICY_VERSION,
    FORMULA_CONFIDENCE_SEMANTICS,
    formula_evidence_strength,
)
from app.domains.xingce_learning.cause_agent.repository import CauseAgentRepository


CAUSE_TAXONOMY_VERSION = "xingce-cause-taxonomy@v1"
ACTION_TRACE_CONTRACT_VERSION = "xingce-action-trace@v3"
INK_FORMULA_CONTRACT_VERSION = "xingce-ink-formula@v1"
SCRATCH_REASONING_CONTRACT_VERSION = "xingce-scratch-reasoning@v1"
CAUSES = (
    ("stem_misread", "题干条件或问法审读错误"),
    ("knowledge_gap", "知识或概念缺口"),
    ("method_selection", "方法选择或步骤组织不当"),
    ("calculation_error", "方法成立但计算、单位或抄写错误"),
    ("time_pressure", "时间分配导致超时或仓促作答"),
    ("guessing", "证据不足时凭感觉作答"),
    ("active_abandon", "主动放弃或跳过"),
    ("other", "现有分类无法覆盖"),
)

_NON_REVIEWABLE_RESULT_CODES = {"unanswered", "skipped"}
_RESULT_EVIDENCE_FIELDS = (
    "resultCode",
    "selectedOptionIds",
    "correctOptionIds",
    "answerChangeCount",
    "factContractVersion",
    "objectiveVerdict",
    "disposition",
    "confidenceEvidence",
    "timingEvidence",
)
_ANSWER_EVIDENCE_FIELDS = (
    "selectedOptionIds",
    "eliminatedOptionIds",
    "confidence",
    "marked",
    "skipped",
    "elapsedSeconds",
    "draftText",
    "materialPosition",
)


class CauseAgentTools:
    def __init__(
        self,
        *,
        session: Session,
        repository: CauseAgentRepository,
        user_id: str,
        session_id: str,
        reflection_version_id: str,
        input_version_refs: list[str] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._user_id = user_id
        self._session_id = session_id
        self._reflection_version_id = reflection_version_id
        self._input_version_refs = list(input_version_refs or [])
        self._reviewable_rows: list[Any] | None = None

    @property
    def handlers(self) -> dict[str, Any]:
        return {
            "load_attempt_evidence": self.load_attempt_evidence,
            "load_user_reflection": self.load_user_reflection,
            "load_published_question_explanation": (
                self.load_published_question_explanation
            ),
            "load_cause_taxonomy": self.load_cause_taxonomy,
            "load_action_trace": self.load_action_trace,
            "load_ink_formulas": self.load_ink_formulas,
        }

    def _require_scope(self, arguments: dict[str, Any]) -> None:
        if arguments != {
            "userId": self._user_id,
            "sessionId": self._session_id,
            "reflectionVersionId": self._reflection_version_id,
        }:
            raise PermissionError("tool arguments do not match the frozen review scope")

    def load_attempt_evidence(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._require_scope(arguments)
        rows = self._reviewable_attempt_rows()
        bundle_row = self._repository.session_bundle(
            self._session,
            user_id=self._user_id,
            session_id=self._session_id,
        )
        if bundle_row is None:
            raise PermissionError("task session is not owned by the frozen learner scope")
        safe_bundle = dict(bundle_row["safe_bundle"])
        questions = {
            str(item["questionId"]): item
            for item in safe_bundle.get("questions", [])
            if isinstance(item, dict) and item.get("questionId")
        }
        reviewable_question_ids = {str(row["question_id"]) for row in rows}
        material_ids = {
            str(question["materialId"])
            for question_id, question in questions.items()
            if question_id in reviewable_question_ids and question.get("materialId")
        }
        return {
            "contractVersion": "xingce-attempt-evidence@v1",
            "items": [
                {
                    "questionId": str(row["question_id"]),
                    "question": _compact_question(questions.get(str(row["question_id"]))),
                    "resultCode": str(row["result_code"]),
                    # 原始 answer payload 还包含全量笔迹点和动作事件；
                    # 这些已由 action-trace 工具去重摘要，不再重复发给模型。
                    "result": _pick(dict(row["result_snapshot"]), _RESULT_EVIDENCE_FIELDS),
                    "answer": _pick(dict(row["answer_payload"]), _ANSWER_EVIDENCE_FIELDS),
                    "contentVersionId": str(row["content_version_id"]),
                    "assessmentId": str(row["assessment_id"]),
                    # 该题存在本人未撤销的内容争议：证据仍然给出（不偷偷剔除），
                    # 但显式标注，让错因候选必须知晓争议状态。
                    "disputed": bool(row["disputed"]),
                }
                for row in rows
            ],
            "materials": [
                _compact_material(material)
                for material in safe_bundle.get("materials", [])
                if isinstance(material, dict)
                and str(material.get("materialId", "")) in material_ids
            ],
            "evidenceRefs": [
                f"assessment:{row['assessment_id']}" for row in rows
            ]
            + list(
                dict.fromkeys(
                    f"content-version:{row['content_version_id']}" for row in rows
                )
            )
            + [
                f"question-dispute:{row['question_id']}"
                for row in rows
                if row["disputed"]
            ],
        }

    def load_user_reflection(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._require_scope(arguments)
        row = self._repository.reflection_evidence(
            self._session,
            user_id=self._user_id,
            reflection_version_id=self._reflection_version_id,
        )
        if row is None:
            raise PermissionError("reflection is not owned by the frozen learner scope")
        return {
            "contractVersion": "xingce-reflection-evidence@v1",
            "payload": dict(row["payload"]),
            "payloadSha256": str(row["payload_sha256"]),
            "evidenceRefs": [f"reflection-version:{self._reflection_version_id}"],
        }

    def load_published_question_explanation(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_scope(arguments)
        row = self._repository.session_bundle(
            self._session,
            user_id=self._user_id,
            session_id=self._session_id,
        )
        if row is None:
            raise PermissionError("task session is not owned by the frozen learner scope")
        questions = list(dict(row["scoring_bundle"])["questions"])
        reviewable_question_ids = self._reviewable_question_ids()
        published = [
            {
                "questionId": str(item["questionId"]),
                "contentVersionId": str(item["contentVersionId"]),
                "explanation": item.get("explanation"),
                "explanationSource": item.get("explanationSource"),
            }
            for item in questions
            if str(item["questionId"]) in reviewable_question_ids
            if item.get("explanationSource") in {"verified", "ai_rewrite_accepted"}
        ]
        return {
            "contractVersion": "published-question-explanation@v1",
            "items": published,
            "evidenceRefs": [
                f"content-version:{item['contentVersionId']}" for item in published
            ]
            or [f"task-session:{self._session_id}:no-published-explanation"],
        }

    def load_action_trace(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """按题返回检查点里真实记录的动作事件摘要与批注笔画统计。

        数据源是提交时绑定的 learning.xingce_answer_versions；
        没有记录的题保持显式空（events=[]、笔画统计为 0），不推断过程。
        契约增量 v2：笔画携带 anchor 时，额外汇总用户实际锚定的文字段
        （anchoredSegments + anchoredSummary）；无 anchor 的旧笔迹保持原有字段。
        契约增量 v3：输出 scratchReasoning，把打字草稿、已确认公式、
        锚定关键词、笔迹几何、时序和擦改转成可引用的题级证据。
        不尝试从自由笔迹猜测文字；不可辨内容只标记 unknown。
        """

        self._require_scope(arguments)
        rows = self._reviewable_attempt_rows()
        bundle = self._repository.session_bundle(
            self._session,
            user_id=self._user_id,
            session_id=self._session_id,
        )
        if bundle is None:
            raise PermissionError("task session is not owned by the frozen learner scope")
        items: list[dict[str, Any]] = []
        scratch_items: list[dict[str, Any]] = []
        traced_question_ids: list[str] = []
        exact_formula_rows = self._exact_formula_rows()
        has_formula_version_refs = any(
            ref.startswith("formula-version:") for ref in self._input_version_refs
        )
        # Only pre-version-ref historical runs may discover their current
        # formula heads dynamically.  Any new run with frozen input refs stays
        # fail-closed and consumes only exact formula-version refs.
        formula_rows = (
            exact_formula_rows
            if has_formula_version_refs
            else self._repository.ink_formula_evidence(
                self._session,
                user_id=self._user_id,
                session_id=self._session_id,
            )
        )
        for row in rows:
            question_id = str(row["question_id"])
            frozen_projection = (
                dict(row["scratch_evidence_projection"])
                if isinstance(row.get("scratch_evidence_projection"), dict)
                else None
            )
            if frozen_projection is not None:
                annotation_payload = frozen_projection.get("annotations")
                answer = {
                    "draftText": frozen_projection.get("typedDraft", ""),
                    "annotations": (
                        list(annotation_payload.get("vectorStrokes", []))
                        if isinstance(annotation_payload, dict)
                        else []
                    ),
                    "actionEvents": list(
                        frozen_projection.get("actionSequence", [])
                    ),
                    "actionEventsTruncated": bool(
                        frozen_projection.get("actionSequenceTruncated", False)
                    ),
                }
                confirmed_for_question = [
                    formula
                    for formula in formula_rows
                    if str(formula["question_id"]) == question_id
                    and str(formula["state"]) == "confirmed"
                ]
                projection_ref = (
                    "scratch-evidence-projection:"
                    f"{row['attempt_record_id']}:"
                    f"{row['scratch_evidence_fingerprint']}"
                )
            else:
                # Historical attempt compatibility: before v103 the immutable
                # answer revision was the strongest available evidence.
                answer = dict(row["answer_payload"])
                confirmed_for_question = [
                    formula
                    for formula in formula_rows
                    if str(formula["question_id"]) == question_id
                    and str(formula["state"]) == "confirmed"
                ]
                projection_ref = f"scratch-reasoning:{question_id}"
            events = [
                {
                    "type": str(event["type"]),
                    "atMs": int(event["atMs"]),
                    **(
                        {"optionId": str(event["optionId"])}
                        if event.get("optionId") is not None
                        else {}
                    ),
                }
                for event in answer.get("actionEvents", [])
            ]
            strokes = list(answer.get("annotations", []))
            # 早期冻结答案里存在没有 atMs 的旧笔画；它们仍是真实
            # 草稿证据，但不能伪造首末时间，因此统计时忽略缺失值。
            stroke_at_ms = sorted(
                int(stroke["atMs"])
                for stroke in strokes
                if stroke.get("atMs") is not None
            )
            truncated = bool(answer.get("actionEventsTruncated", False))
            annotation_summary: dict[str, Any] = {
                "strokeCount": len(strokes),
                "erasedStrokeCount": sum(
                    1
                    for stroke in strokes
                    if stroke.get("erasedAtMs") is not None
                ),
                "firstStrokeAtMs": stroke_at_ms[0] if stroke_at_ms else None,
                "lastStrokeAtMs": stroke_at_ms[-1] if stroke_at_ms else None,
            }
            material_stroke_count = sum(
                1 for stroke in strokes if stroke.get("surface", "question") == "material"
            )
            if material_stroke_count > 0:
                annotation_summary["surfaceCounts"] = {
                    "question": len(strokes) - material_stroke_count,
                    "material": material_stroke_count,
                }
            anchored = _collect_anchored_segments(strokes)
            if anchored is not None:
                annotation_summary.update(anchored)
            items.append(
                {
                    "questionId": question_id,
                    "events": events,
                    "annotationSummary": annotation_summary,
                    # 端上 400 事件截断标记：为 true 时事件序列只是前缀，
                    # 错因判断必须知道后续动作缺失，而不是当作完整轨迹。
                    "actionEventsTruncated": truncated,
                }
            )
            if events or strokes or truncated:
                traced_question_ids.append(question_id)
            scratch = _scratch_reasoning_item(
                question_id=question_id,
                answer=answer,
                confirmed_formulas=confirmed_for_question,
                projection_ref=projection_ref,
            )
            if scratch is not None:
                scratch_items.append(scratch)
        scratch_refs = list(
            dict.fromkeys(
                ref
                for item in scratch_items
                for ref in (
                    str(item["projectionRef"]),
                    f"scratch-reasoning:{item['questionId']}",
                )
            )
        )
        return {
            "contractVersion": ACTION_TRACE_CONTRACT_VERSION,
            "items": items,
            # 只有真实存在轨迹（事件/笔画/截断标记）的题才发 action-trace 引用；
            # 完全无轨迹的会话返回显式空引用，杜绝凭空引用行为证据。
            "evidenceRefs": [
                f"action-trace:{question_id}" for question_id in traced_question_ids
            ]
            or [f"task-session:{self._session_id}:no-action-trace"],
            "scratchReasoning": {
                "contractVersion": SCRATCH_REASONING_CONTRACT_VERSION,
                "items": scratch_items,
                "evidenceRefs": scratch_refs
                or [f"task-session:{self._session_id}:no-scratch-reasoning"],
            },
        }

    def load_ink_formulas(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """返回该会话各题当前有效的候选/已确认手写公式（latex+时间）。

        数据源是 learning.math_formula_versions 的版本链头（无后继版本），
        已被用户拒绝的公式不作为错因证据；没有公式时返回显式空。
        """

        self._require_scope(arguments)
        attempt_rows = self._reviewable_attempt_rows()
        exact_rows = self._exact_formula_rows()
        has_formula_version_refs = any(
            ref.startswith("formula-version:") for ref in self._input_version_refs
        )
        current_rows = (
            exact_rows
            if has_formula_version_refs
            else self._repository.ink_formula_evidence(
                self._session,
                user_id=self._user_id,
                session_id=self._session_id,
            )
        )
        items: list[dict[str, Any]] = []
        for attempt in attempt_rows:
            question_id = str(attempt["question_id"])
            items.extend(
                _formula_evidence_item(question_id=question_id, row=row)
                for row in current_rows
                if str(row["question_id"]) == question_id
            )
        refs: list[str] = []
        for item in items:
            ref = f"formula-version:{item['formulaVersionId']}"
            if ref not in refs:
                refs.append(ref)
        return {
            "contractVersion": INK_FORMULA_CONTRACT_VERSION,
            "items": items,
            "evidenceRefs": refs
            or [f"task-session:{self._session_id}:no-ink-formula"],
        }

    def load_cause_taxonomy(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._require_scope(arguments)
        return {
            "contractVersion": CAUSE_TAXONOMY_VERSION,
            "causes": [
                {"code": code, "description": description}
                for code, description in CAUSES
            ],
            "evidenceRefs": [f"cause-taxonomy:{CAUSE_TAXONOMY_VERSION}"],
        }

    def _reviewable_attempt_rows(self) -> list[Any]:
        """Only real answered items belong in cause analysis.

        Final-submit turns untouched remainder into explicit unanswered records so the
        attempt can be settled.  Those records are useful for completion accounting,
        but they are not evidence that the learner "cannot do" a question and must not
        be sent to the cause model.
        """

        if self._reviewable_rows is None:
            rows = self._repository.attempt_evidence(
                self._session,
                user_id=self._user_id,
                session_id=self._session_id,
            )
            self._reviewable_rows = [
                row
                for row in rows
                if str(row["result_code"]) not in _NON_REVIEWABLE_RESULT_CODES
            ]
        return self._reviewable_rows

    def _reviewable_question_ids(self) -> set[str]:
        return {str(row["question_id"]) for row in self._reviewable_attempt_rows()}

    def _exact_formula_rows(self) -> list[dict[str, Any]]:
        refs: list[tuple[str, str]] = []
        for ref in self._input_version_refs:
            if not ref.startswith("formula-version:"):
                continue
            parts = ref.split(":", 2)
            if len(parts) != 3 or len(parts[2]) != 64:
                raise PermissionError("formula evidence ref is malformed")
            refs.append((parts[1], parts[2]))
        if not refs:
            return []
        rows = self._repository.formula_versions_by_ids(
            self._session,
            user_id=self._user_id,
            session_id=self._session_id,
            formula_version_ids=[item[0] for item in refs],
        )
        expected = dict(refs)
        if len(rows) != len(refs):
            raise PermissionError("frozen formula evidence is unavailable")
        for row in rows:
            version_id = str(row["formula_version_id"])
            actual = sha256(str(row["latex"]).encode("utf-8")).hexdigest()
            if expected.get(version_id) != actual:
                raise PermissionError("frozen formula evidence fingerprint changed")
        return rows


def _scratch_reasoning_item(
    *,
    question_id: str,
    answer: dict[str, Any],
    confirmed_formulas: list[dict[str, Any]],
    projection_ref: str | None = None,
) -> dict[str, Any] | None:
    """把冻结草稿转成可审计的思路证据。

    这里只做确定性识别：锚定高亮、横向下划线、闭合圈选、
    两条相交斜线划掉。其他自由手写不做 OCR，始终返回
    freehand_unknown，避免把笔迹坐标猜成文字或公式。
    """

    draft_text = str(answer.get("draftText", "")).strip()
    strokes = [item for item in answer.get("annotations", []) if isinstance(item, dict)]
    formulas = [
        _formula_evidence_item(question_id=question_id, row=row)
        for row in confirmed_formulas
    ]
    if not draft_text and not strokes and not formulas:
        return None

    observations = _scratch_observations(strokes)
    unknown_count = sum(
        1
        for item in observations
        if str(item["kind"]).endswith("_unknown")
    )
    signals: list[str] = []
    if draft_text or formulas:
        signals.append("externalized_calculation_or_notes")
    if any(item.get("anchor") for item in observations):
        signals.append("focused_on_anchored_text")
    if any(item.get("surface") == "material" for item in observations):
        signals.append("worked_on_shared_material")
    if any(item.get("erasedAtMs") is not None for item in observations):
        signals.append("revised_written_work")

    fingerprint_input = {
        "questionId": question_id,
        "draftText": draft_text,
        "annotations": strokes,
        "confirmedFormulas": formulas,
    }
    input_fingerprint = sha256(
        json.dumps(
            fingerprint_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "questionId": question_id,
        "projectionRef": projection_ref or f"scratch-reasoning:{question_id}",
        "inputFingerprint": input_fingerprint,
        "typedDraft": draft_text or None,
        "confirmedFormulas": formulas,
        "observations": observations,
        "phases": _scratch_phases(observations),
        "reasoningSignals": signals,
        "revisionCount": sum(
            1 for item in observations if item.get("erasedAtMs") is not None
        ),
        "unknownFreehandCount": unknown_count,
        "coverage": "partial" if unknown_count else "structured",
        "limitations": (
            ["未锚定的自由笔迹未做文字猜测"] if unknown_count else []
        ),
    }


def _formula_evidence_item(
    *,
    question_id: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    metadata = row.get("recognition_metadata")
    recognition_metadata = dict(metadata) if isinstance(metadata, dict) else None
    # v3 historical scratch helpers only passed already-confirmed formula rows
    # and therefore did not carry an explicit state column.
    state = str(row.get("state", "confirmed"))
    source = str(row["source"])
    confidence = (
        recognition_metadata.get("confidence")
        if recognition_metadata is not None
        else None
    )
    return {
        "questionId": question_id,
        "formulaVersionId": str(row["formula_version_id"]),
        "sourceVersionRef": f"formula-version:{row['formula_version_id']}",
        "state": state,
        "latex": str(row["latex"]),
        "source": source,
        "confidence": confidence,
        "strength": formula_evidence_strength(
            state=state,
            source=source,
            recognition_metadata=recognition_metadata,
        ),
        "confidencePolicyVersion": FORMULA_CONFIDENCE_POLICY_VERSION,
        "candidateMediumThreshold": FORMULA_CANDIDATE_MEDIUM_THRESHOLD,
        "confidenceSemantics": FORMULA_CONFIDENCE_SEMANTICS,
        "recognitionMetadata": recognition_metadata,
        "createdAt": (
            _isoformat(row["created_at"])
            if row.get("created_at") is not None
            else None
        ),
        "confirmedAt": (
            _isoformat(row["confirmed_at"])
            if row.get("confirmed_at") is not None
            else None
        ),
    }


def _isoformat(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _scratch_observations(strokes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(strokes, key=lambda item: (int(item.get("atMs", 0)), str(item.get("strokeId", ""))))
    consumed: set[str] = set()
    observations: list[dict[str, Any]] = []

    # 只有同一锚定文段、时间接近、方向相反且真实相交的两条直线
    # 才解释成“划掉”；单斜线不推断排除意图。
    for index, first in enumerate(ordered):
        first_id = str(first.get("strokeId", ""))
        if first_id in consumed or not _anchor_key(first):
            continue
        for second in ordered[index + 1 :]:
            second_id = str(second.get("strokeId", ""))
            if second_id in consumed or _anchor_key(second) != _anchor_key(first):
                continue
            if int(second.get("atMs", 0)) - int(first.get("atMs", 0)) > 3_000:
                break
            if _forms_cross(first, second):
                consumed.update((first_id, second_id))
                observations.append(
                    _observation(
                        kind="crossed_out_text",
                        strokes=(first, second),
                        confidence=0.9,
                    )
                )
                break

    for stroke in ordered:
        stroke_id = str(stroke.get("strokeId", ""))
        if stroke_id in consumed:
            continue
        metrics = _stroke_metrics(stroke)
        anchor = stroke.get("anchor") if isinstance(stroke.get("anchor"), dict) else None
        tool = str(stroke.get("tool", "pen"))
        if anchor and tool == "highlighter":
            kind, confidence = "highlighted_text", 0.99
        elif anchor and _is_closed_loop(metrics):
            kind, confidence = "circled_text", 0.86
        elif anchor and _is_horizontal_mark(metrics):
            kind, confidence = "underlined_text", 0.88
        elif anchor:
            kind, confidence = "marked_text", 0.72
        elif _is_closed_loop(metrics):
            kind, confidence = "diagram_or_grouping_unknown", 0.68
        elif tool == "highlighter":
            kind, confidence = "freehand_highlight_unknown", 0.6
        else:
            kind, confidence = "freehand_unknown", 0.5
        observations.append(
            _observation(kind=kind, strokes=(stroke,), confidence=confidence)
        )

    observations.sort(key=lambda item: (int(item["atMs"]), str(item["observationId"])))
    for index, item in enumerate(observations, start=1):
        item["observationId"] = f"scratch-observation-{index}"
    return observations


def _observation(
    *,
    kind: str,
    strokes: tuple[dict[str, Any], ...],
    confidence: float,
) -> dict[str, Any]:
    first = min(strokes, key=lambda item: int(item.get("atMs", 0)))
    anchor = first.get("anchor") if isinstance(first.get("anchor"), dict) else None
    erased = [int(item["erasedAtMs"]) for item in strokes if item.get("erasedAtMs") is not None]
    payload: dict[str, Any] = {
        "observationId": "pending",
        "kind": kind,
        "surface": str(first.get("surface", "question")),
        "tool": str(first.get("tool", "pen")),
        "sourceStrokeIds": [str(item.get("strokeId", "")) for item in strokes],
        "atMs": int(first.get("atMs", 0)),
        "completedAtMs": max(int(item.get("atMs", 0)) for item in strokes),
        "erasedAtMs": max(erased) if erased else None,
        "confidence": confidence,
    }
    if anchor:
        payload["anchor"] = {
            "blockId": str(anchor.get("blockId", "")),
            "segmentIndex": int(anchor.get("segmentIndex", 0)),
            "segmentText": str(anchor.get("segmentText", "")),
        }
    return payload


def _scratch_phases(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phases: list[list[dict[str, Any]]] = []
    for observation in observations:
        if not phases or int(observation["atMs"]) - int(phases[-1][-1]["atMs"]) >= 5_000:
            phases.append([observation])
        else:
            phases[-1].append(observation)
    return [
        {
            "phaseNo": index,
            "fromMs": int(items[0]["atMs"]),
            "toMs": max(
                int(item.get("erasedAtMs") or item.get("completedAtMs") or item["atMs"])
                for item in items
            ),
            "observationRefs": [str(item["observationId"]) for item in items],
            "containsRevision": any(item.get("erasedAtMs") is not None for item in items),
        }
        for index, items in enumerate(phases, start=1)
    ]


def _anchor_key(stroke: dict[str, Any]) -> tuple[str, int, str] | None:
    anchor = stroke.get("anchor")
    if not isinstance(anchor, dict):
        return None
    block_id = str(anchor.get("blockId", ""))
    text = str(anchor.get("segmentText", ""))
    try:
        segment_index = int(anchor.get("segmentIndex", -1))
    except (TypeError, ValueError):
        return None
    if not block_id or not text or segment_index < 0:
        return None
    return block_id, segment_index, text


def _stroke_metrics(stroke: dict[str, Any]) -> dict[str, float]:
    points = [item for item in stroke.get("points", []) if isinstance(item, dict)]
    coordinates = [
        (float(item.get("x", 0.0)), float(item.get("y", 0.0)))
        for item in points
    ]
    if len(coordinates) < 2:
        return {"width": 0.0, "height": 0.0, "path": 0.0, "net": 0.0, "closure": 1.0, "dx": 0.0, "dy": 0.0}
    xs = [point[0] for point in coordinates]
    ys = [point[1] for point in coordinates]
    path = sum(math.dist(left, right) for left, right in zip(coordinates, coordinates[1:]))
    net = math.dist(coordinates[0], coordinates[-1])
    diagonal = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    return {
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
        "path": path,
        "net": net,
        "closure": net / max(diagonal, 1e-9),
        "dx": coordinates[-1][0] - coordinates[0][0],
        "dy": coordinates[-1][1] - coordinates[0][1],
    }


def _is_horizontal_mark(metrics: dict[str, float]) -> bool:
    return (
        metrics["width"] >= 0.05
        and metrics["height"] <= max(0.02, metrics["width"] * 0.25)
        and metrics["net"] / max(metrics["path"], 1e-9) >= 0.72
    )


def _is_closed_loop(metrics: dict[str, float]) -> bool:
    aspect = metrics["width"] / max(metrics["height"], 1e-9)
    return (
        metrics["width"] >= 0.03
        and metrics["height"] >= 0.03
        and 0.35 <= aspect <= 2.8
        and metrics["closure"] <= 0.3
        and metrics["path"] >= 1.2 * (metrics["width"] + metrics["height"])
    )


def _forms_cross(first: dict[str, Any], second: dict[str, Any]) -> bool:
    left = _stroke_metrics(first)
    right = _stroke_metrics(second)
    if any(
        metrics["net"] / max(metrics["path"], 1e-9) < 0.82
        or abs(metrics["dx"]) < 0.025
        or abs(metrics["dy"]) < 0.025
        for metrics in (left, right)
    ):
        return False
    if left["dx"] * left["dy"] * right["dx"] * right["dy"] >= 0:
        return False
    first_points = first.get("points", [])
    second_points = second.get("points", [])
    a = (float(first_points[0]["x"]), float(first_points[0]["y"]))
    b = (float(first_points[-1]["x"]), float(first_points[-1]["y"]))
    c = (float(second_points[0]["x"]), float(second_points[0]["y"]))
    d = (float(second_points[-1]["x"]), float(second_points[-1]["y"]))
    return _segments_intersect(a, b, c, d)


def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    def orientation(
        first: tuple[float, float],
        second: tuple[float, float],
        third: tuple[float, float],
    ) -> float:
        return (second[0] - first[0]) * (third[1] - first[1]) - (
            second[1] - first[1]
        ) * (third[0] - first[0])

    return orientation(a, b, c) * orientation(a, b, d) <= 0 and orientation(c, d, a) * orientation(c, d, b) <= 0


def _collect_anchored_segments(
    strokes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """把用户锚定批注的笔画汇总为 AI 可读的锚定文字段。

    仅汇总真实存在的 anchor；没有一条笔画携带 anchor 时返回 None，
    annotationSummary 保持旧结构（向后兼容无 anchor 的旧笔迹）。
    契约增量 v2：锚定文字是用户标记的题面/材料原文，不是用户输入。
    """

    if not strokes:
        return None
    ordered: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for stroke in strokes:
        anchor = stroke.get("anchor")
        if not isinstance(anchor, dict):
            continue
        block_id = str(anchor.get("blockId", ""))
        try:
            segment_index = int(anchor["segmentIndex"])
        except (KeyError, TypeError, ValueError):
            continue
        if not block_id or segment_index < 0:
            continue
        segment_text = str(anchor.get("segmentText", "")).strip()
        if not segment_text:
            continue
        if (block_id, segment_index) not in seen:
            # 收集顺序稳定：同 blockId+segmentIndex 的首笔时间即该段首次被标记时间。
            seen.add((block_id, segment_index))
            ordered.append(
                {
                    "blockId": block_id,
                    "segmentIndex": segment_index,
                    "segmentText": segment_text,
                    "strokeCount": 1,
                    "firstStrokeAtMs": int(stroke.get("atMs", 0)),
                }
            )
        else:
            for item in ordered:
                if (
                    item["blockId"] == block_id
                    and item["segmentIndex"] == segment_index
                ):
                    item["strokeCount"] += 1
                    break
    if not ordered:
        return None
    ordered.sort(key=lambda item: item["firstStrokeAtMs"])
    segments = [
        {
            "blockId": item["blockId"],
            "segmentIndex": item["segmentIndex"],
            "segmentText": item["segmentText"],
            "strokeCount": item["strokeCount"],
        }
        for item in ordered
    ]
    text_parts = [f"「{item['segmentText']}」" for item in ordered]
    return {
        "anchoredSegments": segments,
        "anchoredSummary": "用户依次标记了" + "、".join(text_parts),
    }


def _compact_question(question: Any) -> dict[str, Any] | None:
    if not isinstance(question, dict):
        return None
    return {
        "stem": str(question.get("stem", "")).strip(),
        "materialId": (
            str(question["materialId"]) if question.get("materialId") is not None else None
        ),
        "options": [
            {
                "optionId": str(option.get("optionId", "")),
                "label": str(option.get("label", "")),
                "text": str(option.get("text", "")),
            }
            for option in question.get("options", [])
            if isinstance(option, dict)
        ],
    }


def _compact_material(material: dict[str, Any]) -> dict[str, Any]:
    return {
        "materialId": str(material.get("materialId", "")),
        "title": material.get("title"),
        "text": material.get("text"),
        "content": [
            _compact_content_block(block)
            for block in material.get("content", [])
            if isinstance(block, dict)
        ],
    }


def _compact_content_block(block: dict[str, Any]) -> dict[str, Any]:
    kind = str(block.get("type", ""))
    if kind == "paragraph":
        return {"type": kind, "text": str(block.get("text", ""))}
    if kind == "formula":
        return {"type": kind, "expression": str(block.get("expression", ""))}
    if kind == "table":
        return {
            "type": kind,
            "cells": [
                {
                    "row": cell.get("row"),
                    "column": cell.get("column"),
                    "text": str(cell.get("text", "")),
                }
                for cell in block.get("cells", [])
                if isinstance(cell, dict)
            ],
        }
    return {"type": kind, "altText": str(block.get("altText", ""))}


def _pick(source: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: source[field] for field in fields if field in source}
