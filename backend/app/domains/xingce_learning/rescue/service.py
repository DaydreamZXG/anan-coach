from __future__ import annotations

from sqlalchemy.orm import Session

from app.domains.xingce_learning.rescue.repository import RescueRepository
from app.domains.xingce_learning.rescue.schemas import (
    MAX_ITEMS,
    RESCUABLE_CAUSE_CODES,
    RescueMethodCard,
    RescueNodeContext,
    RescueSimilarQuestion,
    XingceRescue,
    XingceRescueItem,
    build_cause_explanation,
    cause_label,
)


class XingceRescueError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class XingceRescueService:
    """错因 -> 方法卡 -> 同类题的桥接。

    只读：从已确认的错因（method_selection / knowledge_gap）出发，定位错题
    在知识树上的考点节点，经节点 method_card_ids 取方法卡，再从题库按考点
    选一道同类题。触发点在完成一组题、确认错因之后，不打断做题节奏。

    错因来源两层：
    1. 候选 payload 的逐题错因（questionCandidates）——能精确定位到题；
    2. 会话级已确认错因码——旧候选没有逐题字段时，把它应用到所有错题。
    """

    def __init__(self, repository: RescueRepository | None = None) -> None:
        self._repository = repository or RescueRepository

    def rescue(
        self,
        session: Session,
        *,
        user_id: str,
        review_id: str,
    ) -> XingceRescue:
        review = self._repository(session).current_review(
            user_id=user_id,
            review_id=review_id,
        )
        if review is None:
            raise XingceRescueError(
                "xingce_review_not_found",
                "行测复盘不存在。",
                status_code=404,
            )
        session_id = str(review["session_id"])
        candidate_version_id = (
            str(review["cause_candidate_version_id"])
            if review["cause_candidate_version_id"] is not None
            else None
        )
        repository = self._repository(session)

        # 1. 错因：逐题候选优先，回退到会话级已确认码。
        per_question = repository.per_question_candidate_codes(
            candidate_version_id=candidate_version_id,
        )
        session_codes = repository.confirmed_cause_codes(
            user_id=user_id,
            review_id=review_id,
        )
        rescusable_session_codes = [
            code for code in session_codes if code in RESCUABLE_CAUSE_CODES
        ]
        wrong_question_ids = repository.wrong_question_ids_for_review(
            user_id=user_id,
            session_id=session_id,
        )
        if not wrong_question_ids:
            return _empty_rescue(review_id=review_id, session_id=session_id)

        cause_by_question: dict[str, str] = {}
        for question_id in wrong_question_ids:
            per_question_code = per_question.get(question_id)
            if per_question_code in RESCUABLE_CAUSE_CODES:
                cause_by_question[question_id] = per_question_code
        # 会话级可救场错因应用到仍未命中的错题。
        if rescusable_session_codes and not cause_by_question:
            for question_id in wrong_question_ids:
                cause_by_question[question_id] = rescusable_session_codes[0]
        if not cause_by_question:
            return _empty_rescue(review_id=review_id, session_id=session_id)

        questions_to_locate = list(cause_by_question)

        # 2. 当前 fresh active 知识图谱发布
        release = repository.resolve_fresh_release()
        if release is None:
            return _empty_rescue(review_id=review_id, session_id=session_id)

        # 3. 错题 -> 知识树考点节点
        nodes_by_question = repository.knowledge_nodes_for_questions(
            question_ids=questions_to_locate,
            release=release,
        )

        items: list[XingceRescueItem] = []
        for question_id in questions_to_locate:
            if len(items) >= MAX_ITEMS:
                break
            node = nodes_by_question.get(question_id)
            if node is None:
                continue
            cause_code = cause_by_question[question_id]

            # 4. 方法卡（通过节点 method_card_ids，缺省取不到则无卡）
            method_card = None
            if node.method_card_ids:
                cards = repository.method_cards_by_ids(
                    list(node.method_card_ids),
                )
                first = (
                    cards[node.method_card_ids[0]]
                    if node.method_card_ids[0] in cards
                    else None
                )
                if first is not None:
                    method_card = RescueMethodCard(
                        cardId=first.card_id,
                        title=first.title,
                        moduleName=first.module_name,
                        tagPath=first.tag_path,
                        recognition=first.recognition,
                        methodSteps=list(first.method_steps),
                        pitfall=first.pitfall,
                        fixAction=first.fix_action,
                        timeBudgetSec=first.time_budget_sec,
                    )

            # 5. 同类题（排除本组已做过的题）
            similar = None
            similar_rows = repository.select_similar_question(
                user_id=user_id,
                node_id=node.node_id,
                module_name=node.module_name,
                release=release,
                excluded_question_ids=questions_to_locate,
                limit=1,
            )
            if similar_rows:
                row = similar_rows[0]
                similar = RescueSimilarQuestion(
                    questionId=row.question_id,
                    contentVersionId=row.content_version_id,
                    moduleName=row.module_name,
                    stem=row.stem,
                    options=[
                        {
                            "optionId": option["label"],
                            "label": option["label"],
                            "text": option["option_text"],
                        }
                        for option in row.options
                    ],
                    correctOptionIds=list(row.correct_option_ids),
                    explanation=row.explanation,
                )

            focus = _focus_label(node.display_path)
            items.append(
                XingceRescueItem(
                    questionId=question_id,
                    causeCode=cause_code,
                    causeLabel=cause_label(cause_code),
                    explanation=build_cause_explanation(
                        cause_code=cause_code,
                        focus=focus,
                    ),
                    node=RescueNodeContext(
                        nodeId=node.node_id,
                        displayPath=node.display_path,
                        moduleName=node.module_name,
                    ),
                    methodCard=method_card,
                    similarQuestion=similar,
                )
            )

        if not items:
            return _empty_rescue(review_id=review_id, session_id=session_id)
        return XingceRescue(
            reviewId=review_id,
            sessionId=session_id,
            items=items,
            summary=_summary(len(items)),
        )


def _focus_label(display_path: str) -> str:
    """从 display_path 取最具体的末段作为人话解释里的考点名。"""
    segments = [segment for segment in display_path.split("/") if segment.strip()]
    return segments[-1] if segments else "这道题"


def _summary(item_count: int) -> str:
    return (
        f"这次有 {item_count} 道题可以先补一补，方法卡看懂后再做同类题。"
    )


def _empty_rescue(*, review_id: str, session_id: str) -> XingceRescue:
    return XingceRescue(
        reviewId=review_id,
        sessionId=session_id,
        items=[],
        summary="这组题没有需要即时补的知识点或方法，继续保持你的节奏。",
    )
