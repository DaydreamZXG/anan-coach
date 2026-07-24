import type { ApiClient } from "./client";
import type {
  LearningMediaArtifact as GeneratedLearningMediaArtifact,
  LearningMediaAuthorization as GeneratedLearningMediaAuthorization,
  LearningMediaUploadProjection as GeneratedLearningMediaUploadProjection,
  LearningMediaUploadSession as GeneratedLearningMediaUploadSession,
  StartLearningMediaUploadCommand as GeneratedStartLearningMediaUploadCommand,
} from "./generated/types.gen";

export const learningMediaContractVersion = "learning-media-upload@v1" as const;

export type LearningMediaPurpose = GeneratedStartLearningMediaUploadCommand["purpose"];
export type LearningMediaMimeType = GeneratedStartLearningMediaUploadCommand["mimeType"];
export type LearningMediaUploadState = GeneratedLearningMediaUploadSession["state"];
export type StartLearningMediaUploadCommand = GeneratedStartLearningMediaUploadCommand;
export type LearningMediaAuthorization = GeneratedLearningMediaAuthorization;
export type LearningMediaUploadSession = GeneratedLearningMediaUploadSession;
export type LearningMediaArtifact = GeneratedLearningMediaArtifact;
export type LearningMediaUploadProjection = GeneratedLearningMediaUploadProjection;

export class LearningMediaApi {
  constructor(private readonly client: ApiClient) {}

  async startUpload(
    command: StartLearningMediaUploadCommand,
    idempotencyKey: string,
  ): Promise<LearningMediaUploadProjection> {
    const response = await this.client.post<LearningMediaUploadProjection>(
      "/api/v2/learning-media/uploads",
      command,
      { idempotencyKey, timeoutMs: 30_000 },
    );
    return response.data;
  }

  async completeUpload(
    uploadSessionId: string,
    idempotencyKey: string,
  ): Promise<LearningMediaUploadProjection> {
    const response = await this.client.post<LearningMediaUploadProjection>(
      `/api/v2/learning-media/uploads/${encodeURIComponent(uploadSessionId)}/complete`,
      {},
      { idempotencyKey, timeoutMs: 30_000 },
    );
    return response.data;
  }

  async getUpload(uploadSessionId: string): Promise<LearningMediaUploadProjection> {
    const response = await this.client.get<LearningMediaUploadProjection>(
      `/api/v2/learning-media/uploads/${encodeURIComponent(uploadSessionId)}`,
      { timeoutMs: 15_000 },
    );
    return response.data;
  }
}

export function putPrivateLearningMedia(
  authorization: LearningMediaAuthorization,
  bytes: ArrayBuffer,
): Promise<void> {
  if (!authorization.url.trim()) throw new Error("录音上传授权地址无效。");
  return new Promise((resolve, reject) => {
    wx.request<ArrayBuffer>({
      url: authorization.url,
      method: "PUT",
      data: bytes,
      header: { ...authorization.requiredHeaders },
      timeout: 60_000,
      success: (response) => {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve();
          return;
        }
        reject(new Error(`录音对象上传失败（${String(response.statusCode)}）。`));
      },
      fail: () => {
        reject(new Error("录音对象上传网络中断。"));
      },
    });
  });
}
