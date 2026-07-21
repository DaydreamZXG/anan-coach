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
}
