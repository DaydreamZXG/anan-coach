import type {
  AnswerIngestionVersion,
  ContentMatchVersion,
  LearningTrack,
  OfflinePacketVersion,
  RecognitionRevision,
  SourceArtifactVersion,
  SourceKind,
} from "../../api/external-learning-contracts";

export type ImportSourceKind = Extract<SourceKind, "camera_image" | "image_import" | "pdf_upload">;

export interface LocalSourceArtifactFile {
  localPath: string;
  displayName: string;
  sourceKind: SourceKind;
  mimeType: string;
  byteSize: number;
  pageCount?: number | null;
}

export interface LocalImportFile extends LocalSourceArtifactFile {
  localPath: string;
  displayName: string;
  sourceKind: ImportSourceKind;
  mimeType: "image/jpeg" | "image/png" | "application/pdf";
  byteSize: number;
  pageCount: number | null;
}

export interface LocalAnswerImageFile extends LocalSourceArtifactFile {
  localPath: string;
  displayName: string;
  sourceKind: "answer_sheet";
  mimeType: "image/jpeg" | "image/png";
  byteSize: number;
  pageCount?: null;
}

export interface ImportRecognitionSegment {
  segmentId: string;
  pageNo: number;
  ordinal: number;
  text: string;
  confidence: number | null;
  state: "candidate" | "confirmed" | "rejected";
  requiresCorrection: boolean;
}

export interface ImportReferenceAnswer {
  sourceType: "authorized_reference";
  matchKind: "exact" | "similar";
  answerText: string;
  contentVersionId: string;
  authorizationState: "authorized_current";
  learningImpact: "reference_only";
}

export interface ImportAIAnswer {
  answerVersionId: string;
  state: "requested" | "processing" | "candidate" | "confirmed" | "failed";
  answerText: string | null;
  explanation: string | null;
  keyPoints: readonly string[];
  confidence: "low" | "medium" | "high" | null;
  limitations: readonly string[];
  outputFingerprint: string | null;
  failureCode: string | null;
  sourceType: "ai_estimated";
  learningImpact: "reference_only";
}

export interface ImportQuestionUnit {
  questionUnitId: string;
  ordinal: number;
  segmentIds: readonly string[];
  match: ContentMatchVersion | null;
  referenceAnswer: ImportReferenceAnswer | null;
  aiAnswer: ImportAIAnswer | null;
  state: "recognition_review" | "match_review" | "unmatched" | "ready";
  partialReasons: readonly string[];
}

export interface ImportWorkspace {
  importId: string;
  state: "uploading" | "processing" | "review" | "ready" | "failed" | "quarantined" | "expired";
  artifact: SourceArtifactVersion | null;
  recognition: RecognitionRevision | null;
  recognitionSegments: readonly ImportRecognitionSegment[];
  questionUnits: readonly ImportQuestionUnit[];
  partialReasons: readonly string[];
  failureReason: string | null;
  processingLabel: string | null;
  pollAfterMs: number | null;
  expiresAt: string | null;
  diagnosticEligibility: "blocked_unconfirmed" | "eligible_confirmed";
  generatedAt: string;
  requestId: string;
}

export interface BeginImportCommand {
  file: LocalImportFile;
  learningTrack: LearningTrack;
}

export interface RecognitionCorrectionCommand {
  importId: string;
  recognitionRevisionId: string;
  expectedRevisionNo: number;
  segments: readonly Pick<ImportRecognitionSegment, "segmentId" | "text">[];
}

export interface RecognitionConfirmationCommand {
  importId: string;
  recognitionRevisionId: string;
  expectedRevisionNo: number;
  learningTrack: LearningTrack;
}

export interface MatchConfirmationCommand {
  importId: string;
  contentMatchVersionId: string;
  expectedVersionNo: number;
  confirmedContentVersionId: string;
}

export interface AIAnswerRequestCommand {
  importId: string;
  questionUnitId: string;
  learningTrack: LearningTrack;
}

export interface AIAnswerConfirmationCommand {
  answerVersionId: string;
  expectedOutputFingerprint: string;
}

export interface PacketContentCandidate {
  questionId: string;
  contentVersionId: string;
  contentLicenseId: string;
  expectedAuthorizationEpoch: number;
  components: readonly (
    "question_prompt" | "question_options" | "material_text" | "material_table" | "material_image"
  )[];
  title: string;
  track: LearningTrack;
  sourceLabel: string;
  versionLabel: string;
  selectedByDefault: boolean;
}

export interface PacketListSnapshot {
  packets: readonly OfflinePacketVersion[];
  eligibleContent: readonly PacketContentCandidate[];
  partialReasons: readonly string[];
  generatedAt: string;
  requestId: string;
}

export interface CreatePacketCommand {
  renderFormat: "pdf" | "note_app_file";
  items: readonly {
    questionId: string;
    contentVersionId: string;
    contentLicenseId: string;
    expectedAuthorizationEpoch: number;
    track: LearningTrack;
    components: PacketContentCandidate["components"];
  }[];
  expiresInDays: number;
}

