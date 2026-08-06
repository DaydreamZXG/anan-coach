import type { RecognitionRevision } from "../../../api/external-learning-contracts";
import { ApiClientError } from "../../../api/client";
import type { ApiClient } from "../../../api/client";
import { SourceArtifactApi } from "../../../api/source-artifact";
import type {
  EssayHandwritingArtifactAccess,
  EssayHandwritingCorrectionCommand,
  EssayHandwritingDecisionCommand,
  EssayHandwritingStartCommand,
  RecognitionCandidatePayload,
  TextSpan,
} from "../../../api/generated/types.gen";
import type { LocalSourceImageFile } from "../../../xingce/shared/platform/source-image";

export interface EssayHandwritingUploadProgress {
  sentBytes: number;
  totalBytes: number;
  percent: number;
}

export class EssayHandwritingApi {
  private readonly artifacts: SourceArtifactApi;

  constructor(private readonly client: ApiClient) {
    this.artifacts = new SourceArtifactApi(client);
  }

  async createRecognition(
    file: LocalSourceImageFile,
    idempotencyKey: string,
    onProgress: (progress: EssayHandwritingUploadProgress) => void,
  ): Promise<RecognitionRevision> {
    if (!["camera_image", "image_import"].includes(file.sourceKind)) {
      throw new ApiClientError(
        "申论手写 OCR 不接受行测答题卡或文档来源。",
        422,
        "essay-handwriting-source-kind-invalid",
      );
    }
    const uploaded = await this.artifacts.upload(
      file,
      derivedKey(idempotencyKey, "upload"),
      onProgress,
    );
    const payload: EssayHandwritingStartCommand = {
      artifactVersionId: uploaded.artifact.artifactVersionId,
    };
    const response = await this.client.post<RecognitionRevision>(
      "/api/v2/essay-intelligence/handwriting-recognitions",
      payload,
      { idempotencyKey: derivedKey(idempotencyKey, "recognition"), timeoutMs: 60_000 },
    );
    assertEssayHandwriting(response.data);
    return response.data;
  }

  async correctRecognition(
    revision: RecognitionRevision,
    spans: readonly TextSpan[],
    idempotencyKey: string,
  ): Promise<RecognitionRevision> {
    assertEssayHandwriting(revision);
    const candidate = handwritingCandidate(revision);
    const payload: EssayHandwritingCorrectionCommand = {
      expectedRevisionNo: revision.revisionNo,
      reason: "learner_verified_essay_handwriting_transcription",
      correctedPayload: { ...candidate, kind: "handwriting_ocr", textSpans: [...spans] },
    };
    const response = await this.client.put<RecognitionRevision>(
      `/api/v2/essay-intelligence/handwriting-recognitions/${encodeURIComponent(revision.recognitionRevisionId)}/candidate`,
      payload,
      { idempotencyKey, timeoutMs: 30_000 },
    );
    assertEssayHandwriting(response.data);
    return response.data;
  }

  async confirmRecognition(
    revision: RecognitionRevision,
    idempotencyKey: string,
  ): Promise<RecognitionRevision> {
    assertEssayHandwriting(revision);
    const payload: EssayHandwritingDecisionCommand = {
      expectedRevisionNo: revision.revisionNo,
    };
    const response = await this.client.post<RecognitionRevision>(
      `/api/v2/essay-intelligence/handwriting-recognitions/${encodeURIComponent(revision.recognitionRevisionId)}/confirm`,
      payload,
      { idempotencyKey, timeoutMs: 30_000 },
    );
    assertEssayHandwriting(response.data);
    return response.data;
  }

  async getArtifactAccess(artifactVersionId: string): Promise<EssayHandwritingArtifactAccess> {
    const normalized = artifactVersionId.trim();
    if (!normalized) {
      throw new ApiClientError(
        "申论手写原图版本不完整。",
        422,
        "essay-handwriting-artifact-required",
      );
    }
    const response = await this.client.post<EssayHandwritingArtifactAccess>(
      `/api/v2/essay-intelligence/handwriting-artifacts/${encodeURIComponent(normalized)}/access`,
      {},
      { timeoutMs: 30_000 },
    );
    return response.data;
  }
}

function handwritingCandidate(revision: RecognitionRevision): RecognitionCandidatePayload {
  const payload = revision.candidatePayload;
  if (
    payload.kind !== "handwriting_ocr" ||
    typeof payload.processorRequestIdHash !== "string" ||
    typeof payload.processorOutputContract !== "string" ||
    typeof payload.requestHash !== "string" ||
    !Array.isArray(payload.textSpans)
  ) {
    throw new ApiClientError(
      "申论手写 OCR 版本缺少可核验的逐段文本。",
      409,
      "essay-handwriting-contract-invalid",
    );
  }
  return payload as unknown as RecognitionCandidatePayload;
}

function assertEssayHandwriting(revision: RecognitionRevision): void {
  if (revision.recognitionKind !== "handwriting_ocr") {
    throw new ApiClientError(
      "非申论手写识别结果不能进入申论作答。",
      409,
      "non-essay-recognition-rejected",
    );
  }
}

function derivedKey(base: string, suffix: string): string {
  const combined = `${base}:${suffix}`;
  return combined.length <= 160 ? combined : base;
}
