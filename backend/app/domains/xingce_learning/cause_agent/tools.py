from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.domains.xingce_learning.cause_agent.repository import CauseAgentRepository


CAUSE_TAXONOMY_VERSION = "xingce-cause-taxonomy@v1"
ACTION_TRACE_CONTRACT_VERSION = "xingce-action-trace@v1"
INK_FORMULA_CONTRACT_VERSION = "xingce-ink-formula@v1"
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


class CauseAgentTools:
    def __init__(
        self,
        *,
        session: Session,
        repository: CauseAgentRepository,
        user_id: str,
        session_id: str,
        reflection_version_id: str,
    ) -> None:
        self._session = session
        self._repository = repository
        self._user_id = user_id
        self._session_id = session_id
        self._reflection_version_id = reflection_version_id

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
        rows = self._repository.attempt_evidence(
            self._session,
            user_id=self._user_id,
            session_id=self._session_id,
        )
        return {
            "contractVersion": "xingce-attempt-evidence@v1",
            "items": [
                {
                    "questionId": str(row["question_id"]),
                    "resultCode": str(row["result_code"]),
                    "result": dict(row["result_snapshot"]),
                    "answer": dict(row["answer_payload"]),
                    "contentVersionId": str(row["content_version_id"]),
                    "assessmentId": str(row["assessment_id"]),
                    # 该题存在本人未撤销的内容争议：证据仍然给出（不偷偷剔除），
                    # 但显式标注，让错因候选必须知晓争议状态。
                    "disputed": bool(row["disputed"]),
                }
                for row in rows
            ],
            "evidenceRefs": [
                f"assessment:{row['assessment_id']}" for row in rows
            ]
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
        published = [
            {
                "questionId": str(item["questionId"]),
                "contentVersionId": str(item["contentVersionId"]),
                "explanation": item.get("explanation"),
                "explanationSource": item.get("explanationSource"),
            }
            for item in questions
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

        数据源是 learning.xingce_task_sessions.checkpoint 已冻结的 answers；
        没有记录的题保持显式空（events=[]、笔画统计为 0），不推断过程。
        """

        self._require_scope(arguments)
        row = self._repository.session_checkpoint(
            self._session,
            user_id=self._user_id,
            session_id=self._session_id,
        )
        if row is None:
            raise PermissionError("task session is not owned by the frozen learner scope")
        answers = list(dict(row["checkpoint"]).get("answers", []))
        items: list[dict[str, Any]] = []
        traced_question_ids: list[str] = []
        for answer in answers:
            question_id = str(answer["questionId"])
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
            stroke_at_ms = sorted(int(stroke["atMs"]) for stroke in strokes)
            truncated = bool(answer.get("actionEventsTruncated", False))
            items.append(
                {
                    "questionId": question_id,
                    "events": events,
                    "annotationSummary": {
                        "strokeCount": len(strokes),
                        "erasedStrokeCount": sum(
                            1
                            for stroke in strokes
                            if stroke.get("erasedAtMs") is not None
                        ),
                        "firstStrokeAtMs": stroke_at_ms[0] if stroke_at_ms else None,
                        "lastStrokeAtMs": stroke_at_ms[-1] if stroke_at_ms else None,
                    },
                    # 端上 400 事件截断标记：为 true 时事件序列只是前缀，
                    # 错因判断必须知道后续动作缺失，而不是当作完整轨迹。
                    "actionEventsTruncated": truncated,
                }
            )
            if events or strokes or truncated:
                traced_question_ids.append(question_id)
        return {
            "contractVersion": ACTION_TRACE_CONTRACT_VERSION,
            "items": items,
            # 只有真实存在轨迹（事件/笔画/截断标记）的题才发 action-trace 引用；
            # 完全无轨迹的会话返回显式空引用，杜绝凭空引用行为证据。
            "evidenceRefs": [
                f"action-trace:{question_id}" for question_id in traced_question_ids
            ]
            or [f"task-session:{self._session_id}:no-action-trace"],
        }

    def load_ink_formulas(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """返回该会话各题当前有效的候选/已确认手写公式（latex+时间）。

        数据源是 learning.math_formula_versions 的版本链头（无后继版本），
        已被用户拒绝的公式不作为错因证据；没有公式时返回显式空。
        """

        self._require_scope(arguments)
        rows = self._repository.ink_formula_evidence(
            self._session,
            user_id=self._user_id,
            session_id=self._session_id,
        )
        items = [
            {
                "questionId": str(row["question_id"]),
                "formulaVersionId": str(row["formula_version_id"]),
                "state": str(row["state"]),
                "latex": str(row["latex"]),
                "source": str(row["source"]),
                "createdAt": row["created_at"].isoformat(),
                "confirmedAt": (
                    row["confirmed_at"].isoformat()
                    if row["confirmed_at"] is not None
                    else None
                ),
            }
            for row in rows
        ]
        refs: list[str] = []
        for item in items:
            ref = f"ink-formula:{item['questionId']}"
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