export interface RetryPacketCommand {
  packetId: string;
  failedPacketVersionId: string;
  expectedVersionNo: number;
}

export interface RenewPacketCommand {
  packetId: string;
  expiredPacketVersionId: string;
  expectedVersionNo: number;
}

export interface PacketAccessGrant {
  packetVersionId: string;
  localFilePath: string;
  fileType: "pdf" | "note_app_file";
  expiresAt: string;
}

export interface AnswerCandidateItem {
  itemId: string;
  orderNo: number;
  answerBindingCode: string;
  answer: string;
  confidence: number | null;
  requiresCorrection: boolean;
}

export interface AnswerIngestionWorkspace {
  ingestion: AnswerIngestionVersion;
  items: readonly AnswerCandidateItem[];
  packetTitle: string | null;
  packetItemCount: number | null;
  partialReasons: readonly string[];
  expiresAt: string | null;
  diagnosticEligibility: "blocked_unconfirmed" | "eligible_committed";
  generatedAt: string;
  requestId: string;
}

export interface CreateAnswerStringCommand {
  packetVersionId: string;
  track: LearningTrack;
  answerString: string;
}

export interface CreateAnswerImageCommand {
  packetVersionId: string;
  track: LearningTrack;
  file: LocalAnswerImageFile;
}

export interface ReviseAnswerCommand {
  ingestionId: string;
  expectedVersionNo: number;
  answers: readonly Pick<AnswerCandidateItem, "itemId" | "answerBindingCode" | "answer">[];
}

export interface ConfirmAnswerCommand {
  ingestionId: string;
  expectedVersionNo: number;
}

export interface PacketAnswerTarget {
  packetVersionId: string;
  packetTitle: string;
  track: LearningTrack;
  itemCount: number;
  versionLabel: string;
  expiresAt: string | null;
}

export interface AnswerTargetSnapshot {
  packets: readonly PacketAnswerTarget[];
  generatedAt: string;
  requestId: string;
}

export interface UploadProgress {
  sentBytes: number;
  totalBytes: number;
  percent: number;
}

export interface ExternalLearningGateway {
  beginImport(
    command: BeginImportCommand,
    idempotencyKey: string,
    onProgress: (progress: UploadProgress) => void,
  ): Promise<ImportWorkspace>;
  getImport(importId: string): Promise<ImportWorkspace>;
  reviseRecognition(
    command: RecognitionCorrectionCommand,
    idempotencyKey: string,
  ): Promise<ImportWorkspace>;
  confirmRecognition(
    command: RecognitionConfirmationCommand,
    idempotencyKey: string,
  ): Promise<ImportWorkspace>;
  confirmMatch(command: MatchConfirmationCommand, idempotencyKey: string): Promise<ImportWorkspace>;
  requestAIAnswer(command: AIAnswerRequestCommand, idempotencyKey: string): Promise<void>;
  confirmAIAnswer(command: AIAnswerConfirmationCommand, idempotencyKey: string): Promise<void>;

  listPackets(): Promise<PacketListSnapshot>;
  createPacket(command: CreatePacketCommand, idempotencyKey: string): Promise<OfflinePacketVersion>;
  retryPacket(command: RetryPacketCommand, idempotencyKey: string): Promise<OfflinePacketVersion>;
  renewPacket(command: RenewPacketCommand, idempotencyKey: string): Promise<OfflinePacketVersion>;
  getPacketAccess(packetVersionId: string): Promise<PacketAccessGrant>;

  listAnswerTargets(): Promise<AnswerTargetSnapshot>;
  getAnswerIngestion(ingestionId: string): Promise<AnswerIngestionWorkspace>;
  createAnswerString(
    command: CreateAnswerStringCommand,
    idempotencyKey: string,
  ): Promise<AnswerIngestionWorkspace>;
  createAnswerImage(
    command: CreateAnswerImageCommand,
    idempotencyKey: string,
    onProgress: (progress: UploadProgress) => void,
  ): Promise<AnswerIngestionWorkspace>;
  reviseAnswer(
    command: ReviseAnswerCommand,
    idempotencyKey: string,
  ): Promise<AnswerIngestionWorkspace>;
  confirmAnswer(
    command: ConfirmAnswerCommand,
    idempotencyKey: string,
  ): Promise<AnswerIngestionWorkspace>;
}

export interface ExternalLearningGatewayContext {
  userId: string;
  accessToken: string;
}

export type ExternalLearningGatewayFactory = (
  context: ExternalLearningGatewayContext,
) => ExternalLearningGateway;

let gatewayFactory: ExternalLearningGatewayFactory | null = null;

export function configureExternalLearningGatewayFactory(
  factory: ExternalLearningGatewayFactory,
): void {
  gatewayFactory = factory;
}

export function requireExternalLearningGateway(
  context: ExternalLearningGatewayContext,
): ExternalLearningGateway {
  if (!gatewayFactory) {
    throw new Error("External learning API gateway is not configured");
  }
  return gatewayFactory(context);
}

export function resetExternalLearningGatewayFactoryForTests(): void {
  gatewayFactory = null;
}
