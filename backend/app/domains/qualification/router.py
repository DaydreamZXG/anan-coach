from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.position_decision.goals import (
    ConfirmPrimaryGoalCommand,
    PositionGoalContractError,
    PositionGoalService,
)
from app.domains.position_decision.schemas import (
    PrimaryGoalConfirmRequest,
    PrimaryGoalConfirmResponse,
    PrimaryGoalPreview,
    PrimaryGoalPreviewRequest,
)
from app.domains.positions.discovery import (
    PositionComparisonError,
    PositionDiscoveryCursorError,
    PositionDiscoveryService,
)
from app.domains.positions.schemas import (
    MajorTreeResponse,
    ProvinceListResponse,
    PositionComparisonRequest,
    PositionComparisonResponse,
    PositionDiscoveryDetail,
    PositionDiscoveryResponse,
    PositionFitResponse,
    PositionTargetScoreContractResponse,
    PositionWorkspaceResponse,
)
from app.domains.positions.repository import PositionRepository
from app.domains.positions.service import PositionNotFoundError, PositionService
from app.domains.positions.target_scores import (
    PositionTargetScoreContractError,
    PositionTargetScoreOwner,
    TargetScoreVersionConflictError,
)
from app.domains.qualification.application import QualificationApplicationService
from app.domains.qualification.gaps import QualificationGapService
from app.domains.qualification.repository import (
    QualificationContractError,
    PositionRuleReleaseUnavailable,
)
from app.domains.qualification.schemas import (
    PositionEligibilitySnapshot,
    QualificationFactsUpdate,
    QualificationGapSummary,
    QualificationProfile,
    RequirementConfirmationRequest,
    RequirementConfirmationResult,
)
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_error_response,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2",
    tags=["qualification-and-position-goals"],
    responses=COMMON_API_ERROR_RESPONSES,
)


def _idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=160)],
) -> str:
    return value


def _target_score_error_response(
    *,
    request: Request,
    error: PositionTargetScoreContractError,
) -> Response:
    messages = {
        "position_release_unavailable": "当前考试年度没有已生效的岗位数据版本。",
        "position_release_upcoming": "本年度招录岗位已经核验，报名周期尚未开始。",
        "position_release_between_windows": "首轮报名已经结束，后续补录窗口尚未开始。",
        "position_release_closed": "本年度岗位报名周期已经结束，当前仅可用于历史参考。",
        "position_release_conflict": "岗位发布状态存在冲突，暂不能作为报考依据。",
        "target_score_basis_expired": "当前岗位目标分依据已过期，请等待新的岗位数据版本。",
        "position_data_version_conflict": "岗位数据版本已更新，请刷新后重试。",
        "target_score_unavailable": "该岗位暂无足够的真实进面分样本，无法生成目标分区间。",
    }
    return public_error_response(
        request=request,
        status_code=status.HTTP_409_CONFLICT,
        code=error.code,
        message=messages.get(error.code, "目标分依据无法用于当前岗位版本。"),
        details=error.details,
    )


def _position_release_unavailable_response(
    *,
    request: Request,
    error: PositionRuleReleaseUnavailable,
) -> Response:
    messages = {
        "position_release_unavailable": "当前考试年度没有已生效的岗位数据版本。",
        "position_release_upcoming": "本年度招录岗位已经核验，报名周期尚未开始。",
        "position_release_between_windows": "首轮报名已经结束，后续补录窗口尚未开始。",
        "position_release_closed": "本年度岗位报名周期已经结束，当前仅可用于历史参考。",
        "position_release_conflict": "岗位发布状态存在冲突，暂不能作为报考依据。",
    }
    return public_error_response(
        request=request,
        status_code=status.HTTP_409_CONFLICT,
        code=error.code,
        message=messages.get(error.code, "当前岗位发布不能用于报考决策。"),
        details=error.details,
    )


