from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.commerce.catalog import FEATURE_QUOTAS, product_ids
from app.domains.knowledge.router import get_knowledge_service
from app.domains.knowledge.service import KnowledgeService
from app.domains.memory.deps import get_memory_service
from app.domains.memory.repository import (
    LearnerWorkbenchObjectRecord,
    LearnerWorkbenchSectionRecord,
)
from app.domains.memory.service import LearnerMemoryService
from app.domains.xingce_learning.real_attempt.router import router as xingce_router

router = APIRouter()
router.include_router(xingce_router)


@router.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_slug,
        "environment": settings.environment,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/v1/bootstrap")
def bootstrap(
    knowledge_service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> dict[str, object]:
    settings = get_settings()
    stats = knowledge_service.get_product_stats()
    return {
        "app": {
            "name": settings.app_name,
            "slug": settings.app_slug,
            "bundleId": settings.bundle_id,
            "appleId": settings.app_store_apple_id,
            "apiBaseUrl": settings.api_base_url,
        },
        "storage": {
            "bucket": settings.tos_bucket,
            "region": settings.tos_region,
            "publicReads": False,
        },
        "questionBank": {
            "canonicalQuestions": stats.canonical_questions,
            "objectiveQuestions": stats.objective_questions,
            "essayQuestions": stats.essay_questions,
            "explanations": stats.explanations,
            "knowledgeChunks": stats.knowledge_chunks,
        },
        "features": {
            "appleSignIn": True,
            "iap": True,
            "ipadOptimized": True,
            "announcements": False,
            "openWebResearch": False,
            "companion": True,
            "externalWrongQuestionIntake": True,
            "weeklyMockExam": True,
            "abilityCalibration": True,
        },
        "navigation": {
            "firstLevelSections": [
                {"id": "today", "title": "今日"},
                {"id": "workbench", "title": "工作台"},
                {"id": "profile", "title": "我的"},
            ]
        },
        "iap": {
            "products": product_ids()
        },
        "ai": {
            "studyPlanRegeneration": {
                "freePerDay": _quota_limit("study_plan_regeneration", "free"),
                "proPerMonth": 10,
            },
            "freeQuota": {
                "photoExplainPerDay": _quota_limit("photo_explain", "free"),
                "textExplainPerDay": _quota_limit("companion_ask", "free"),
                "positionNarrativePerDay": _quota_limit("position_narrative", "free"),
                "essayReviewPerDay": _quota_limit("essay_review", "free"),
            },
        },
    }


@router.get("/api/v1/workbench/preview")
def workbench_preview(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    knowledge_service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    memory_service: Annotated[LearnerMemoryService, Depends(get_memory_service)],
) -> dict[str, object]:
    stats = knowledge_service.get_product_stats()
    sections = memory_service.list_workbench_sections(user_id=current_user.user_id)
    return {
        "headline": "你的公考学习工作台",
        "sourceBase": {
            "canonicalQuestions": stats.canonical_questions,
            "knowledgeChunks": stats.knowledge_chunks,
            "positionRecords": stats.position_records,
            "openWebResearch": False,
        },
        "sections": [_section_response(section) for section in sections],
    }


def _section_response(section: LearnerWorkbenchSectionRecord) -> dict[str, object]:
    return {
        "id": section.id,
        "title": section.title,
        "objects": [_object_response(item) for item in section.objects],
    }


def _object_response(item: LearnerWorkbenchObjectRecord) -> dict[str, object]:
    return {
        "id": item.id,
        "type": item.type,
        "title": item.title,
        "subtitle": item.subtitle,
        "sourceCount": item.source_count,
        "payload": item.payload,
    }


def _quota_limit(feature: str, tier: str) -> int:
    quota = next(item for item in FEATURE_QUOTAS if item.feature == feature)
    return quota.pro_limit if tier == "pro" else quota.free_limit
