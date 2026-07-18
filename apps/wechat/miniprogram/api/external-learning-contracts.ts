// Generated contract surface for the Stage 1.5 external-learning boundary.
// Backend parity is enforced by backend/tests/contracts/external_learning.

export const EXTERNAL_LEARNING_CONTRACT_CATALOG = {
  schemaVersion: "external-learning-contract-catalog@v1",
  contractVersions: {
    sourceArtifact: "source-artifact@v1",
    recognitionRevision: "recognition-revision@v1",
    contentMatch: "content-match@v1",
    offlinePacket: "offline-packet@v1",
    answerIngestion: "answer-ingestion@v1",
    contentLicenseRecord: "content-license-record@v1",
    contentLicenseEvaluate: "content-license-evaluate@v1",
    contentLicenseDecision: "content-license-decision@v1",
    contentLicenseSnapshotStatus: "content-license-snapshot-status@v1",
  },
  sourceKinds: ["camera_image", "image_import", "pdf_upload", "answer_sheet", "notes_app_export"],
  recognitionKinds: ["printed_ocr", "handwriting_ocr", "exam_segmentation", "answer_sheet_omr"],
  learningTracks: ["xingce", "essay"],
  contentComponents: [
    "question_prompt",
    "question_options",
    "reference_answer",
    "solution_explanation",
    "material_text",
    "material_table",
    "material_image",
    "rubric",
    "user_submission",
  ],
  contentUses: [
    "catalog_display",
    "in_app_practice",
    "private_print",
    "note_export",
    "share_forward",
    "cross_user_match",
    "ai_grounding",
    "model_training",
  ],
  deliveryChannels: [
    "wechat_miniprogram",
    "pdf_export",
    "image_export",
    "note_app_file",
    "qr_access",
    "internal_retrieval",
    "ai_processor",
  ],
  audiences: ["account_owner", "authorized_recipient", "authenticated_users", "public"],
  contentTransforms: [
    "verbatim",
    "layout_reflow",
    "excerpt",
    "derivative_explanation",
    "ocr",
    "embedding",
    "model_training",
  ],
  territories: ["mainland_china", "contract_defined"],
  licenseStates: [
    "draft",
    "in_review",
    "active",
    "suspended",
    "rejected",
    "expired",
    "withdrawn",
    "superseded",
    "abandoned",
  ],
} as const;

type Catalog = typeof EXTERNAL_LEARNING_CONTRACT_CATALOG;
export type SourceKind = Catalog["sourceKinds"][number];
export type RecognitionKind = Catalog["recognitionKinds"][number];
export type LearningTrack = Catalog["learningTracks"][number];
export type ContentComponent = Catalog["contentComponents"][number];
export type ContentUse = Catalog["contentUses"][number];
export type DeliveryChannel = Catalog["deliveryChannels"][number];
export type Audience = Catalog["audiences"][number];
export type ContentTransform = Catalog["contentTransforms"][number];
export type Territory = Catalog["territories"][number];
export type LicenseState = Catalog["licenseStates"][number];
export type Rfc3339DateTime = `${number}-${number}-${number}T${string}`;

export interface SourceArtifactVersion {
  contractVersion: "source-artifact@v1";
  artifactVersionId: string;
  artifactId: string;
  ownerUserId: string;
  versionNo: number;
  sourceKind: SourceKind;
  state: "uploaded" | "available" | "quarantined" | "expired" | "deleted";
  objectRef: string;
  mimeType: string;
  contentSha256: string;
  byteSize: number;
  pageCount: number | null;
  supersedesArtifactVersionId: string | null;
  createdAt: string;
}

export interface RecognitionRevision {
  contractVersion: "recognition-revision@v1";
  recognitionRevisionId: string;
  artifactVersionId: string;
  ownerUserId: string;
  revisionNo: number;
  recognitionKind: RecognitionKind;
  state: "candidate" | "confirmed" | "rejected" | "expired";
  processorPolicyVersion: string;
  processorRoute: string;
  inputSha256: string;
  outputSha256: string;
  candidatePayload: Record<string, unknown>;
  confidence: number | null;
  expiresAt: string;
  confirmedAt: string | null;
  createdAt: string;
}

export interface MatchCandidate {
  contentVersionId: string;
  matchKind: "exact" | "similar";
  score: number;
  evidenceRefs: readonly string[];
}

export interface ContentMatchVersion {
  contractVersion: "content-match@v1";
  contentMatchVersionId: string;
  contentMatchId: string;
  artifactVersionId: string;
  recognitionRevisionId: string;
  questionUnitId: string;
  ownerUserId: string;
  versionNo: number;
  state: "candidate" | "confirmed" | "rejected" | "expired";
  candidates: readonly MatchCandidate[];
  evaluationReason:
    "essay_incomplete" | "no_eligible_candidate" | "single_exact" | "single_similar" | "score_tie";
  confirmedContentVersionId: string | null;
  matchingPolicyVersion: string;
  inputFingerprint: string;
  supersedesContentMatchVersionId: string | null;
  createdAt: string;
}

export interface OfflinePacketItem {
  packetItemId: string;
  contentVersionId: string;
  track: LearningTrack;
  orderNo: number;
  answerBindingCode: string;
}

