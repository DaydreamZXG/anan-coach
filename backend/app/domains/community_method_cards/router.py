from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.community_method_cards.candidate_provider import (
    CandidateProviderConfigurationError,
)
from app.domains.community_method_cards.deps import get_community_method_card_service
from app.domains.community_method_cards.effects import EffectPolicyUnavailable
from app.domains.community_method_cards.moderation import ReviewPolicyUnavailable
from app.domains.community_method_cards.schemas import (
    CandidateConfirm,
    CardSubmit,
    ConfirmReceipt,
    ContributionsResponse,
    DelayedVerificationReceipt,
    DelayedVerificationRecord,
    DiscoveryRequest,
    DiscoveryResponse,
    DraftCreate,
    DraftReceipt,
    ExposureCreate,
    ExposureReceipt,
    FeedbackReceipt,
    FeedbackRecord,
    PublicCardDetail,
    ReportCreate,
    ReportReceipt,
    RetireCard,
    RetireReceipt,
    ReviewOutcome,
    StructureCandidate,
    TrialComplete,
    TrialReceipt,
    Workspace,
)
from app.domains.community_method_cards.service import (
    CommunityMethodCardError,
    CommunityMethodCardService,
)
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_error_response,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/community-method-cards",
    tags=["community-method-cards"],
    responses=COMMON_API_ERROR_RESPONSES,
)

T = TypeVar("T")


@router.post("/drafts", response_model=PublicApiEnvelope[DraftReceipt])
def create_draft(
    command: DraftCreate,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        CommunityMethodCardService,
        Depends(get_community_method_card_service),
    ],
) -> PublicApiEnvelope[DraftReceipt] | JSONResponse:
    return _execute(
        request,
        response,
        lambda: service.create_draft(
            author_user_id=current_user.user_id,
            command=command,
        ),
    )


@router.get("/contributions", response_model=PublicApiEnvelope[ContributionsResponse])
def get_contributions(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        CommunityMethodCardService,
        Depends(get_community_method_card_service),
    ],
    cursor: Annotated[str | None, Query()] = None,
) -> PublicApiEnvelope[ContributionsResponse] | JSONResponse:
    if cursor is not None:
        return _error(request, CommunityMethodCardError("cursor_expired"))
    return _execute(
        request,
        response,
        lambda: service.contributions(author_user_id=current_user.user_id),
    )


@router.post("/discovery", response_model=PublicApiEnvelope[DiscoveryResponse])
def discover(
    query: DiscoveryRequest,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        CommunityMethodCardService,
        Depends(get_community_method_card_service),
    ],
) -> PublicApiEnvelope[DiscoveryResponse] | JSONResponse:
    return _execute(
        request,
        response,
        lambda: service.discover(
            learner_user_id=current_user.user_id,
            query=query,
        ),
    )


@router.get(
    "/versions/{card_version_id}",
    response_model=PublicApiEnvelope[PublicCardDetail],
)
def get_public_card_detail(
    card_version_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        CommunityMethodCardService,
        Depends(get_community_method_card_service),
    ],
) -> PublicApiEnvelope[PublicCardDetail] | JSONResponse:
    del current_user
    return _execute(
        request,
        response,
        lambda: service.public_detail(card_version_id=str(card_version_id)),
    )


@router.post("/exposures", response_model=PublicApiEnvelope[ExposureReceipt])
def create_exposure(
    command: ExposureCreate,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        CommunityMethodCardService,
        Depends(get_community_method_card_service),
    ],
) -> PublicApiEnvelope[ExposureReceipt] | JSONResponse:
    return _execute(
        request,
        response,
        lambda: service.record_exposure(
            learner_user_id=current_user.user_id,
            command=command,
        ),
    )


@router.post("/trials", response_model=PublicApiEnvelope[TrialReceipt])
def complete_trial(
    command: TrialComplete,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        CommunityMethodCardService,
        Depends(get_community_method_card_service),
    ],
) -> PublicApiEnvelope[TrialReceipt] | JSONResponse:
    return _execute(
        request,
        response,
        lambda: service.complete_trial(
            learner_user_id=current_user.user_id,
            command=command,
        ),
    )


@router.post(
    "/trials/{trial_id}/delayed-verifications",
    response_model=PublicApiEnvelope[DelayedVerificationReceipt],
)
def record_delayed_verification(
    trial_id: UUID,
    command: DelayedVerificationRecord,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        CommunityMethodCardService,
        Depends(get_community_method_card_service),
    ],
) -> PublicApiEnvelope[DelayedVerificationReceipt] | JSONResponse:
    if str(trial_id) != command.trial_id:
        return _error(request, CommunityMethodCardError("idempotency_conflict"))
    return _execute(
        request,
        response,
        lambda: service.record_delayed_verification(
            learner_user_id=current_user.user_id,
            command=command,
        ),
    )


