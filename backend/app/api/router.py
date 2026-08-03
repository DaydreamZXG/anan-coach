from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import router as app_router
from app.domains.ability_estimation.router import router as ability_estimation_router
from app.domains.answer_ingestion.router import router as answer_ingestion_router
from app.domains.planning.router import router as planning_router
from app.domains.auth.router import router as auth_router, wechat_router
from app.domains.calibration.router import router as calibration_router
from app.domains.coach_notifications.router import router as coach_notifications_router
from app.domains.companion.router import (
    router as companion_router,
    v2_router as companion_v2_router,
)
from app.domains.commerce.router import router as commerce_router
from app.domains.commerce.stage8_router import router as stage8_commerce_router
from app.domains.community_method_cards.router import (
    router as community_method_cards_router,
)
from app.domains.community_rewards.router import router as community_rewards_router
from app.domains.docket.router import router as docket_router
from app.domains.data_rights.router import router as data_rights_router
from app.domains.essay.router import router as essay_router
from app.domains.essay_intelligence.router import router as essay_intelligence_router
from app.domains.external_ingestion.router import router as external_ingestion_router
from app.domains.feed.router import router as feed_router
from app.domains.knowledge.router import router as knowledge_router
from app.domains.learning_packages.router import router as learning_packages_router
from app.domains.learning_media.router import router as learning_media_router
from app.domains.learning_brain.router import router as learning_brain_router
from app.domains.memory.router import router as memory_router
from app.domains.math_ink.router import router as math_ink_router
from app.domains.method_cards.router import router as method_cards_router
from app.domains.mock_exam.router import (
    router as mock_exam_router,
    v2_router as mock_exam_v2_router,
)
from app.domains.onboarding.router import (
    router as onboarding_router,
    v2_router as onboarding_v2_router,
)
from app.domains.operations.router import router as operations_router
from app.domains.position_decision.router import (
    learner_target_router,
    router as position_decision_router,
)
from app.domains.processor_disclosure.router import (
    router as processor_disclosure_router,
)
from app.domains.positions.router import router as positions_router
from app.domains.practice.router import router as practice_router
from app.domains.progression.router import router as progression_router
from app.domains.private_knowledge.router import router as private_knowledge_router
from app.domains.qualification.router import router as qualification_router
from app.domains.rank_system.router import router as rank_router
from app.domains.review_session.router import router as review_session_router
from app.domains.reporting.router import router as reporting_router
from app.domains.retake.router import router as retake_router
from app.domains.taskstream.router import router as taskstream_router
from app.domains.task_help.router import router as task_help_router
from app.domains.task_editing.router import router as task_editing_router
from app.domains.xingce_learning.briefing.router import router as xingce_briefing_router
from app.domains.xfyun.router import router as xfyun_router
from app.internal_services.router import router as internal_services_router

router = APIRouter()
router.include_router(app_router)
router.include_router(ability_estimation_router)
router.include_router(answer_ingestion_router)
router.include_router(planning_router)
router.include_router(auth_router)
router.include_router(wechat_router)
router.include_router(calibration_router)
router.include_router(coach_notifications_router)
router.include_router(companion_router)
router.include_router(companion_v2_router)
router.include_router(commerce_router)
router.include_router(stage8_commerce_router)
router.include_router(community_method_cards_router)
router.include_router(community_rewards_router)
router.include_router(docket_router)
router.include_router(data_rights_router)
router.include_router(knowledge_router)
router.include_router(learning_packages_router)
router.include_router(learning_media_router)
router.include_router(learning_brain_router)
router.include_router(memory_router)
router.include_router(math_ink_router)
router.include_router(method_cards_router)
router.include_router(mock_exam_router)
router.include_router(mock_exam_v2_router)
router.include_router(onboarding_router)
router.include_router(onboarding_v2_router)
router.include_router(operations_router)
router.include_router(position_decision_router)
router.include_router(learner_target_router)
router.include_router(processor_disclosure_router)
router.include_router(positions_router)
router.include_router(taskstream_router)
router.include_router(task_help_router)
router.include_router(task_editing_router)
router.include_router(xingce_briefing_router)
router.include_router(xfyun_router)
router.include_router(practice_router)
router.include_router(progression_router)
router.include_router(private_knowledge_router)
router.include_router(essay_router)
router.include_router(essay_intelligence_router)
router.include_router(external_ingestion_router)
router.include_router(feed_router)
router.include_router(rank_router)
router.include_router(review_session_router)
router.include_router(reporting_router)
router.include_router(retake_router)
router.include_router(qualification_router)
router.include_router(internal_services_router)
