import type { ApiEnvelope } from "../domain/contracts";
import type {
  AccountOverview,
  AccountProfileUpdate,
  AccountProfileView,
  LogoutAllResult,
  SessionRevocationResult,
} from "./generated/types.gen";
import type { ApiClient } from "./client";

export class AccountApi {
  constructor(private readonly client: ApiClient) {}

  getOverview(): Promise<ApiEnvelope<AccountOverview>> {
    return this.client.get<AccountOverview>("/api/v2/auth/account");
  }

  updateProfile(command: AccountProfileUpdate): Promise<ApiEnvelope<AccountProfileView>> {
    return this.client.put<AccountProfileView>("/api/v2/auth/account/profile", command);
  }

  revokeSession(sessionId: string): Promise<ApiEnvelope<SessionRevocationResult>> {
    return this.client.delete<SessionRevocationResult>(
      `/api/v2/auth/sessions/${encodeURIComponent(sessionId)}`,
    );
  }

  logoutCurrent(): Promise<ApiEnvelope<LogoutAllResult>> {
    return this.client.post<LogoutAllResult>("/api/v2/auth/logout", {});
  }

  logoutAll(): Promise<ApiEnvelope<LogoutAllResult>> {
    return this.client.post<LogoutAllResult>("/api/v2/auth/logout-all", {});
  }
}
