import type { ApiClient } from "../../miniprogram/api/client";
import type {
  CandidateConfirm as GeneratedCandidateConfirm,
  CardVersionView as GeneratedCardVersionView,
  CommunityRewardLedgerResponse as GeneratedCommunityRewardLedgerResponse,
  ConfirmReceipt as GeneratedConfirmReceipt,
  ContributionsResponse as GeneratedContributionsResponse,
  DelayedVerificationRecord as GeneratedDelayedVerificationRecord,
  DelayedVerificationReceipt as GeneratedDelayedVerificationReceipt,
  DiscoveryCard as GeneratedDiscoveryCard,
  DiscoveryResponse as GeneratedDiscoveryResponse,
  DraftCreate as GeneratedDraftCreate,
  DraftReceipt as GeneratedDraftReceipt,
  ExposureReceipt as GeneratedExposureReceipt,
  FeedbackReceipt as GeneratedFeedbackReceipt,
  MethodCardCatalog as GeneratedMethodCardCatalog,
  MethodCardCatalogItem as GeneratedMethodCardCatalogItem,
  ReportReceipt as GeneratedReportReceipt,
  ReviewOutcome as GeneratedReviewOutcome,
  StructureCandidate as GeneratedStructureCandidate,
  TrialReceipt as GeneratedTrialReceipt,
  Workspace as GeneratedWorkspace,
} from "../../miniprogram/api/generated/types.gen";

export const communityMethodCardsContractVersion = "stage35-v2" as const;

export type CommunityTrack = "xingce" | "essay";
export type CommunitySourceType =
  "text" | "voice_transcript" | "handwriting_extraction" | "reflection";
export type CommunityRightsDeclaration =
  "original" | "authorized_derivative" | "private_third_party";
export type CommunityCardState =
  | "private_draft"
  | "automated_review"
  | "trial"
  | "community_validated"
  | "revision_required"
  | "rejected"
  | "restricted"
  | "retired";
export type CommunityCandidateStatus = "processing" | "ready" | "failed" | "expired";
export type CommunityFeedbackVote = "helpful" | "not_helpful";
export type CommunityFeedbackReason =
  "clear" | "transferable" | "too_specific" | "unclear" | "incorrect" | "unsafe";
export type CommunityReportCategory =
  "copyright" | "incorrect" | "unsafe" | "advertising" | "contact_information" | "other_fixed";

export interface CommunityDraftCreateCommand extends GeneratedDraftCreate {
  sourceType: CommunitySourceType;
  sourceText: string;
  track: CommunityTrack;
  applicability: string[];
  sourceArtifactId?: string;
  reflectionVersionId?: string;
  idempotencyKey: string;
}

export interface CommunityDraftReceipt extends GeneratedDraftReceipt {
  cardId: string;
  versionId: string;
  status: "private_draft";
  candidateStatus: "processing" | "ready" | "failed";
}

export interface CommunityStructureCandidate extends GeneratedStructureCandidate {
  candidateId: string;
  baseVersionId: string;
  title: string;
  recognition: string;
  methodSteps: string[];
  sourceSpanRefs: string[];
  status: CommunityCandidateStatus;
  failureCode: string | null;
}

export interface CommunityCandidateConfirmCommand extends GeneratedCandidateConfirm {
  cardId: string;
  baseVersionId: string;
  candidateId: string;
  title: string;
  recognition: string;
  methodSteps: string[];
  applicability: string[];
  exclusions: string[];
  pitfalls: string[];
  rightsDeclaration: CommunityRightsDeclaration;
}

export interface CommunityConfirmReceipt extends GeneratedConfirmReceipt {
  cardId: string;
  versionId: string;
  versionNumber: number;
  authorConfirmedAt: string;
}

export interface CommunityReviewOutcome extends GeneratedReviewOutcome {
  cardId: string;
  versionId: string;
  status: "trial" | "revision_required" | "rejected";
  checkCodes: [string, string, string, string, string];
  reviewId: string;
  policyVersion: string;
}

export interface CommunityCardVersion extends GeneratedCardVersionView {
  versionId: string;
  cardId: string;
  versionNumber: number;
  baseVersionId: string | null;
  title: string | null;
  recognition: string | null;
  methodSteps: string[];
  applicability: string[];
  exclusions: string[];
  pitfalls: string[];
  rightsDeclaration: CommunityRightsDeclaration | null;
  authorConfirmedAt: string | null;
}