def _qualification_error_response(
    *,
    request: Request,
    error: QualificationContractError,
) -> Response:
    messages = {
        "qualification_profile_missing": "资格档案不存在，请重新登录后完成资格建档。",
        "qualification_fact_set_invalid": "资格事实不完整或存在冲突，请检查后重试。",
        "qualification_confirmation_invalid": "该岗位要求当前不能由用户确认。",
        "qualification_evaluation_conflict": "资格判断依据已变化，请刷新后重新判断。",
        "idempotency_key_reused": "同一幂等键已用于不同的资格建档命令。",
        "qualification_contract_conflict": "资格档案状态已变化，请刷新后重试。",
        "position_release_unavailable": "当前考试年度没有已生效的岗位数据版本。",
        "position_release_upcoming": "本年度招录岗位已经核验，报名周期尚未开始。",
        "position_release_between_windows": "首轮报名已经结束，后续补录窗口尚未开始。",
        "position_release_closed": "本年度岗位报名周期已经结束，当前仅可用于历史参考。",
        "position_release_conflict": "岗位发布状态存在冲突，暂不能作为报考依据。",
    }
    return public_error_response(
        request=request,
        status_code=(
            status.HTTP_404_NOT_FOUND
            if error.code == "qualification_profile_missing"
            else status.HTTP_409_CONFLICT
        ),
        code=error.code,
        message=messages.get(error.code, "资格档案状态已变化，请刷新后重试。"),
        details=error.details,
    )


@router.get(
    "/positions",
    response_model=PublicApiEnvelope[PositionDiscoveryResponse],
)
def discover_positions(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    exam_year: int | None = Query(default=None, alias="examYear", ge=2000, le=2100),
    region: str | None = Query(default=None, min_length=1, max_length=40),
    city: str | None = Query(default=None, min_length=1, max_length=60),
    query: str | None = Query(default=None, alias="q", min_length=1, max_length=80),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None, min_length=8, max_length=800),
) -> PublicApiEnvelope[PositionDiscoveryResponse] | Response:
    try:
        result = PositionDiscoveryService().search(
            session,
            user_id=current_user.user_id,
            exam_year=exam_year,
            region=region,
            city=city,
            query=query.strip() if query else None,
            limit=limit,
            cursor=cursor,
        )
        return public_success_envelope(request=request, response=response, data=result)
    except PositionDiscoveryCursorError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PositionRuleReleaseUnavailable as exc:
        return _position_release_unavailable_response(request=request, error=exc)
    except PositionTargetScoreContractError as exc:
        return _target_score_error_response(request=request, error=exc)
    except QualificationContractError as exc:
        return _qualification_error_response(request=request, error=exc)


@router.get(
    "/positions/workspace",
    response_model=PublicApiEnvelope[PositionWorkspaceResponse],
)
def get_position_workspace(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    region: str | None = Query(default=None, min_length=1, max_length=40),
    city: str | None = Query(default=None, min_length=1, max_length=60),
    query: str | None = Query(default=None, alias="q", min_length=1, max_length=80),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None, min_length=8, max_length=800),
) -> PublicApiEnvelope[PositionWorkspaceResponse] | Response:
    try:
        result = PositionDiscoveryService().workspace(
            session,
            user_id=current_user.user_id,
            region=region,
            city=city,
            query=query.strip() if query else None,
            limit=limit,
            cursor=cursor,
        )
        return public_success_envelope(request=request, response=response, data=result)
    except PositionDiscoveryCursorError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PositionRuleReleaseUnavailable as exc:
        return _position_release_unavailable_response(request=request, error=exc)
    except PositionTargetScoreContractError as exc:
        return _target_score_error_response(request=request, error=exc)
    except QualificationContractError as exc:
        return _qualification_error_response(request=request, error=exc)


