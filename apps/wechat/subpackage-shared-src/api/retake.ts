import type { ApiClient } from "../../miniprogram/api/client";
import type {
  RetakeEnrollmentRequest,
  RetakeEnrollmentResult,
  RetakeWorkspace,
} from "../../miniprogram/api/generated/types.gen";
import type { ApiEnvelope } from "../../miniprogram/domain/contracts";

export class RetakeApi {
  constructor(private readonly client: ApiClient) {}

  getWorkspace(): Promise<ApiEnvelope<RetakeWorkspace>> {
    return this.client.get<RetakeWorkspace>("/api/v2/retake");
  }

  startProgram(
    request: RetakeEnrollmentRequest,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<RetakeEnrollmentResult>> {
    return this.client.post<RetakeEnrollmentResult>("/api/v2/retake/programs", request, {
      idempotencyKey,
    });
  }
}
