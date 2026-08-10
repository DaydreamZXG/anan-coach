import type { ApiEnvelope } from "../domain/contracts";
import type {
  CompanionAskRequest,
  CompanionAskResponse,
  CompanionMemoryItem,
  CompanionMemoryMutationResponse,
  CompanionMemorySettingRequest,
  CompanionMemorySettingResponse,
  CompanionMemoryState,
  CompanionMemoryUpdateRequest,
  CompanionMemoryWriteRequest,
} from "./generated/types.gen";
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

  getMemories(): Promise<ApiEnvelope<CompanionMemoryState>> {
    return this.client.get<CompanionMemoryState>("/api/v2/companion/memories");
  }

  createMemory(
    command: CompanionMemoryWriteRequest,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<CompanionMemoryItem>> {
    return this.client.post<CompanionMemoryItem>("/api/v2/companion/memories", command, {
      idempotencyKey,
    });
  }

  updateMemory(
    memoryId: string,
    command: CompanionMemoryUpdateRequest,
  ): Promise<ApiEnvelope<CompanionMemoryItem>> {
    return this.client.put<CompanionMemoryItem>(
      `/api/v2/companion/memories/${encodeURIComponent(memoryId)}`,
      command,
    );
  }

  deleteMemory(memoryId: string): Promise<ApiEnvelope<CompanionMemoryMutationResponse>> {
    return this.client.delete<CompanionMemoryMutationResponse>(
      `/api/v2/companion/memories/${encodeURIComponent(memoryId)}`,
    );
  }

  clearMemories(): Promise<ApiEnvelope<CompanionMemoryMutationResponse>> {
    return this.client.delete<CompanionMemoryMutationResponse>("/api/v2/companion/memories");
  }

  setMemoryEnabled(
    command: CompanionMemorySettingRequest,
  ): Promise<ApiEnvelope<CompanionMemorySettingResponse>> {
    return this.client.put<CompanionMemorySettingResponse>(
      "/api/v2/companion/memory-settings",
      command,
    );
  }

  clearConversationMemory(): Promise<ApiEnvelope<CompanionMemoryMutationResponse>> {
    return this.client.delete<CompanionMemoryMutationResponse>(
      "/api/v2/companion/conversation-memory",
    );
  }
}
