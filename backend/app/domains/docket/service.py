from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from app.contracts.guokao_xingce import GUOKAO_XINGCE_MODULE_NAMES
from app.domains.docket.repository import DocketRepository
from app.domains.docket.schemas import (
    CourtStateSnapshot,
    DocketCard,
    DocketOpenCourtResponse,
    DocketOption,
    RankSnapshot,
)
from app.domains.knowledge.schemas import QuestionDetail
from app.domains.knowledge.service import KnowledgeService
from app.domains.rank_system.schemas import OfficialRankProfile
from app.domains.progression.service import (
    ProgressionService,
    court_state_from_profile,
)


OPEN_COURT_CARD_COUNT = 10

_MODULE_WORLD: dict[str, tuple[str, str, str]] = {
    "政治理论": (
        "朝政理论奏报",
        "研判朝政纲要",
        "治国理政要义交织，请大人辨明理论与政策依据。",
    ),
    "资料分析": (
        "州府钱粮急件",
        "核算全县钱粮",
        "户房递来钱粮簿册，须速判增减盈亏。",
    ),
    "判断推理": (
        "州府疑案",
        "堪破州府冤案",
        "刑名卷宗互有牵连，请大人辨明虚实。",
    ),
    "常识判断": (
        "民情风俗奏报",
        "体察民间风俗",
        "乡里奏报纷至，考验大人见闻与常识。",
    ),
    "言语理解与表达": (
        "诏令释义",
        "校阅诏令文义",
        "文书句读暗藏机锋，须辨其本意。",
    ),
    "数量关系": (
        "工程筹算",
        "筹算工役粮饷",
        "工役、路程、钱粮相互牵扯，请大人速算定夺。",
    ),
}

if set(_MODULE_WORLD) != set(GUOKAO_XINGCE_MODULE_NAMES):
    raise RuntimeError("docket presentation must cover the current guokao modules")


class DocketService:
    def __init__(
        self,
        *,
        repository: DocketRepository,
        knowledge_service: KnowledgeService,
        progression_service: ProgressionService,
    ) -> None:
        self._repository = repository
        self._knowledge_service = knowledge_service
        self._progression_service = progression_service

    def open_court(self, *, user_id: str, now: datetime | None = None) -> DocketOpenCourtResponse:
        opened_at = now or datetime.now(UTC)
        docket_date = opened_at.date()
        rank_profile = self._progression_service.get_profile(user_id=user_id)
        docket = self._repository.get_active_docket_for_date(
            user_id=user_id,
            docket_date=docket_date,
        )
        if docket is not None:
            cards = self._repository.list_cards(docket_id=docket.docket_id)
            if cards:
                return DocketOpenCourtResponse(
                    docket_id=docket.docket_id,
                    opened_at=opened_at,
                    companion_line=_companion_line(
                        rank_profile=rank_profile,
                        card_count=len(cards),
                    ),
                    rank_snapshot=_rank_snapshot(rank_profile),
                    court_state=_court_state(rank_profile),
                    cards=cards,
                )

        docket = self._repository.create_docket(
            user_id=user_id,
            docket_date=docket_date,
        )
        selection = self._knowledge_service.select_weighted_xingce_practice(
            exam_scope="guokao",
            limit=OPEN_COURT_CARD_COUNT,
        )
        cards = [
            self._card_from_question(
                user_id=user_id,
                docket_date=docket_date,
                index=index,
                question=self._knowledge_service.get_question(summary.question_id),
            )
            for index, summary in enumerate(selection.items, start=1)
        ]
        self._repository.create_cards(docket_id=docket.docket_id, cards=cards)
        return DocketOpenCourtResponse(
            docket_id=docket.docket_id,
            opened_at=opened_at,
            companion_line=_companion_line(rank_profile=rank_profile, card_count=len(cards)),
            rank_snapshot=_rank_snapshot(rank_profile),
            court_state=_court_state(rank_profile),
            cards=cards,
        )

    def _card_from_question(
        self,
        *,
        user_id: str,
        docket_date: date,
        index: int,
            question: QuestionDetail,
    ) -> DocketCard:
        title, world_label, narrative = _world_for_module(question.module_name)
        return DocketCard(
            card_id=str(uuid4()),
            type="daily_affair",
            track="xingce",
            question_id=question.question_id,
            module_name=question.module_name,
            world_label=world_label,
            title=title,
            narrative=narrative,
            stem=question.stem,
            stem_html=question.stem_html,
            material_text=question.material_text,
            material_html=question.material_html,
            options=[
                DocketOption(
                    label=option.label,
                    text=option.option_text,
                    html=option.option_html,
                )
                for option in question.options
            ],
            source_summary=_source_summary(question),
            occurrence_count=question.occurrence_count,
            latest_year=question.latest_year,
        )


def _world_for_module(module_name: str) -> tuple[str, str, str]:
    return _MODULE_WORLD[module_name]


def _rank_snapshot(rank: OfficialRankProfile) -> RankSnapshot:
    return RankSnapshot(
        rank_code=rank.rank_code,
        rank_level=rank.rank_level,
        rank_name=rank.rank_name,
        xp=rank.xp,
        fame=rank.fame,
        stamina=rank.stamina,
        consecutive_days=rank.consecutive_days,
        ui_theme=rank.ui_theme,
        unlock_features=rank.unlock_features,
        next_rank_name=rank.next_rank_name,
        xp_to_next=rank.xp_to_next,
    )


def _court_state(rank: OfficialRankProfile) -> CourtStateSnapshot:
    court_state = court_state_from_profile(rank)
    return CourtStateSnapshot(
        popular_sentiment=court_state.popular_sentiment,
        stability=court_state.stability,
        demerits=court_state.demerits,
        risk_level=court_state.risk_level,
        background_tone=court_state.background_tone,
    )


def _source_summary(question: QuestionDetail) -> str:
    if not question.occurrences:
        return "题源：product.questions"
    first = question.occurrences[0]
    if first.paper_title:
        return f"题源：{first.paper_title}"
    if first.province and first.year:
        return f"题源：{first.year} {first.province}"
    return "题源：product.questions"


def _companion_line(*, rank_profile: OfficialRankProfile, card_count: int) -> str:
    return (
        f"大人今日以{rank_profile.rank_name}开衙，臣已从近年国考题库调来"
        f"{card_count}封急件，先批钱粮，再论升迁。"
    )