@router.get(
    "/qualification/gaps",
    response_model=PublicApiEnvelope[QualificationGapSummary],
)
def get_qualification_gaps(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[QualificationGapSummary] | Response:
    try:
        result = QualificationGapService().summarize(
            session,
            user_id=current_user.user_id,
        )
        return public_success_envelope(request=request, response=response, data=result)
    except PositionRuleReleaseUnavailable as exc:
        return _position_release_unavailable_response(request=request, error=exc)
    except QualificationContractError as exc:
        return _qualification_error_response(request=request, error=exc)


@router.get(
    "/positions/majors",
    response_model=PublicApiEnvelope[MajorTreeResponse],
)
def list_position_majors_v2(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[MajorTreeResponse]:
    del current_user
    result = PositionService(PositionRepository(session)).list_majors()
    return public_success_envelope(request=request, response=response, data=result)


@router.get(
    "/positions/regions",
    response_model=PublicApiEnvelope[ProvinceListResponse],
)
def list_position_regions_v2(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ProvinceListResponse]:
    del current_user
    result = PositionService(PositionRepository(session)).list_regions()
    return public_success_envelope(request=request, response=response, data=result)


@router.post(
    "/positions/compare",
    response_model=PublicApiEnvelope[PositionComparisonResponse],
)
def compare_positions(
    request: Request,
    response: Response,
    comparison: PositionComparisonRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PositionComparisonResponse] | Response:
    try:
        result = PositionDiscoveryService().compare(
            session,
            user_id=current_user.user_id,
            position_ids=[str(position_id) for position_id in comparison.position_ids],
        )
        return public_success_envelope(request=request, response=response, data=result)
    except PositionComparisonError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PositionRuleReleaseUnavailable as exc:
        return _position_release_unavailable_response(request=request, error=exc)
    except QualificationContractError as exc:
        return _qualification_error_response(request=request, error=exc)
    except PositionTargetScoreContractError as exc:
        return _target_score_error_response(request=request, error=exc)


@router.get(
    "/positions/{position_id}/fit",
    response_model=PublicApiEnvelope[PositionFitResponse],
)
def get_position_fit_v2(
    request: Request,
    response: Response,
    position_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PositionFitResponse] | Response:
    try:
        result = PositionDiscoveryService().fit(
            session,
            user_id=current_user.user_id,
            position_id=str(position_id),
        )
        return public_success_envelope(request=request, response=response, data=result)
    except PositionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Position not found"
        ) from exc
    except PositionTargetScoreContractError as exc:
        return _target_score_error_response(request=request, error=exc)
    except QualificationContractError as exc:
        return _qualification_error_response(request=request, error=exc)


@router.get(
    "/positions/{position_id}",
    response_model=PublicApiEnvelope[PositionDiscoveryDetail],
)
def get_position_detail_v2(
    request: Request,
    response: Response,
    position_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PositionDiscoveryDetail] | Response:
    try:
        result = PositionDiscoveryService().detail(
            session,
            user_id=current_user.user_id,
            position_id=str(position_id),
        )
        return public_success_envelope(request=request, response=response, data=result)
    except PositionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Position not found"
        ) from exc
    except PositionTargetScoreContractError as exc:
        return _target_score_error_response(request=request, error=exc)
    except QualificationContractError as exc:
        return _qualification_error_response(request=request, error=exc)


@router.get(
    "/positions/{position_id}/target-score",
    response_model=PublicApiEnvelope[PositionTargetScoreContractResponse],
)
def get_position_target_score_v2(
    request: Request,
    response: Response,
    position_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PositionTargetScoreContractResponse] | Response:
    del current_user
    try:
        owner = PositionTargetScoreOwner()
        context = owner.active_context_for_position(
            session,
            position_id=str(position_id),
        )
        target_score = owner.range_for_position(
            session,
            position_id=str(position_id),
            context=context,
        )
        result = PositionTargetScoreContractResponse(
            position_id=str(position_id),
            exam_year=context.release.exam_year,
            rule_set_version=context.release.rule_set_version,
            position_data_version=context.release.position_data_version,
            target_score=target_score,
        )
        return public_success_envelope(request=request, response=response, data=result)
    except PositionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Position not found"
        ) from exc
    except PositionTargetScoreContractError as exc:
        return _target_score_error_response(request=request, error=exc)


