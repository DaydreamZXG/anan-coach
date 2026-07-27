from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.deps import get_auth_repository
from app.domains.auth.repository import AuthenticatedUser, token_hash_for_tests
from app.domains.docket.repository import DocketRecord
from app.domains.docket.router import get_docket_service
from app.domains.docket.service import DocketService
from app.domains.knowledge.schemas import (
    QuestionDetail,
    QuestionExplanation,
    QuestionOccurrence,
    QuestionOption,
    QuestionSummary,
    QuestionTag,
)
from app.domains.knowledge.service import WeightedPracticeSelection
from app.domains.rank_system.schemas import OfficialRankProfile
from app.main import create_app
from tests.knowledge_map_vocabulary_helpers import (
    activate_knowledge_release,
    bind_question_to_release_node,
    seed_released_module_vocabulary,
)


class FakeAuthRepository:
    def get_user_by_access_token(self, access_token: str) -> AuthenticatedUser | None:
        return AuthenticatedUser(
            user_id="user-docket",
            learner_key="user-docket",
            display_name="张大人",
            email=None,
        )


class FakeKnowledgeService:
    def select_weighted_xingce_practice(
        self,
        *,
        exam_scope: str,
        limit: int,
        module_name: str | None = None,
    ) -> WeightedPracticeSelection:
        items = [
            QuestionSummary(
                question_id=f"dq_{index:02d}",
                domain="objective",
                module_name="资料分析",
                question_type="资料分析",
                stem=f"第 {index} 道资料分析真题。",
                answer="B",
                visual_required=False,
                has_explanation=True,
                occurrence_count=10 + index,
                latest_year=2026,
            )
            for index in range(1, limit + 1)
        ]
        return WeightedPracticeSelection(
            release_id="00000000-0000-0000-0000-000000000102",
            release_version="knowledge-map@docket-test",
            source_data_version="questions@docket-test",
            fresh_until=datetime(2099, 1, 1, tzinfo=UTC),
            node_id="km_money",
            exam_scope=exam_scope,
            track="xingce",
            module_name="资料分析",
            node_type="knowledge_point",
            tag_type="knowledge_point",
            tag_value="增长率",
            display_path="行测/资料分析/增长率",
            window_start_year=2022,
            window_end_year=2026,
            occurrence_count=66,
            weight=0.18,
            items=items,
        )

    def get_question(self, question_id: str) -> QuestionDetail:
        return QuestionDetail(
            question_id=question_id,
            domain="objective",
            status="active",
            module_name="资料分析",
            question_type="资料分析",
            display_level1="资料分析",
            display_level2="文字资料",
            display_level3="增长率",
            knowledge_family="文字资料",
            teaching_point="增长率",
            micro_skill="增长率",
            stem=f"{question_id}：某县粮仓收支同比增长，问增速变化。",
            stem_html=(
                f"<p>{question_id}：某县粮仓<u>&nbsp;&nbsp;&nbsp;&nbsp;</u>"
                "同比增长，问增速变化。</p>"
            ),
            answer="B",
            material_text="材料：本年收入 1200 万石，上年收入 1000 万石。",
            material_html="<p>材料：本年收入 1200 万石，上年收入 1000 万石。</p>",
            difficulty=None,
            visual_required=False,
            needs_multimodal=False,
            shared_context_required=False,
            occurrence_count=12,
            occurrence_province_count=8,
            first_year=2022,
            latest_year=2026,
            source_member_count=1,
            options=[
                QuestionOption(option_index=1, label="A", option_text="10%", option_html="<p>10%</p>"),
                QuestionOption(option_index=2, label="B", option_text="20%", option_html="<p>20%</p>"),
                QuestionOption(option_index=3, label="C", option_text="30%"),
                QuestionOption(option_index=4, label="D", option_text="40%"),
            ],
            explanation=QuestionExplanation(
                preferred_analysis="本题用增长率公式。",
                original_analysis=None,
                stored_analysis=None,
                analysis_source="ai_rewrite",
                analysis_status="ai_rewrite_accepted",
                prefer_ai_analysis=True,
                needs_review=False,
            ),
            occurrences=[
                QuestionOccurrence(
                    occurrence_id=f"occ_{question_id}",
                    source_question_id=f"source_{question_id}",
                    source_dataset_id="fenbi",
                    province="国考",
                    year=2026,
                    number=101,
                    section="资料分析",
                    paper_title="2026年国家公务员录用考试《行测》题（测试卷）",
                    source_path=None,
                    rank=1,
                )
            ],
            tags=[
                QuestionTag(
                    tag_type="knowledge_point",
                    tag_value="增长率",
                    sort_order=1,
                    evidence_link_count=1,
                )
            ],
            knowledge_chunks=[],
            assets=[],
        )


