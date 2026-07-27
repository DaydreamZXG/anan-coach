import type { ApiEnvelope } from "../domain/contracts";
import type { ApiClient } from "./client";

export type ILinkState = "unbound" | "pending" | "bound" | "revoked";

export interface ILinkChannelView {
  linkState: ILinkState;
  linkId: string | null;
  bindingCode: string | null;
  expiresAt: string | null;
  queryCount: number;
  collectionCount: number;
  usageCopy: string;
  evidenceCopy: string;
}

export type HubBotState =
  "unbound" | "waiting_scan" | "scanned" | "connected" | "reauth_required" | "revoked";

export interface HubBotAuthorizationView {
  state: HubBotState;
  authorizationId: string | null;
  qrImageDataUrl: string | null;
  expiresAt: string | null;
  statusCopy: string;
  usageCopy: string;
}

const ROOT = "/api/v2/ilink-channel";

export class ILinkChannelApi {
  constructor(private readonly client: ApiClient) {}

  get(): Promise<ApiEnvelope<ILinkChannelView>> {
    return this.client.get<ILinkChannelView>(ROOT);
  }

  issue(replacePending: boolean, idempotencyKey: string): Promise<ApiEnvelope<ILinkChannelView>> {
    return this.client.post<ILinkChannelView>(
      `${ROOT}/link-codes`,
      { replacePending },
      { idempotencyKey },
    );
  }

  revoke(linkId: string, idempotencyKey: string): Promise<ApiEnvelope<ILinkChannelView>> {
    return this.client.post<ILinkChannelView>(
      `${ROOT}/links/${encodeURIComponent(linkId)}/revoke`,
      { expectedLinkId: linkId },
      { idempotencyKey },
    );
  }

  getHubBot(): Promise<ApiEnvelope<HubBotAuthorizationView>> {
    return this.client.get<HubBotAuthorizationView>(`${ROOT}/bot`);
  }

  startHubBot(
    replacePending: boolean,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<HubBotAuthorizationView>> {
    return this.client.post<HubBotAuthorizationView>(
      `${ROOT}/bot/authorizations`,
      { replacePending },
      { idempotencyKey },
    );
  }

  pollHubBot(authorizationId: string): Promise<ApiEnvelope<HubBotAuthorizationView>> {
    return this.client.get<HubBotAuthorizationView>(
      `${ROOT}/bot/authorizations/${encodeURIComponent(authorizationId)}`,
      { timeoutMs: 50_000 },
    );
  }

  revokeHubBot(idempotencyKey: string): Promise<ApiEnvelope<HubBotAuthorizationView>> {
    return this.client.delete<HubBotAuthorizationView>(`${ROOT}/bot`, {
      idempotencyKey,
    });
  }
}