@router.get("/qualification/profile", response_model=PublicApiEnvelope[QualificationProfile])
def get_qualification_profile(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[QualificationProfile] | Response:
    try:
        profile = QualificationApplicationService().get_profile(
            session,
            user_id=current_user.user_id,
        )
        return public_success_envelope(request=request, response=response, data=profile)
    except QualificationContractError as exc:
        return _qualification_error_response(request=request, error=exc)


@router.put("/qualification/profile", response_model=PublicApiEnvelope[QualificationProfile])
def update_qualification_profile(
    request: Request,
    response: Response,
    update: QualificationFactsUpdate,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[QualificationProfile] | Response:
    try:
        result = QualificationApplicationService().record_reported_facts(
            session,
            user_id=current_user.user_id,
            facts=update.facts,
            command_id=f"{current_user.user_id}:{command_id}",
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except QualificationContractError as exc:
        session.rollback()
        return _qualification_error_response(request=request, error=exc)


@router.post(
    "/positions/{position_id}/eligibility",
    response_model=PublicApiEnvelope[PositionEligibilitySnapshot],
)
def evaluate_position_eligibility(
    request: Request,
    response: Response,
    position_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PositionEligibilitySnapshot] | Response:
    try:
        result = QualificationApplicationService().evaluate_position(
            session,
            user_id=current_user.user_id,
            position_id=str(position_id),
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except LookupError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Position not found"
        ) from exc
    except PositionRuleReleaseUnavailable as exc:
        session.rollback()
        return _position_release_unavailable_response(request=request, error=exc)
    except QualificationContractError as exc:
        session.rollback()
        return _qualification_error_response(request=request, error=exc)


@router.put(
    "/positions/{position_id}/requirements/{requirement_id}/confirmation",
    response_model=PublicApiEnvelope[RequirementConfirmationResult],
)
def confirm_position_requirement(
    request: Request,
    response: Response,
    confirmation_request: RequirementConfirmationRequest,
    position_id: UUID,
    requirement_id: UUID,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[RequirementConfirmationResult] | Response:
    try:
        confirmation, eligibility = QualificationApplicationService().confirm_requirement(
            session,
            user_id=current_user.user_id,
            position_id=str(position_id),
            requirement_id=str(requirement_id),
            response=confirmation_request.response,
            statement=confirmation_request.statement,
            command_id=f"{current_user.user_id}:{command_id}",
        )
        session.commit()
        return public_success_envelope(
            request=request,
            response=response,
            data=RequirementConfirmationResult(
                confirmation=confirmation,
                eligibility=eligibility,
            ),
        )
    except QualificationContractError as exc:
        session.rollback()
        return _qualification_error_response(request=request, error=exc)
    except LookupError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/position-goals/primary/preview",
    response_model=PublicApiEnvelope[PrimaryGoalPreview],
)
def preview_primary_position_goal(
    http_request: Request,
    response: Response,
    preview_request: PrimaryGoalPreviewRequest,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PrimaryGoalPreview] | Response:
    try:
        snapshot = (
            session.execute(
                text(
                    """
                select snapshot.learner_profile_version,
                       snapshot.rule_set_version,
                       snapshot.position_data_version,
                       snapshot.status,
                       profile.profile_version as current_profile_version
                from learner.eligibility_snapshots snapshot
                join learner.profiles profile on profile.user_id = snapshot.user_id
                where snapshot_id = cast(:snapshot_id as uuid)
                  and snapshot.user_id = cast(:user_id as uuid)
                  and snapshot.position_id = cast(:position_id as uuid)
                """
                ),
                {
                    "snapshot_id": preview_request.eligibility_snapshot_id,
                    "user_id": current_user.user_id,
                    "position_id": preview_request.position_id,
                },
            )
            .mappings()
            .one_or_none()
        )
        if snapshot is None:
            raise PositionGoalContractError("Eligibility snapshot does not match the goal")
        if snapshot["status"] != "eligible":
            raise PositionGoalContractError(
                "Only a confirmed eligible snapshot can become the primary goal"
            )
        if int(snapshot["learner_profile_version"]) != int(snapshot["current_profile_version"]):
            raise PositionGoalContractError("Learner profile changed; reevaluate eligibility")

        target_score_owner = PositionTargetScoreOwner()
        target_context = target_score_owner.active_context_for_position(
            session,
            position_id=str(preview_request.position_id),
        )
        target_score = target_score_owner.range_for_position(
            session,
            position_id=str(preview_request.position_id),
            context=target_context,
            expected_rule_set_version=snapshot["rule_set_version"],
            expected_position_data_version=snapshot["position_data_version"],
            require_evidence=True,
        )
        assert target_score is not None
        goal_preview = PositionGoalService().create_primary_goal_preview(
            session,
            request_id=f"{current_user.user_id}:{command_id}",
            user_id=current_user.user_id,
            position_id=str(preview_request.position_id),
            eligibility_snapshot_id=str(preview_request.eligibility_snapshot_id),
            learner_profile_version=int(snapshot["learner_profile_version"]),
            position_data_version=snapshot["position_data_version"],
            target_score=target_score,
        )
        session.commit()
        return public_success_envelope(
            request=http_request,
            response=response,
            data=goal_preview,
        )
    except PositionNotFoundError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Position not found"
        ) from exc
    except PositionTargetScoreContractError as exc:
        session.rollback()
        return _target_score_error_response(request=http_request, error=exc)
    except PositionGoalContractError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.put(
    "/position-goals/primary",
    response_model=PublicApiEnvelope[PrimaryGoalConfirmResponse],
)
def confirm_primary_position_goal(
    http_request: Request,
    response: Response,
    goal_request: PrimaryGoalConfirmRequest,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PrimaryGoalConfirmResponse] | Response:
    try:
        scoped_command_id = f"{current_user.user_id}:{command_id}"
        goal_service = PositionGoalService()
        replayed = goal_service.replay_confirm_primary_goal(
            session,
            command_id=scoped_command_id,
            user_id=current_user.user_id,
            preview_id=str(goal_request.preview_id),
            change_reason=goal_request.change_reason,
        )
        if replayed is not None:
            replayed_goal, replayed_impact = replayed
            return public_success_envelope(
                request=http_request,
                response=response,
                data=PrimaryGoalConfirmResponse(
                    goal=replayed_goal,
                    plan_impact=replayed_impact,
                ),
            )
        preview = goal_service.get_primary_goal_preview(
            session,
            user_id=current_user.user_id,
            preview_id=str(goal_request.preview_id),
        )
        target_score_owner = PositionTargetScoreOwner()
        target_context = target_score_owner.active_context_for_position(
            session,
            position_id=preview.position_id,
        )
        current_target_score = target_score_owner.range_for_position(
            session,
            position_id=preview.position_id,
            context=target_context,
            expected_position_data_version=preview.position_data_version,
            require_evidence=True,
        )
        if current_target_score != preview.target_score:
            raise TargetScoreVersionConflictError(
                "Target-score evidence changed after the primary-goal preview"
            )
        goal = goal_service.confirm_primary_goal(
            session,
            command=ConfirmPrimaryGoalCommand(
                command_id=scoped_command_id,
                preview_id=preview.preview_id,
                user_id=current_user.user_id,
                position_id=preview.position_id,
                eligibility_snapshot_id=preview.eligibility_snapshot_id,
                expected_previous_goal_version_id=preview.current_goal_version_id,
                learner_profile_version=preview.learner_profile_version,
                position_data_version=preview.position_data_version,
                target_score=preview.target_score,
                change_reason=goal_request.change_reason,
                plan_impact=preview.plan_impact,
            ),
        )
        session.commit()
        return public_success_envelope(
            request=http_request,
            response=response,
            data=PrimaryGoalConfirmResponse(goal=goal, plan_impact=preview.plan_impact),
        )
    except PositionNotFoundError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Position not found"
        ) from exc
    except PositionTargetScoreContractError as exc:
        session.rollback()
        return _target_score_error_response(request=http_request, error=exc)
    except PositionGoalContractError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