class FakeProgressionService:
    def get_profile(self, *, user_id: str) -> OfficialRankProfile:
        return OfficialRankProfile(
            user_id=user_id,
            rank_code="nine_pin_xianling",
            rank_level=9,
            rank_name="九品县令",
            xp=120,
            fame=50,
            stamina=88,
            consecutive_days=2,
            demerits=0,
            ui_theme="bamboo",
            next_rank_name="八品县丞",
            next_rank_xp_threshold=500,
            xp_to_next=380,
            unlock_features=[],
        )


class FakeDocketRepository:
    def __init__(self) -> None:
        self._dockets: dict[tuple[str, object], DocketRecord] = {}
        self._cards: dict[str, list[object]] = {}

    def get_active_docket_for_date(
        self,
        *,
        user_id: str,
        docket_date: object,
    ) -> DocketRecord | None:
        return self._dockets.get((user_id, docket_date))

    def create_docket(
        self,
        *,
        user_id: str,
        docket_date: object,
    ) -> DocketRecord:
        now = datetime.now(UTC)
        docket = DocketRecord(
            docket_id=str(uuid4()),
            user_id=user_id,
            docket_date=docket_date,  # type: ignore[arg-type]
            status="active",
            created_at=now,
            updated_at=now,
        )
        self._dockets[(user_id, docket_date)] = docket
        return docket

    def list_cards(self, *, docket_id: str) -> list[object]:
        return list(self._cards.get(docket_id, []))

    def create_cards(self, *, docket_id: str, cards: list[object]) -> None:
        self._cards[docket_id] = list(cards)


def make_client() -> TestClient:
    app = create_app()
    repository = FakeDocketRepository()
    app.dependency_overrides[get_auth_repository] = lambda: FakeAuthRepository()
    app.dependency_overrides[get_docket_service] = lambda: DocketService(  # type: ignore[arg-type]
        repository=repository,
        knowledge_service=FakeKnowledgeService(),
        progression_service=FakeProgressionService(),
    )
    return TestClient(app)


