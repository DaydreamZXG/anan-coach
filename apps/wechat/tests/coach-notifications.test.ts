import { afterEach, describe, expect, it, vi } from "vitest";
import { CoachNotificationApi } from "../miniprogram/api/coach-notifications";
import { ApiClient } from "../miniprogram/api/client";
import { recordCoachNotificationOpen } from "../miniprogram/features/coach-notifications/open-tracker";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("coach notifications", () => {
  it("uses authenticated, idempotent settings and open endpoints", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.header).toMatchObject({ Authorization: "Bearer token-1" });
      if (options.method === "PUT") {
        expect(options.url).toBe("https://api.example.test/api/v2/coach-notifications/preferences");
        expect(options.header).toMatchObject({ "Idempotency-Key": "coach-save-1" });
        respond(options, preference());
        return;
      }
      expect(options.url).toBe(
        "https://api.example.test/api/v2/coach-notifications/deliveries/00000000-0000-4000-8000-000000000001/opened",
      );
      expect(options.header).toMatchObject({ "Idempotency-Key": "coach-open-1" });
      respond(options, {
        deliveryId: "00000000-0000-4000-8000-000000000001",
        openedAt: "2026-07-19T10:31:00+08:00",
        taskCompletionChanged: false,
      });
    });
    vi.stubGlobal("wx", { request });
    const api = new CoachNotificationApi(new ApiClient("https://api.example.test", "token-1"));

    await api.updatePreference(
      {
        basedOnPreferenceVersionId: "00000000-0000-4000-8000-000000000010",
        enabled: true,
        taskRemindersEnabled: true,
        reflectionRemindersEnabled: true,
        retestRemindersEnabled: true,
        mockRemindersEnabled: true,
        weeklySummaryEnabled: true,
        receiveStart: "07:30:00",
        receiveEnd: "22:30:00",
        quietEnabled: false,
        quietStart: null,
        quietEnd: null,
        dailyLimit: 2,
        timezone: "Asia/Shanghai",
      },
      "coach-save-1",
    );
    const opened = await api.recordOpen("00000000-0000-4000-8000-000000000001", "coach-open-1");

    expect(opened.data.taskCompletionChanged).toBe(false);
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("records a message deep-link open once and leaves task completion untouched", async () => {
    const storage = new Map<string, unknown>();
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      respond(options, {
        deliveryId: "00000000-0000-4000-8000-000000000001",
        openedAt: "2026-07-19T10:31:00+08:00",
        taskCompletionChanged: false,
      });
    });
    vi.stubGlobal("getApp", () => ({
      globalData: { accessToken: "token-1", userId: "user-1" },
    }));
    vi.stubGlobal("wx", {
      request,
      getStorageSync: (key: string) => storage.get(key) ?? "",
      setStorageSync: (key: string, value: unknown) => storage.set(key, value),
      removeStorageSync: (key: string) => storage.delete(key),
    });
    const options = {
      query: { notificationDeliveryId: "00000000-0000-4000-8000-000000000001" },
    };

    await recordCoachNotificationOpen(options);
    await recordCoachNotificationOpen(options);

    expect(request).toHaveBeenCalledTimes(1);
  });

  it("persists the exact requestSubscribeMessage result as one-time authorization", async () => {
    let captured: unknown;
    const requests: WechatMiniprogram.RequestOption[] = [];
    vi.stubGlobal("getApp", () => ({
      globalData: { accessToken: "token-1", userId: "user-1" },
    }));
    vi.stubGlobal("Page", (definition: unknown) => {
      captured = definition;
    });
    vi.stubGlobal("wx", {
      canIUse: () => true,
      requestSubscribeMessage: (options: WechatMiniprogram.RequestSubscribeMessageOption) => {
        options.success?.({
          errMsg: "requestSubscribeMessage:ok",
          "template-id-1": "accept",
        });
      },
      getRandomValues: () => Promise.resolve({ randomValues: new Uint8Array(16).buffer }),
      request: (options: WechatMiniprogram.RequestOption) => {
        requests.push(options);
        if (options.method === "POST") {
          expect(options.data).toEqual({
            source: "request_prompt",
            templateCode: "coach_reminder",
            templateId: "template-id-1",
            result: "accept",
          });
          respond(options, {
            eventId: "00000000-0000-4000-8000-000000000020",
            source: "request_prompt",
            templateCode: "coach_reminder",
            result: "accept",
            grantCreated: true,
            availableGrants: 1,
            occurredAt: "2026-07-19T10:30:00+08:00",
          });
          return;
        }
        respond(options, settings());
      },
      getStorageSync: () => "",
      removeStorageSync: () => undefined,
    });
    await import("../miniprogram/pages/notification-settings/index");
    const definition = captured as Record<string, unknown> & {
      data: Record<string, unknown>;
      requestWechatAuthorization(): void;
    };
    const instance = pageInstance(definition);
    instance.data.templateConfigured = true;
    instance.data.templateId = "template-id-1";

    instance.requestWechatAuthorization();
    await vi.waitFor(() => {
      expect(requests).toHaveLength(2);
    });

    expect(headerValue(requests[0], "Idempotency-Key")).toMatch(
      /^coach-subscription-request-prompt-/u,
    );
  });
});

