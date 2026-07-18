import type { ApiEnvelope } from "../domain/contracts";
import type { CompanionAskRequest, CompanionAskResponse } from "./generated/types.gen";
import type { ApiClient } from "./client";

export class CompanionApi {
  constructor(private readonly client: ApiClient) {}

  ask(
    command: CompanionAskRequest,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<CompanionAskResponse>> {
    return this.client.post<CompanionAskResponse>("/api/v2/companion/ask", command, {
      idempotencyKey,
      timeoutMs: 45_000,
    });
  }
}
