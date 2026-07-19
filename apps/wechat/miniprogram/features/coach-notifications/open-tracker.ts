import { CoachNotificationApi } from "../../api/coach-notifications";
import { ApiClient } from "../../api/client";
import { currentAccessToken } from "../../auth/session";

interface LaunchQueryOptions {
  query?: Record<string, string | undefined>;
}

const deliveryPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;

export async function recordCoachNotificationOpen(options: LaunchQueryOptions): Promise<void> {
  const deliveryId = options.query?.notificationDeliveryId?.trim() ?? "";
  if (!deliveryPattern.test(deliveryId)) return;
  const token = currentAccessToken();
  if (!token) return;
  const storageKey = `gongkao.coach.opened.v1.${deliveryId}`;
  if (wx.getStorageSync<boolean>(storageKey)) return;
  await new CoachNotificationApi(new ApiClient(undefined, token)).recordOpen(
    deliveryId,
    `coach-open-${deliveryId}`,
  );
  wx.setStorageSync(storageKey, true);
}
