import type { ApiClient } from "./client";
import type {
  MajorTreeResponse,
  PositionEligibilitySnapshot,
  PositionComparisonResponse,
  PositionDiscoveryDetail,
  PositionDiscoveryResponse,
  PositionFitResponse,
  PositionWorkspaceResponse,
  ProvinceListResponse,
  PrimaryGoalConfirmRequest,
  PrimaryGoalConfirmResponse,
  PrimaryGoalPreview,
  PrimaryGoalPreviewRequest,
  QualificationFactInput,
  QualificationGapSummary,
  QualificationProfile,
  RequirementConfirmationRequest,
  RequirementConfirmationResult,
} from "./generated/types.gen";
import type { ApiEnvelope } from "../domain/contracts";

export class QualificationApi {
  constructor(private readonly client: ApiClient) {}

  getProfile(): Promise<ApiEnvelope<QualificationProfile>> {
    return this.client.get<QualificationProfile>("/api/v2/qualification/profile");
  }

  getQualificationGaps(): Promise<ApiEnvelope<QualificationGapSummary>> {
    return this.client.get<QualificationGapSummary>("/api/v2/qualification/gaps");
  }

  getMajorCatalog(): Promise<ApiEnvelope<MajorTreeResponse>> {
    return this.client.get<MajorTreeResponse>("/api/v2/positions/majors");
  }

  getRegionCatalog(): Promise<ApiEnvelope<ProvinceListResponse>> {
    return this.client.get<ProvinceListResponse>("/api/v2/positions/regions");
  }

  discoverPositions(filters: {
    examYear?: number;
    region?: string;
    city?: string;
    query?: string;
    limit?: number;
    cursor?: string;
  }): Promise<ApiEnvelope<PositionDiscoveryResponse>> {
    const params: string[] = [];
    if (filters.examYear !== undefined) params.push(`examYear=${String(filters.examYear)}`);
    if (filters.region) params.push(`region=${encodeURIComponent(filters.region)}`);
    if (filters.city) params.push(`city=${encodeURIComponent(filters.city)}`);
    if (filters.query) params.push(`q=${encodeURIComponent(filters.query)}`);
    if (filters.limit !== undefined) params.push(`limit=${String(filters.limit)}`);
    if (filters.cursor) params.push(`cursor=${encodeURIComponent(filters.cursor)}`);
    const queryString = params.length ? `?${params.join("&")}` : "";
    return this.client.get<PositionDiscoveryResponse>(`/api/v2/positions${queryString}`);
  }

  getPositionWorkspace(
    filters: {
      region?: string;
      city?: string;
      query?: string;
      limit?: number;
      cursor?: string;
    } = {},
  ): Promise<ApiEnvelope<PositionWorkspaceResponse>> {
    const params: string[] = [];
    if (filters.region) params.push(`region=${encodeURIComponent(filters.region)}`);
    if (filters.city) params.push(`city=${encodeURIComponent(filters.city)}`);
    if (filters.query) params.push(`q=${encodeURIComponent(filters.query)}`);
    if (filters.limit !== undefined) params.push(`limit=${String(filters.limit)}`);
    if (filters.cursor) params.push(`cursor=${encodeURIComponent(filters.cursor)}`);
    const queryString = params.length ? `?${params.join("&")}` : "";
    return this.client.get<PositionWorkspaceResponse>(`/api/v2/positions/workspace${queryString}`);
  }

  comparePositions(positionIds: string[]): Promise<ApiEnvelope<PositionComparisonResponse>> {
    return this.client.post<PositionComparisonResponse>("/api/v2/positions/compare", {
      positionIds,
    });
  }

  getPositionDetail(positionId: string): Promise<ApiEnvelope<PositionDiscoveryDetail>> {
    return this.client.get<PositionDiscoveryDetail>(
      `/api/v2/positions/${encodeURIComponent(positionId)}`,
    );
  }

  getPositionFit(positionId: string): Promise<ApiEnvelope<PositionFitResponse>> {
    return this.client.get<PositionFitResponse>(
      `/api/v2/positions/${encodeURIComponent(positionId)}/fit`,
    );
  }

  updateProfile(
    facts: QualificationFactInput[],
    idempotencyKey: string,
  ): Promise<ApiEnvelope<QualificationProfile>> {
    return this.client.put<QualificationProfile>(
      "/api/v2/qualification/profile",
      { facts },
      { idempotencyKey },
    );
  }

  evaluatePosition(positionId: string): Promise<ApiEnvelope<PositionEligibilitySnapshot>> {
    return this.client.post<PositionEligibilitySnapshot>(
      `/api/v2/positions/${encodeURIComponent(positionId)}/eligibility`,
      {},
    );
  }

  confirmRequirement(
    positionId: string,
    requirementId: string,
    request: RequirementConfirmationRequest,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<RequirementConfirmationResult>> {
    return this.client.put<RequirementConfirmationResult>(
      `/api/v2/positions/${encodeURIComponent(positionId)}/requirements/${encodeURIComponent(requirementId)}/confirmation`,
      request,
      { idempotencyKey },
    );
  }

  confirmPrimaryGoal(
    request: PrimaryGoalConfirmRequest,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<PrimaryGoalConfirmResponse>> {
    return this.client.put<PrimaryGoalConfirmResponse>("/api/v2/position-goals/primary", request, {
      idempotencyKey,
    });
  }

  previewPrimaryGoal(
    request: PrimaryGoalPreviewRequest,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<PrimaryGoalPreview>> {
    return this.client.post<PrimaryGoalPreview>("/api/v2/position-goals/primary/preview", request, {
      idempotencyKey,
    });
  }
}