export interface OfflinePacketVersion {
  contractVersion: "offline-packet@v1";
  packetVersionId: string;
  packetId: string;
  ownerUserId: string;
  versionNo: number;
  state: "requested" | "generating" | "ready" | "failed" | "revoked" | "expired";
  renderFormat: "pdf" | "note_app_file";
  items: readonly OfflinePacketItem[];
  manifestSha256: string;
  licenseSnapshotRefs: readonly string[];
  artifactRef: string | null;
  expiresAt: string | null;
  createdAt: string;
}

export interface AnswerIngestionVersion {
  contractVersion: "answer-ingestion@v1";
  ingestionVersionId: string;
  ingestionId: string;
  ownerUserId: string;
  versionNo: number;
  packetVersionId: string | null;
  sourceArtifactVersionIds: readonly string[];
  track: LearningTrack;
  inputMode: "answer_string" | "answer_sheet_omr" | "handwriting_ocr" | "notes_app_export";
  state: "candidate" | "confirmed" | "rejected" | "committed" | "conflict";
  candidatePayload: Record<string, unknown>;
  targetWriter: "xingce_learning" | "essay_learning";
  writerContractVersion: string;
  sourceLineageRefs: readonly string[];
  confirmedAt: string | null;
  committedFactRef: string | null;
  createdAt: string;
}

export interface LicenseTerm {
  termKind: "fixed_term" | "revocable_open_term";
  effectiveAt: string;
  expiresAt: Rfc3339DateTime | "not_applicable";
  renewalPolicyVersionRef: string;
  postTerminationNewUse: "prohibited";
  postTerminationLiveAccess: "revoke";
  postTerminationStoredArtifacts: "quarantine_then_policy_dispose";
  postTerminationPrintedCopies: "cannot_recall_notify_and_block_return";
  postTerminationLearningEvidence: "retain_historical_not_current_authority";
}

export interface LicenseGrantCell {
  component: ContentComponent;
  use: ContentUse;
  channel: DeliveryChannel;
  audience: Audience;
  transform: ContentTransform;
  territory: Territory;
  decision: "allow";
}

export interface LicenseGrantMatrix {
  matrixVersionRef: string;
  matrixHash: string;
  componentsCoverage: readonly ContentComponent[];
  usesCoverage: readonly ContentUse[];
  channelsCoverage: readonly DeliveryChannel[];
  audiencesCoverage: readonly Audience[];
  transformsCoverage: readonly ContentTransform[];
  territoriesCoverage: readonly Territory[];
  defaultDecision: "deny";
  modelTrainingDecision: "deny";
  allowedGrantCells: readonly LicenseGrantCell[];
}

export interface AccountPrivateRights {
  sourceId: string;
  modelTrainingUse: "prohibited";
  term: LicenseTerm;
  grantMatrix: LicenseGrantMatrix;
  distributionClass: "account_private";
  rightsBasis: "user_private_purpose_limited";
  subjectScopeRef: string;
  userAuthorizationReceiptRef: string;
  userAuthorizationReceiptHash: string;
  crossAccountUse: "prohibited";
  forwarding: "prohibited";
}

export interface TransferableRights {
  sourceId: string;
  modelTrainingUse: "prohibited";
  term: LicenseTerm;
  grantMatrix: LicenseGrantMatrix;
  distributionClass: "licensed_transferable";
  rightsBasis: "verified_agreement";
  agreementRef: string;
  agreementHash: string;
}

export interface ReviewerReceipt {
  reviewerId: string;
  decision: "approved" | "rejected";
  reviewedRecordHash: string;
  evidenceManifestHash: string;
  grantMatrixHash: string;
  reviewedAt: string;
}

export interface ContentLicenseRecord {
  contractVersion: "content-license-record@v1";
  contentLicenseId: string;
  contentVersionId: string;
  revision: number;
  authorizationEpoch: number;
  state: LicenseState;
  createdByActorId: string;
  closedCoverageVersionRef: string;
  closedCoverageHash: string;
  rights: AccountPrivateRights | TransferableRights;
  evidenceManifestRef: string;
  evidenceManifestHash: string;
  recordCanonicalHash: string;
  reviewerReceipt: ReviewerReceipt | null;
  supersedesContentLicenseId: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface LicenseUseRequest {
  contractVersion: "content-license-evaluate@v1";
  contentLicenseId: string;
  contentVersionId: string;
  expectedAuthorizationEpoch: number | null;
  component: ContentComponent;
  use: ContentUse;
  channel: DeliveryChannel;
  audience: Audience;
  transform: ContentTransform;
  territory: Territory;
  requestedAt: string;
}

export interface LicenseDecision {
  contractVersion: "content-license-decision@v1";
  snapshotId: string;
  allowed: boolean;
  reason:
    | "explicit_grant"
    | "license_unknown"
    | "content_version_mismatch"
    | "license_not_active"
    | "authorization_epoch_stale"
    | "term_not_started"
    | "term_expired"
    | "default_deny";
  contentLicenseId: string;
  contentVersionId: string;
  licenseRevision: number | null;
  authorizationEpoch: number | null;
  grantMatrixHash: string | null;
  evidenceManifestHash: string | null;
  evaluatedAt: string;
}

export interface LicenseSnapshotStatus {
  contractVersion: "content-license-snapshot-status@v1";
  snapshotId: string;
  valid: boolean;
  reason:
    | "current"
    | "snapshot_denied"
    | "license_missing"
    | "content_version_mismatch"
    | "license_not_active"
    | "authorization_epoch_changed"
    | "term_not_started"
    | "term_expired";
  boundAuthorizationEpoch: number | null;
  currentAuthorizationEpoch: number | null;
  checkedAt: string;
}