export interface CommunityCardWorkspace extends GeneratedWorkspace {
  cardId: string;
  status: CommunityCardState;
  headVersionId: string;
  versionIds: string[];
  candidateStatus: CommunityCandidateStatus;
  reviewStatus: string;
  versions: CommunityCardVersion[];
}

export interface CommunityDiscoveryCard extends Omit<GeneratedDiscoveryCard, "isAuthor"> {
  cardVersionId: string;
  title: string;
  recognition: string;
  methodSteps: string[];
  applicability: string[];
  exclusions: string[];
  pitfalls: string[];
  conservativeScore: number | null;
  evidenceCount: number;
  riskFlags: string[];
  evidencePolicyVersion: string;
  isAuthor?: boolean;
}

export interface CommunityDiscoveryResponse extends Omit<GeneratedDiscoveryResponse, "cards"> {
  cardVersionIds: string[];
  rankingPolicyVersion: string;
  nextCursor: string | null;
  cards: CommunityDiscoveryCard[];
}

export interface CommunityExposureReceipt extends GeneratedExposureReceipt {
  exposureId: string;
  cardId: string;
  versionId: string;
  optedInAt: string;
}

export interface CommunityTrialCompleteCommand {
  exposureId: string;
  questionAttemptId: string;
  questionVersionId: string;
}

export interface CommunityTrialReceipt extends GeneratedTrialReceipt {
  trialId: string;
  feedbackEligible: boolean;
  outcomeCode: string;
  delayedVerificationDueAt: string | null;
}

export interface CommunityDelayedVerificationCommand {
  trialId: string;
  questionAttemptId: string;
  questionVersionId: string;
}

type CommunityDelayedVerificationContractCheck =
  CommunityDelayedVerificationCommand extends GeneratedDelayedVerificationRecord
    ? GeneratedDelayedVerificationRecord extends CommunityDelayedVerificationCommand
      ? true
      : never
    : never;

const communityDelayedVerificationContractCheck: CommunityDelayedVerificationContractCheck = true;
void communityDelayedVerificationContractCheck;

export interface CommunityDelayedVerificationReceipt extends GeneratedDelayedVerificationReceipt {
  verificationId: string;
  recorded: boolean;
}

export interface CommunityFeedbackReceipt extends GeneratedFeedbackReceipt {
  voteId: string;
  recorded: boolean;
}

export interface CommunityReportReceipt extends GeneratedReportReceipt {
  reportId: string;
  cardStatus: "trial" | "community_validated" | "restricted";
}

export interface CommunityContributionsResponse extends GeneratedContributionsResponse {
  cardIds: string[];
  rewardEntryIds: string[];
  nextCursor: string | null;
}

export interface CommunityRewardLedgerResponse extends GeneratedCommunityRewardLedgerResponse {
  activePolicyVersions: string[];
  awardIds: string[];
  entryIds: string[];
  aiQuotaBalance: number;
  membershipSecondsBalance: number;
  entitlementVersion: string;
}

export interface SystemMethodCard extends Omit<GeneratedMethodCardCatalogItem, "track"> {
  track: CommunityTrack;
}

export interface SystemMethodCatalog extends Omit<GeneratedMethodCardCatalog, "cards"> {
  cards: SystemMethodCard[];
}

export class CommunityMethodCardsApi {
  constructor(private readonly client: ApiClient) {}

  async listSystemMethods(track: CommunityTrack): Promise<SystemMethodCatalog> {
    const systemTrack = track === "essay" ? "shenlun" : "xingce";
    const response = await this.client.get<GeneratedMethodCardCatalog>(
      `/api/v1/method-cards?track=${systemTrack}&limit=100&offset=0`,
      { timeoutMs: 30_000 },
    );
    return {
      ...response.data,
      cards: response.data.cards.map((card) => ({
        ...card,
        track: card.track === "shenlun" ? "essay" : "xingce",
      })),
    };
  }

  async createDraft(command: CommunityDraftCreateCommand): Promise<CommunityDraftReceipt> {
    const response = await this.client.post<CommunityDraftReceipt>(
      "/api/v2/community-method-cards/drafts",
      command,
      { idempotencyKey: command.idempotencyKey, timeoutMs: 60_000 },
    );
    return response.data;
  }

