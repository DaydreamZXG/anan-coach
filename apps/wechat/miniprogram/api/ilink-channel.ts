import type { ApiEnvelope } from "../domain/contracts";
import type { ApiClient } from "./client";
import type {
  HubBotAuthorizationView as GeneratedHubBotAuthorizationView,
  ILinkChannelView as GeneratedILinkChannelView,
  IssueLinkCommand,
  RevokeLinkCommand,
  StartHubBotAuthorizationCommand,
} from "./generated/types.gen";

export type ILinkChannelView = GeneratedILinkChannelView;
export type ILinkState = ILinkChannelView["linkState"];
export type HubBotAuthorizationView = GeneratedHubBotAuthorizationView;
export type HubBotState = HubBotAuthorizationView["state"];

const ROOT = "/api/v2/ilink-channel";

export class ILinkChannelApi {
  constructor(private readonly client: ApiClient) {}

  get(): Promise<ApiEnvelope<ILinkChannelView>> {
    return this.client.get<ILinkChannelView>(ROOT);
  }

  issue(replacePending: boolean, idempotencyKey: string): Promise<ApiEnvelope<ILinkChannelView>> {
    const command: IssueLinkCommand = { replacePending };
    return this.client.post<ILinkChannelView>(`${ROOT}/link-codes`, command, { idempotencyKey });
  }

  revoke(linkId: string, idempotencyKey: string): Promise<ApiEnvelope<ILinkChannelView>> {
    const command: RevokeLinkCommand = { expectedLinkId: linkId };
    return this.client.post<ILinkChannelView>(
      `${ROOT}/links/${encodeURIComponent(linkId)}/revoke`,
      command,
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
    const command: StartHubBotAuthorizationCommand = { replacePending };
    return this.client.post<HubBotAuthorizationView>(`${ROOT}/bot/authorizations`, command, {
      idempotencyKey,
    });
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
