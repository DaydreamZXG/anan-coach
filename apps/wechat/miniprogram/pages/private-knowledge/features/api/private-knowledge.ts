import type { ApiClient } from "../../../../api/client";
import type { RecognitionRevision } from "../../../../api/external-learning-contracts";
import { SourceArtifactApi } from "../../../../api/source-artifact";
import type {
  LocalSourceArtifactFile,
  UploadProgress,
} from "../../../../features/external-learning/contracts";

export interface PrivateSourceSummary {
  sourceId: string;
  sourceVersionId: string;
  versionNo: number;
  title: string;
  documentKind: "lecture" | "note" | "summary";
  artifactVersionId: string;
  recognitionRevisionId: string | null;
  pageCount: number;
  chunkCount: number;
  contentSha256: string;
  createdAt: string;
}

export interface PrivateSourceList {
  sources: PrivateSourceSummary[];
  generatedAt: string;
}

export interface PrivateCitation {
  sourceId: string;
  sourceVersionId: string;
  chunkId: string;
  pageNo: number;
  anchor: string;
  title: string;
  excerpt: string;
  lexicalScore: number;
  vectorScore: number;
  finalScore: number;
}

export interface PrivateSearchResult {
  queryFingerprint: string;
  policyVersion: "private-knowledge-search@v1";
  citations: PrivateCitation[];
}

export interface MarkdownExport {
  sourceId: string;
  sourceVersionId: string;
  versionNo: number;
  filename: string;
  content: string;
  contentSha256: string;
}

export interface PrivateSourceDeletionView {
  deletionRequestId: string;
  sourceId: string;
  state: "queued" | "running" | "completed" | "failed";
  sourceVersionCount: number;
  failureCode: string | null;
  requestedAt: string;
  completedAt: string | null;
}

export interface CreatePrivateSourceCommand {
  artifactVersionId: string;
  recognitionRevisionId: string | null;
  title: string;
  documentKind: "lecture" | "note" | "summary";
  sourceId?: string;
  expectedVersionNo?: number;
}

export class PrivateKnowledgeApi {
  private readonly artifacts: SourceArtifactApi;

  constructor(private readonly client: ApiClient) {
    this.artifacts = new SourceArtifactApi(client);
  }

  async upload(
    file: LocalSourceArtifactFile,
    idempotencyKey: string,
    onProgress: (progress: UploadProgress) => void,
  ) {
    return this.artifacts.upload(file, idempotencyKey, onProgress);
  }

  async recognizePrinted(artifactVersionId: string, idempotencyKey: string) {
    const response = await this.client.post<RecognitionRevision>(
      "/api/v2/external-learning/recognitions",
      { artifactVersionId, recognitionKind: "printed_ocr", packetVersionId: null },
      { idempotencyKey, timeoutMs: 60_000 },
    );
    return response.data;
  }

  async correctRecognition(
    revision: RecognitionRevision,
    candidatePayload: Record<string, unknown>,
    idempotencyKey: string,
  ) {
    const response = await this.client.put<RecognitionRevision>(
      `/api/v2/external-learning/recognitions/${encodeURIComponent(revision.recognitionRevisionId)}/candidate`,
      {
        expectedRevisionNo: revision.revisionNo,
        correctedPayload: candidatePayload,
        reason: "用户逐段核对私人资料 OCR",
      },
      { idempotencyKey, timeoutMs: 30_000 },
    );
    return response.data;
  }

  async confirmRecognition(revision: RecognitionRevision, idempotencyKey: string) {
    const response = await this.client.post<RecognitionRevision>(
      `/api/v2/external-learning/recognitions/${encodeURIComponent(revision.recognitionRevisionId)}/confirm`,
      { expectedRevisionNo: revision.revisionNo },
      { idempotencyKey, timeoutMs: 30_000 },
    );
    return response.data;
  }

  async createSource(command: CreatePrivateSourceCommand, idempotencyKey: string) {
    const response = await this.client.post<PrivateSourceSummary>(
      "/api/v2/private-knowledge/sources",
      command,
      { idempotencyKey, timeoutMs: 60_000 },
    );
    return response.data;
  }

  async listSources() {
    return (await this.client.get<PrivateSourceList>("/api/v2/private-knowledge/sources")).data;
  }

  async search(query: string) {
    return (
      await this.client.get<PrivateSearchResult>(
        `/api/v2/private-knowledge/search?q=${encodeURIComponent(query)}&limit=8`,
      )
    ).data;
  }

  async exportMarkdown(sourceId: string) {
    return (
      await this.client.get<MarkdownExport>(
        `/api/v2/private-knowledge/sources/${encodeURIComponent(sourceId)}/markdown`,
      )
    ).data;
  }

  async deleteSource(sourceId: string, idempotencyKey: string) {
    return (
      await this.client.delete<PrivateSourceDeletionView>(
        `/api/v2/private-knowledge/sources/${encodeURIComponent(sourceId)}`,
        { idempotencyKey, timeoutMs: 30_000 },
      )
    ).data;
  }

  async deletionStatus(deletionRequestId: string) {
    return (
      await this.client.get<PrivateSourceDeletionView>(
        `/api/v2/private-knowledge/deletions/${encodeURIComponent(deletionRequestId)}`,
      )
    ).data;
  }
}