  async getWorkspace(cardId: string): Promise<CommunityCardWorkspace> {
    const response = await this.client.get<CommunityCardWorkspace>(
      `${cardPath(cardId)}/workspace`,
      { timeoutMs: 30_000 },
    );
    return response.data;
  }

  async generateCandidate(cardId: string): Promise<CommunityStructureCandidate> {
    const response = await this.client.post<CommunityStructureCandidate>(
      `${cardPath(cardId)}/structure-candidate`,
      {},
      { timeoutMs: 60_000 },
    );
    return response.data;
  }

  async confirmCandidate(
    command: CommunityCandidateConfirmCommand,
  ): Promise<CommunityConfirmReceipt> {
    const response = await this.client.post<CommunityConfirmReceipt>(
      `${cardPath(command.cardId)}/versions/confirm`,
      command,
      { timeoutMs: 30_000 },
    );
    return response.data;
  }

  async submitForReview(
    cardId: string,
    versionId: string,
    idempotencyKey: string,
  ): Promise<CommunityReviewOutcome> {
    const response = await this.client.post<CommunityReviewOutcome>(
      `${cardPath(cardId)}/submit`,
      { cardId, versionId, idempotencyKey },
      { idempotencyKey, timeoutMs: 60_000 },
    );
    return response.data;
  }

  async discover(
    track: CommunityTrack,
    applicability: string[],
    cursor?: string,
  ): Promise<CommunityDiscoveryResponse> {
    const response = await this.client.post<CommunityDiscoveryResponse>(
      "/api/v2/community-method-cards/discovery",
      { track, applicability, ...(cursor ? { cursor } : {}) },
      { timeoutMs: 30_000 },
    );
    return response.data;
  }

  async optInToTrial(
    cardVersionId: string,
    applicabilitySnapshot: string[],
    rankingPolicyVersion: string,
  ): Promise<CommunityExposureReceipt> {
    const response = await this.client.post<CommunityExposureReceipt>(
      "/api/v2/community-method-cards/exposures",
      { cardVersionId, applicabilitySnapshot, rankingPolicyVersion },
      { timeoutMs: 30_000 },
    );
    return response.data;
  }

  async completeTrial(command: CommunityTrialCompleteCommand): Promise<CommunityTrialReceipt> {
    const response = await this.client.post<CommunityTrialReceipt>(
      "/api/v2/community-method-cards/trials",
      command,
      { timeoutMs: 30_000 },
    );
    return response.data;
  }

  async recordDelayedVerification(
    command: CommunityDelayedVerificationCommand,
  ): Promise<CommunityDelayedVerificationReceipt> {
    const response = await this.client.post<CommunityDelayedVerificationReceipt>(
      `/api/v2/community-method-cards/trials/${encodeURIComponent(requiredId(command.trialId, "trialId"))}/delayed-verifications`,
      command,
      { timeoutMs: 30_000 },
    );
    return response.data;
  }

  async recordFeedback(
    trialId: string,
    vote: CommunityFeedbackVote,
    reasonCode: CommunityFeedbackReason,
  ): Promise<CommunityFeedbackReceipt> {
    const response = await this.client.post<CommunityFeedbackReceipt>(
      "/api/v2/community-method-cards/feedback",
      { trialId, vote, reasonCode },
      { timeoutMs: 30_000 },
    );
    return response.data;
  }

  async report(
    cardVersionId: string,
    category: CommunityReportCategory,
    evidenceRef: string,
  ): Promise<CommunityReportReceipt> {
    const response = await this.client.post<CommunityReportReceipt>(
      "/api/v2/community-method-cards/reports",
      { cardVersionId, category, evidenceRef },
      { timeoutMs: 30_000 },
    );
    return response.data;
  }

  async getContributions(cursor?: string): Promise<CommunityContributionsResponse> {
    const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
    const response = await this.client.get<CommunityContributionsResponse>(
      `/api/v2/community-method-cards/contributions${query}`,
      { timeoutMs: 30_000 },
    );
    return response.data;
  }

  async getRewardLedger(): Promise<CommunityRewardLedgerResponse> {
    const response = await this.client.get<CommunityRewardLedgerResponse>(
      "/api/v2/community-rewards/ledger",
      { timeoutMs: 30_000 },
    );
    return response.data;
  }
}

function cardPath(cardId: string): string {
  return `/api/v2/community-method-cards/${encodeURIComponent(requiredId(cardId, "cardId"))}`;
}

function requiredId(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${label} is required`);
  return normalized;
}
