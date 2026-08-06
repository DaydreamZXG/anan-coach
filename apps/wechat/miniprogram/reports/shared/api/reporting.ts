import type { RecognitionRevision } from "../../../api/external-learning-contracts";
import { ApiClientError } from "../../../api/client";
import type { ApiClient } from "../../../api/client";
import { SourceArtifactApi } from "../../../api/source-artifact";
import type {
  ExternalMockReportConfirmCommand,
  ExternalMockReportDraftCommand,
  ExternalMockReportProjection,
  ExternalScoreRecognitionCorrectionCommand,
  ExternalScoreRecognitionDecisionCommand,
  ExternalScoreRecognitionStartCommand,
  PageSegment,
  ReportCenterProjection,
  ReportProjection,
  TextSpan,
} from "../../../api/generated/types.gen";
import type { LocalSourceImageFile } from "../platform/source-image";

export type ReportKind = "single_set" | "weekly" | "mock" | "position_gap" | "global";

export class ReportingApi {
  private readonly artifacts: SourceArtifactApi;

  constructor(private readonly client: ApiClient) {
    this.artifacts = new SourceArtifactApi(client);
  }

  getCenter(): Promise<{ data: ReportCenterProjection }> {
    return this.client.get<ReportCenterProjection>("/api/v2/reporting/center");
  }

  getReport(kind: ReportKind, subjectRef = "current"): Promise<{ data: ReportProjection }> {
    return this.client.get<ReportProjection>(
      `/api/v2/reporting/reports/${kind}?subjectRef=${encodeURIComponent(subjectRef)}`,
    );
  }

  async createExternalScoreRecognition(
    file: LocalSourceImageFile,
    idempotencyKey: string,
    onProgress: (percent: number) => void,
  ): Promise<RecognitionRevision> {
    const uploaded = await this.artifacts.upload(
      file,
      derivedKey(idempotencyKey, "upload"),
      (progress) => {
        onProgress(progress.percent);
      },
    );
    const command: ExternalScoreRecognitionStartCommand = {
      artifactVersionId: uploaded.artifact.artifactVersionId,
    };
    const response = await this.client.post<RecognitionRevision>(
      "/api/v2/reporting/external-score-recognitions",
      command,
      { idempotencyKey: derivedKey(idempotencyKey, "ocr"), timeoutMs: 60_000 },
    );
    assertPrintedOcr(response.data);
    return response.data;
  }

  async correctExternalScoreRecognition(
    revision: RecognitionRevision,
    spans: TextSpan[],
    idempotencyKey: string,
  ): Promise<RecognitionRevision> {
    assertPrintedOcr(revision);
    const payload = revision.candidatePayload;
    const command: ExternalScoreRecognitionCorrectionCommand = {
      expectedRevisionNo: revision.revisionNo,
      reason: "learner_verified_external_score_transcription",
      correctedPayload: {
        schemaVersion: "recognition-candidate-payload@v1",
        kind: "printed_ocr",
        textSpans: spans,
        segments: pageSegments(payload.segments),
        omrMarks: [],
        pageImageSha256: stringArray(payload.pageImageSha256),
        visualFingerprints: stringArray(payload.visualFingerprints),
        processorRequestIdHash: requiredString(payload.processorRequestIdHash),
        processorOutputContract: requiredString(payload.processorOutputContract),
        requestHash: requiredString(payload.requestHash),
        predecessorRevisionId: revision.recognitionRevisionId,
        correctionReason: "learner_verified_external_score_transcription",
      },
    };
    const response = await this.client.put<RecognitionRevision>(
      `/api/v2/reporting/external-score-recognitions/${encodeURIComponent(revision.recognitionRevisionId)}/candidate`,
      command,
      { idempotencyKey, timeoutMs: 30_000 },
    );
    assertPrintedOcr(response.data);
    return response.data;
  }

  async confirmExternalScoreRecognition(
    revision: RecognitionRevision,
    idempotencyKey: string,
  ): Promise<RecognitionRevision> {
    assertPrintedOcr(revision);
    const command: ExternalScoreRecognitionDecisionCommand = {
      expectedRevisionNo: revision.revisionNo,
    };
    const response = await this.client.post<RecognitionRevision>(
      `/api/v2/reporting/external-score-recognitions/${encodeURIComponent(revision.recognitionRevisionId)}/confirm`,
      command,
      { idempotencyKey, timeoutMs: 30_000 },
    );
    assertPrintedOcr(response.data);
    return response.data;
  }

  createExternalMockDraft(
    command: ExternalMockReportDraftCommand,
    idempotencyKey: string,
  ): Promise<{ data: ExternalMockReportProjection }> {
    return this.client.post<ExternalMockReportProjection>(
      "/api/v2/reporting/external-mocks",
      command,
      { idempotencyKey },
    );
  }

  confirmExternalMock(
    reportVersionId: string,
    idempotencyKey: string,
  ): Promise<{ data: ExternalMockReportProjection }> {
    const command: ExternalMockReportConfirmCommand = {
      expectedReportVersionId: reportVersionId,
    };
    return this.client.post<ExternalMockReportProjection>(
      `/api/v2/reporting/external-mocks/${encodeURIComponent(reportVersionId)}/confirm`,
      command,
      { idempotencyKey },
    );
  }

  getExternalMock(reportVersionId: string): Promise<{ data: ExternalMockReportProjection }> {
    return this.client.get<ExternalMockReportProjection>(
      `/api/v2/reporting/external-mocks/${encodeURIComponent(reportVersionId)}`,
    );
  }
}

function assertPrintedOcr(revision: RecognitionRevision): void {
  if (revision.recognitionKind !== "printed_ocr") {
    throw new ApiClientError(
      "非印刷成绩截图识别不能进入外部报告。",
      409,
      "external-score-recognition-kind-invalid",
    );
  }
}

function requiredString(value: unknown): string {
  if (typeof value !== "string" || !value) {
    throw new ApiClientError(
      "OCR 候选缺少处理器证据。",
      409,
      "external-score-recognition-contract-invalid",
    );
  }
  return value;
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const items: unknown[] = value;
  return items.filter((item): item is string => typeof item === "string");
}

function pageSegments(value: unknown): PageSegment[] {
  if (!Array.isArray(value)) return [];
  const items: unknown[] = value;
  return items.filter(isPageSegment);
}

function isPageSegment(value: unknown): value is PageSegment {
  if (!isRecord(value) || !isRecord(value.box)) return false;
  return (
    typeof value.segmentId === "string" &&
    ["question", "options", "material", "answer_area", "essay_prompt", "unknown"].includes(
      String(value.segmentKind),
    ) &&
    typeof value.confidence === "number" &&
    typeof value.box.pageNo === "number" &&
    typeof value.box.x === "number" &&
    typeof value.box.y === "number" &&
    typeof value.box.width === "number" &&
    typeof value.box.height === "number"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function derivedKey(base: string, suffix: string): string {
  const value = `${base}:${suffix}`;
  return value.length <= 160 ? value : base;
}