def test_open_court_returns_ten_traceable_daily_affairs() -> None:
    response = make_client().post(
        "/api/v1/dockets/open-court",
        headers={"Authorization": "Bearer docket-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["docketId"]
    assert body["sourceGrounding"] == "product.questions + product.knowledge_map_nodes"
    assert len(body["cards"]) == 10
    first = body["cards"][0]
    assert first["type"] == "daily_affair"
    assert first["track"] == "xingce"
    assert first["interaction"] == "swipe_verdict"
    assert first["sourceGrounding"] == "product.questions"
    assert first["questionId"] == "dq_01"
    assert first["moduleName"] == "资料分析"
    assert first["worldLabel"] == "核算全县钱粮"
    assert first["title"] == "州府钱粮急件"
    assert first["stemHtml"] == (
        "<p>dq_01：某县粮仓<u>&nbsp;&nbsp;&nbsp;&nbsp;</u>"
        "同比增长，问增速变化。</p>"
    )
    assert first["materialHtml"] == "<p>材料：本年收入 1200 万石，上年收入 1000 万石。</p>"
    assert first["options"][0] == {"label": "A", "text": "10%", "html": "<p>10%</p>"}
    assert first["options"][1] == {"label": "B", "text": "20%", "html": "<p>20%</p>"}
    assert "answer" not in first
    assert "correctAnswer" not in first


def test_open_court_reuses_same_day_docket_identity() -> None:
    client = make_client()

    first = client.post(
        "/api/v1/dockets/open-court",
        headers={"Authorization": "Bearer docket-token"},
    ).json()
    second = client.post(
        "/api/v1/dockets/open-court",
        headers={"Authorization": "Bearer docket-token"},
    ).json()

    assert second["docketId"] == first["docketId"]
    assert [card["cardId"] for card in second["cards"]] == [
        card["cardId"] for card in first["cards"]
    ]


def test_open_court_includes_rank_and_court_state_snapshots() -> None:
    response = make_client().post(
        "/api/v1/dockets/open-court",
        headers={"Authorization": "Bearer docket-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["rankSnapshot"] == {
        "rankCode": "nine_pin_xianling",
        "rankLevel": 9,
        "rankName": "九品县令",
        "xp": 120,
        "fame": 50,
        "stamina": 88,
        "consecutiveDays": 2,
        "uiTheme": "bamboo",
        "unlockFeatures": [],
        "nextRankName": "八品县丞",
        "xpToNext": 380,
    }
    assert body["courtState"]["popularSentiment"] == 50
    assert body["courtState"]["riskLevel"] == "stable"
    assert body["courtState"]["backgroundTone"] == "paper"
    assert "大人" in body["companionLine"]


def test_open_court_persists_and_reuses_daily_docket(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="docket-db-token")
    _seed_docket_qbank(db_session_factory)
    client = _make_db_client(db_session_factory)

    first = client.post(
        "/api/v1/dockets/open-court",
        headers={"Authorization": "Bearer docket-db-token"},
    )

    assert first.status_code == 200
    first_body = first.json()
    assert len(first_body["cards"]) == 10
    with db_session_factory() as session:
        counts = session.execute(
            text(
                """
                select
                  (select count(*) from learner.dockets where user_id = cast(:user_id as uuid)) as dockets,
                  (select count(*) from learner.docket_cards dc
                    join learner.dockets d on d.docket_id = dc.docket_id
                    where d.user_id = cast(:user_id as uuid)) as cards
                """
            ),
            {"user_id": user_id},
        ).mappings().one()
    assert int(counts["dockets"]) == 1
    assert int(counts["cards"]) == 10

    with db_session_factory() as session:
        session.execute(
            text(
                """
                update product.questions
                set stem_html = '<p>live drift</p>',
                    material_html = '<p>live material drift</p>'
                where question_id = 'docket_q_01';
                update product.question_options
                set option_text = 'A、live option drift',
                    option_html = '<p>live option drift</p>'
                where question_id = 'docket_q_01'
                  and option_index = 1
                """
            )
        )
        session.commit()

    second = client.post(
        "/api/v1/dockets/open-court",
        headers={"Authorization": "Bearer docket-db-token"},
    )

    assert second.status_code == 200
    second_body = second.json()
    assert second_body["docketId"] == first_body["docketId"]
    assert [card["cardId"] for card in second_body["cards"]] == [
        card["cardId"] for card in first_body["cards"]
    ]
    assert first_body["cards"][0]["options"][0] == {
        "label": "A",
        "text": "10%",
        "html": "10%",
    }
    assert second_body["cards"][0]["options"][0] == first_body["cards"][0]["options"][0]
    assert second_body["cards"][0]["stemHtml"] == first_body["cards"][0]["stemHtml"]
    assert second_body["cards"][0]["materialHtml"] == first_body["cards"][0]["materialHtml"]
    with db_session_factory() as session:
        counts = session.execute(
            text(
                """
                select
                  (select count(*) from learner.dockets where user_id = cast(:user_id as uuid)) as dockets,
                  (select count(*) from learner.docket_cards dc
                    join learner.dockets d on d.docket_id = dc.docket_id
                    where d.user_id = cast(:user_id as uuid)) as cards
                """
            ),
            {"user_id": user_id},
        ).mappings().one()
    assert int(counts["dockets"]) == 1
    assert int(counts["cards"]) == 10


def _make_db_client(db_session_factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override_db_session() -> Iterator[Session]:
        with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app)


def _seed_user(db_session_factory: sessionmaker[Session], *, token: str) -> str:
    user_id = str(uuid4())
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values (cast(:user_id as uuid), '张大人')
                """
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id,
                  user_id,
                  session_token_hash,
                  device_id,
                  expires_at
                ) values (
                  cast(:session_id as uuid),
                  cast(:user_id as uuid),
                  :session_token_hash,
                  'ios-test',
                  :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": user_id,
                "session_token_hash": token_hash_for_tests(token),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
        session.commit()
    return user_id


def _seed_docket_qbank(db_session_factory: sessionmaker[Session]) -> None:
    release_id = str(uuid4())
    now = datetime.now(UTC)
    with db_session_factory() as session:
        seed_released_module_vocabulary(
            session,
            version="vocab@docket-test-v1",
        )
        session.execute(
            text(
                """
                insert into product.knowledge_map_releases (
                  release_id,
                  release_version,
                  exam_scope,
                  track,
                  source_data_version,
                  vocab_version,
                  generator_version,
                  source_fingerprint,
                  generated_at,
                  fresh_until,
                  status,
                  node_count,
                  activated_at
                ) values (
                  cast(:release_id as uuid),
                  :release_version,
                  'guokao',
                  'xingce',
                  'docket-question-bank@test-v1',
                  'vocab@docket-test-v1',
                  'docket-fixture-generator@v1',
                  :source_fingerprint,
                  :generated_at,
                  :fresh_until,
                  'draft',
                  0,
                  null
                )
                """
            ),
            {
                "release_id": release_id,
                "release_version": f"knowledge-map@docket-{release_id}",
                "source_fingerprint": release_id.replace("-", "") * 2,
                "generated_at": now - timedelta(days=1),
                "fresh_until": now + timedelta(days=30),
            },
        )
        session.execute(
            text(
                """
                insert into product.knowledge_map_nodes (
                  release_id,
                  node_id,
                  exam_scope,
                  track,
                  module_name,
                  node_type,
                  tag_type,
                  tag_value,
                  display_path,
                  support_status,
                  near5_window_start_year,
                  near5_window_end_year,
                  near5_weight,
                  near5_occurrence_count,
                  stable_for_old_questions,
                  method_card_ids,
                  evidence,
                  status
                ) values (
                  cast(:release_id as uuid),
                  'km_docket_money',
                  'guokao',
                  'xingce',
                  '资料分析',
                  'knowledge_point',
                  'knowledge_point',
                  '增长率',
                  '行测/资料分析/增长率',
                  'text_supported',
                  2022,
                  2026,
                  0.5,
                  66,
                  true,
                  '[]'::jsonb,
                  '{}'::jsonb,
                  'published'
                )
                """
            ),
            {"release_id": release_id},
        )
        for index in range(1, 11):
            question_id = f"docket_q_{index:02d}"
            year = 2026 if index <= 7 else 2021
            session.execute(
                text(
                    """
                    insert into product.questions (
                      question_id,
                      domain,
                      status,
                      module_name,
                      question_type,
                      display_level1,
                      display_level2,
                      display_level3,
                      knowledge_family,
                      teaching_point,
                      micro_skill,
                      stem,
                      stem_html,
                      material_text,
                      material_html,
                      answer,
                      visual_required,
                      latest_year,
                      occurrence_count
                    ) values (
                      :question_id,
                      'objective',
                      'active',
                      '资料分析',
                      '资料分析',
                      '资料分析',
                      '文字资料',
                      '增长率',
                      '文字资料',
                      '增长率',
                      '增长率',
                      :stem,
                      :stem_html,
                      '材料：本年收入 1200 万石，上年收入 1000 万石。',
                      '<p>材料：本年收入 1200 万石，上年收入 1000 万石。</p>',
                      'B',
                      false,
                      :latest_year,
                      12
                    )
                    """
                ),
                {
                    "question_id": question_id,
                    "stem": f"第 {index} 道资料分析真题。",
                    "stem_html": (
                        f"<p>第 {index} 道资料分析<u>&nbsp;&nbsp;&nbsp;&nbsp;</u>真题。</p>"
                    ),
                    "latest_year": year,
                },
            )
            for option_index, (label, text_value) in enumerate(
                [("A", "10%"), ("B", "20%"), ("C", "30%"), ("D", "40%")],
                start=1,
            ):
                session.execute(
                    text(
                        """
                        insert into product.question_options (
                          question_id,
                          option_index,
                          label,
                          option_text,
                          option_html
                        ) values (
                          :question_id,
                          :option_index,
                          :label,
                          :option_text,
                          :option_html
                        )
                        """
                    ),
                    {
                        "question_id": question_id,
                        "option_index": option_index,
                        "label": label,
                        "option_text": f"{label}、{text_value}",
                        "option_html": text_value,
                    },
                )
            session.execute(
                text(
                    """
                    insert into product.question_tags (
                      question_tag_id,
                      question_id,
                      tag_type,
                      tag_value
                    ) values (
                      :tag_id,
                      :question_id,
                      'knowledge_point',
                      '增长率'
                    )
                    """
                ),
                {"tag_id": f"tag_{question_id}", "question_id": question_id},
            )
            session.execute(
                text(
                    """
                    insert into product.question_occurrences (
                      occurrence_id,
                      question_id,
                      province,
                      year,
                      paper_title,
                      canonical_occurrence_rank
                    ) values (
                      :occurrence_id,
                      :question_id,
                      '国考',
                      :year,
                      :paper_title,
                      1
                    )
                    """
                ),
                {
                    "occurrence_id": f"occ_{question_id}",
                    "question_id": question_id,
                    "year": year,
                    "paper_title": f"{year}年国家公务员录用考试《行测》题（测试卷）",
                },
            )
            bind_question_to_release_node(
                session,
                release_id=release_id,
                node_id="km_docket_money",
                question_id=question_id,
                latest_recent_exam_year=year if year >= 2022 else None,
                recent_occurrence_count=1 if year >= 2022 else 0,
                latest_old_exam_year=year if year < 2022 else None,
                old_occurrence_count=1 if year < 2022 else 0,
            )
        activate_knowledge_release(
            session,
            release_id=release_id,
            activated_at=now - timedelta(hours=23),
        )
        session.commit()
