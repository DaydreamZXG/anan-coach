import type { ApiClient } from "../../../api/client";
import type {
  RetakeEnrollmentRequest,
  RetakeEnrollmentResult,
  RetakeWorkspace,
} from "../../../api/generated/types.gen";
import type { ApiEnvelope } from "../../../domain/contracts";

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