@router.post("/feedback", response_model=PublicApiEnvelope[FeedbackReceipt])
def record_feedback(
    command: FeedbackRecord,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        CommunityMethodCardService,
        Depends(get_community_method_card_service),
    ],
) -> PublicApiEnvelope[FeedbackReceipt] | JSONResponse:
    return _execute(
        request,
        response,
        lambda: service.record_feedback(
            learner_user_id=current_user.user_id,
            command=command,
        ),
    )


@router.post("/reports", response_model=PublicApiEnvelope[ReportReceipt])
def report_card(
    command: ReportCreate,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        CommunityMethodCardService,
        Depends(get_community_method_card_service),
    ],
) -> PublicApiEnvelope[ReportReceipt] | JSONResponse:
    return _execute(
        request,
        response,
        lambda: service.report(
            reporter_user_id=current_user.user_id,
            command=command,
        ),
    )


@router.get("/{card_id}/workspace", response_model=PublicApiEnvelope[Workspace])
def get_workspace(
    card_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        CommunityMethodCardService,
        Depends(get_community_method_card_service),
    ],
) -> PublicApiEnvelope[Workspace] | JSONResponse:
    return _execute(
        request,
        response,
        lambda: service.workspace(
            author_user_id=current_user.user_id,
            card_id=str(card_id),
        ),
    )


@router.post(
    "/{card_id}/structure-candidate",
    response_model=PublicApiEnvelope[StructureCandidate],
)
def generate_candidate(
    card_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        CommunityMethodCardService,
        Depends(get_community_method_card_service),
    ],
) -> PublicApiEnvelope[StructureCandidate] | JSONResponse:
    return _execute(
        request,
        response,
        lambda: service.generate_candidate(
            author_user_id=current_user.user_id,
            card_id=str(card_id),
        ),
    )


@router.post(
    "/{card_id}/versions/confirm",
    response_model=PublicApiEnvelope[ConfirmReceipt],
)
def confirm_candidate(
    card_id: UUID,
    command: CandidateConfirm,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        CommunityMethodCardService,
        Depends(get_community_method_card_service),
    ],
) -> PublicApiEnvelope[ConfirmReceipt] | JSONResponse:
    if str(card_id) != command.card_id:
        return _error(request, CommunityMethodCardError("idempotency_conflict"))
    return _execute(
        request,
        response,
        lambda: service.confirm_candidate(
            author_user_id=current_user.user_id,
            command=command,
        ),
    )


@router.post("/{card_id}/submit", response_model=PublicApiEnvelope[ReviewOutcome])
def submit_for_review(
    card_id: UUID,
    command: CardSubmit,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        CommunityMethodCardService,
        Depends(get_community_method_card_service),
    ],
) -> PublicApiEnvelope[ReviewOutcome] | JSONResponse:
    if str(card_id) != command.card_id:
        return _error(request, CommunityMethodCardError("idempotency_conflict"))
    return _execute(
        request,
        response,
        lambda: service.submit_for_review(
            author_user_id=current_user.user_id,
            command=command,
        ),
    )


@router.post("/{card_id}/retire", response_model=PublicApiEnvelope[RetireReceipt])
def retire_card(
    card_id: UUID,
    command: RetireCard,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        CommunityMethodCardService,
        Depends(get_community_method_card_service),
    ],
) -> PublicApiEnvelope[RetireReceipt] | JSONResponse:
    if str(card_id) != command.card_id:
        return _error(request, CommunityMethodCardError("idempotency_conflict"))
    return _execute(
        request,
        response,
        lambda: service.retire(
            actor_user_id=current_user.user_id,
            command=command,
        ),
    )


def _execute(
    request: Request,
    response: Response,
    operation: Callable[[], T],
) -> PublicApiEnvelope[T] | JSONResponse:
    try:
        return public_success_envelope(
            request=request,
            response=response,
            data=operation(),
        )
    except CommunityMethodCardError as exc:
        return _error(request, exc)
    except (
        CandidateProviderConfigurationError,
        EffectPolicyUnavailable,
        ReviewPolicyUnavailable,
    ):
        return public_error_response(
            request=request,
            status_code=503,
            code="community_service_unavailable",
            message="社区方法服务的已发布策略暂不可用。",
        )


def _error(request: Request, error: CommunityMethodCardError) -> JSONResponse:
    not_found = {"not_found", "report_not_found"}
    forbidden = {
        "author_forbidden",
        "candidate_not_owned",
        "not_owner",
        "reflection_not_owned",
        "source_artifact_not_owned",
    }
    unavailable = error.code.endswith("_policy_unavailable") or error.code in {
        "community_policy_unavailable",
    }
    status_code = (
        404
        if error.code in not_found
        else 403
        if error.code in forbidden
        else 503
        if unavailable
        else 409
    )
    messages = {
        403: "当前账号不能操作该方法卡或证据。",
        404: "该方法卡或证据不存在。",
        409: "方法卡状态、版本或验证证据已变化。",
        503: "社区方法服务的已发布策略暂不可用。",
    }
    return public_error_response(
        request=request,
        status_code=status_code,
        code=error.code,
        message=messages[status_code],
    )