function pageInstance<T extends Record<string, unknown> & { data: Record<string, unknown> }>(
  definition: T,
): T & { setData(update: Record<string, unknown>): void } {
  const instance = {
    ...definition,
    data: JSON.parse(JSON.stringify(definition.data)) as Record<string, unknown>,
    setData(update: Record<string, unknown>) {
      for (const [path, value] of Object.entries(update)) setPath(this.data, path, value);
    },
  };
  return instance;
}

function headerValue(
  option: WechatMiniprogram.RequestOption | undefined,
  key: string,
): string | undefined {
  const header: unknown = option?.header;
  if (typeof header !== "object" || header === null || Array.isArray(header)) return undefined;
  const value = (header as Record<string, unknown>)[key];
  return typeof value === "string" ? value : undefined;
}

function setPath(target: Record<string, unknown>, path: string, value: unknown): void {
  const parts = path.split(".");
  let current = target;
  for (const part of parts.slice(0, -1)) {
    const next = current[part];
    if (!next || typeof next !== "object") current[part] = {};
    current = current[part] as Record<string, unknown>;
  }
  const last = parts.at(-1);
  if (last) current[last] = value;
}

function respond(options: WechatMiniprogram.RequestOption, data: object): void {
  options.success?.({
    data: { data, requestId: "coach-request-1", generatedAt: "2026-07-19T02:30:00Z" },
    statusCode: 200,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  });
}

function preference() {
  return {
    preferenceVersionId: "00000000-0000-4000-8000-000000000010",
    versionNo: 2,
    enabled: true,
    taskRemindersEnabled: true,
    reflectionRemindersEnabled: true,
    retestRemindersEnabled: true,
    mockRemindersEnabled: true,
    weeklySummaryEnabled: true,
    receiveStart: "07:30:00",
    receiveEnd: "22:30:00",
    quietEnabled: false,
    quietStart: null,
    quietEnd: null,
    dailyLimit: 2,
    timezone: "Asia/Shanghai",
    sourceType: "user_confirmed",
    createdAt: "2026-07-19T02:30:00Z",
  } as const;
}

function settings() {
  return {
    preference: preference(),
    template: {
      templateCode: "coach_reminder",
      title: "岸岸 AI 督学提醒",
      authorizationMode: "one_time",
      configured: true,
      templateId: "template-id-1",
      releaseId: "release-1",
      availableGrants: 1,
      lastResult: "accept",
      lastResultSource: "request_prompt",
    },
    recentDeliveries: [],
    deliveryFactNote: "发送、打开和完成分别记录。",
  } as const;
}
