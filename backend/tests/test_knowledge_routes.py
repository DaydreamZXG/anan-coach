from __future__ import annotations

from fastapi.testclient import TestClient

from app.domains.knowledge.router import get_knowledge_service
from app.domains.knowledge.schemas import (
    QuestionDetail,
    QuestionExplanation,
    QuestionOccurrence,
    QuestionOption,
    QuestionSearchResponse,
    QuestionSummary,
    QuestionTag,
)
from app.domains.knowledge.service import QuestionNotFoundError
from app.main import create_app


class FakeKnowledgeService:
    def get_question(self, question_id: str) -> QuestionDetail:
        if question_id == "missing":
            raise QuestionNotFoundError(question_id)
        return QuestionDetail(
            question_id="cq_31cdbc7fb615d7e849c02542",
            domain="objective",
            status="canonical",
            module_name="判断推理",
            question_type="逻辑判断",
            display_level1="判断推理",
            display_level2="逻辑判断",
            display_level3="削弱题型",
            knowledge_family="拆桥",
            teaching_point="削弱论证链条",
            micro_skill="识别核心桥梁",
            stem="紫杉醇是治疗癌症的明星药物，因此有人认为吃香榧可以治疗癌症。",
            stem_html=None,
            answer="B",
            material_text=None,
            material_html=None,
            difficulty=None,
            visual_required=False,
            needs_multimodal=False,
            shared_context_required=False,
            occurrence_count=11,
            occurrence_province_count=11,
            first_year=2026,
            latest_year=2026,
            source_member_count=11,
            options=[
                QuestionOption(option_index=1, label="A", option_text="A、无关项"),
                QuestionOption(option_index=2, label="B", option_text="B、含量微乎其微"),
            ],
            explanation=QuestionExplanation(
                preferred_analysis="答案B。B项切断食用香榧与治疗作用之间的联系。",
                original_analysis=None,
                stored_analysis=None,
                analysis_source="ai_rewrite",
                analysis_status="ai_rewrite_accepted",
                prefer_ai_analysis=True,
                needs_review=False,
            ),
            occurrences=[
                QuestionOccurrence(
                    occurrence_id="qbo_09db7fcfce1442013bdab65cba02",
                    source_question_id="source-1",
                    source_dataset_id="fenbi",
                    province="吉林",
                    year=2026,
                    number=76,
                    section=None,
                    paper_title="2026年吉林省公务员录用考试《行测》题（网友回忆版）",
                    source_path=None,
                    rank=1,
                )
            ],
            tags=[
                QuestionTag(
                    tag_type="knowledge_point",
                    tag_value="拆桥",
                    sort_order=2,
                    evidence_link_count=11,
                )
            ],
            knowledge_chunks=[],
            assets=[],
        )

    def search_questions(self, **kwargs: object) -> QuestionSearchResponse:
        return QuestionSearchResponse(
            limit=int(kwargs["limit"]),
            items=[
                QuestionSummary(
                    question_id="cq_31cdbc7fb615d7e849c02542",
                    domain="objective",
                    module_name=kwargs.get("module_name") or "判断推理",
                    question_type="逻辑判断",
                    stem="紫杉醇是治疗癌症的明星药物。",
                    answer="B",
                    visual_required=False,
                    has_explanation=True,
                    occurrence_count=11,
                    latest_year=2026,
                )
            ],
        )


def make_client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_knowledge_service] = lambda: FakeKnowledgeService()
    return TestClient(app)


def test_get_question_contract_prefers_ai_explanation_and_provenance() -> None:
    response = make_client().get(
        "/api/v1/knowledge/questions/cq_31cdbc7fb615d7e849c02542"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["questionId"] == "cq_31cdbc7fb615d7e849c02542"
    assert body["domain"] == "objective"
    assert body["answer"] == "B"
    assert body["explanation"]["analysisSource"] == "ai_rewrite"
    assert body["explanation"]["preferAiAnalysis"] is True
    assert body["occurrenceCount"] == 11
    assert body["occurrences"][0]["province"] == "吉林"
    assert body["tags"][0]["tagValue"] == "拆桥"
    assert "source_qbank" not in str(body)
    assert "canonical_qbank" not in str(body)


def test_get_question_404_contract() -> None:
    response = make_client().get("/api/v1/knowledge/questions/missing")
    assert response.status_code == 404
    assert response.json()["detail"] == "Question not found"


def test_search_questions_contract() -> None:
    response = make_client().get(
        "/api/v1/knowledge/questions/search",
        params={"domain": "objective", "moduleName": "判断推理", "limit": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 5
    assert body["items"][0]["moduleName"] == "判断推理"
    assert body["items"][0]["hasExplanation"] is True
