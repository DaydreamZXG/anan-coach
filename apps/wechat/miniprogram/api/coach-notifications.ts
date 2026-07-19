import type { ApiEnvelope } from "../domain/contracts";
import type {
  CoachNotificationOpenView,
  CoachNotificationSettings,
  CoachPreferenceView,
  DisableCoachNotificationsCommand,
  UpdateCoachPreferenceCommand,
  WechatSubscriptionEventCommand,
  WechatSubscriptionEventView,
} from "./generated/types.gen";
import type { ApiClient } from "./client";

export class CoachNotificationApi {
  constructor(private readonly client: ApiClient) {}

  settings(): Promise<ApiEnvelope<CoachNotificationSettings>> {
    return this.client.get<CoachNotificationSettings>("/api/v2/coach-notifications/settings");
  }

  updatePreference(
    command: UpdateCoachPreferenceCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<CoachPreferenceView>> {
    return this.client.put<CoachPreferenceView>(
      "/api/v2/coach-notifications/preferences",
      command,
      {
        idempotencyKey,
      },
    );
  }

  disable(
    command: DisableCoachNotificationsCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<CoachPreferenceView>> {
    return this.client.post<CoachPreferenceView>(
      "/api/v2/coach-notifications/preferences/disable",
      command,
      { idempotencyKey },
    );
  }

  recordWechatSubscription(
    command: WechatSubscriptionEventCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<WechatSubscriptionEventView>> {
    return this.client.post<WechatSubscriptionEventView>(
      "/api/v2/coach-notifications/wechat-subscriptions",
      command,
      { idempotencyKey },
    );
  }

  recordOpen(
    deliveryId: string,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<CoachNotificationOpenView>> {
    return this.client.post<CoachNotificationOpenView>(
      `/api/v2/coach-notifications/deliveries/${encodeURIComponent(deliveryId)}/opened`,
      {},
      { idempotencyKey },
    );
  }
}
