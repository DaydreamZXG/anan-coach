import { afterEach, describe, expect, it, vi } from "vitest";
import { CoachNotificationApi } from "../miniprogram/api/coach-notifications";
import { ApiClient } from "../miniprogram/api/client";
import { recordCoachNotificationOpen } from "../miniprogram/features/coach-notifications/open-tracker";
import { OperationAttemptStore } from "../miniprogram/platform/operation-attempt-store";

const coachPreferenceAttemptScope = "coach-preference:update@v1";

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
    vi.stubGlobal("getApp", () => ({
      globalData: { accessToken: "token-1", userId: "user-1", generation: "generation-1" },
    }));
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
      globalData: { accessToken: "token-1", userId: "user-1", generation: "generation-1" },
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
      globalData: { accessToken: "token-1", userId: "user-1", generation: "generation-1" },
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

  it("reuses an unknown preference attempt key and ignores duplicate taps", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        failRequest(options);
      },
    });

    const first = page.save();
    const duplicate = page.save();
    await Promise.all([first, duplicate]);
    await page.save();

    expect(requests).toHaveLength(2);
    const replayKey = headerValue(requests[0], "Idempotency-Key");
    expect(replayKey).toMatch(/^coach-preference-/u);
    expect(headerValue(requests[1], "Idempotency-Key")).toBe(replayKey);
    expect(page.data.message).toBe("保存结果待确认，请再次保存以确认结果。");
    const attempt = new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope);
    expect(attempt?.state).toBe("unknown");
    expect(attempt?.canonicalInput).toEqual(requests[0]?.data);
    expect(JSON.stringify([...page.storage.values()])).not.toContain("token-1");
  });

  it("replays the original payload and key before loading settings after a lost response", async () => {
    const updateRequests: WechatMiniprogram.RequestOption[] = [];
    const originalVersionId = "00000000-0000-4000-8000-000000000010";
    const recoveredVersionId = "00000000-0000-4000-8000-000000000011";
    let serverVersionId = originalVersionId;
    const page = await notificationSettingsPage({
      request: (options) => {
        if (options.method === "GET") {
          respond(options, settings(serverVersionId));
          return;
        }
        updateRequests.push(options);
        if (updateRequests.length === 1) {
          serverVersionId = recoveredVersionId;
          failRequest(options);
          return;
        }
        respond(options, preference(serverVersionId));
      },
    });

    await page.save();
    expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
      "unknown",
    );
    const recoveredPage = page.instantiatePage();
    recoveredPage.onShow();
    await vi.waitFor(() => {
      expect(recoveredPage.data.status).toBe("ready");
    });

    expect(updateRequests).toHaveLength(2);
    const originalKey = headerValue(updateRequests[0], "Idempotency-Key");
    expect(originalKey).toMatch(/^coach-preference-/u);
    expect(headerValue(updateRequests[1], "Idempotency-Key")).toBe(originalKey);
    expect(
      new Set(updateRequests.map((request) => headerValue(request, "Idempotency-Key"))),
    ).toEqual(new Set([originalKey]));
    expect(updateRequests[1]?.data).toEqual(updateRequests[0]?.data);
    expect(updateRequests[1]?.data).toMatchObject({
      basedOnPreferenceVersionId: originalVersionId,
    });
    expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
      "succeeded",
    );
    expect((recoveredPage.data.draft as { preferenceVersionId: string }).preferenceVersionId).toBe(
      recoveredVersionId,
    );
  });

  it("coalesces concurrent same-session recovery loads and keeps success authoritative", async () => {
    const updates: WechatMiniprogram.RequestOption[] = [];
    const gets: WechatMiniprogram.RequestOption[] = [];
    let pendingRecovery: WechatMiniprogram.RequestOption | undefined;
    const page = await notificationSettingsPage({
      request: (options) => {
        if (options.method === "GET") {
          gets.push(options);
          respond(options, settings());
          return;
        }
        updates.push(options);
        if (updates.length === 1) {
          failRequest(options);
          return;
        }
        pendingRecovery = options;
      },
    });
    await page.save();
    const recoveredPage = page.instantiatePage();

    const firstLoad = recoveredPage.loadSettings();
    const duplicateLoad = recoveredPage.loadSettings();
    expect(duplicateLoad).toBe(firstLoad);
    await vi.waitFor(() => {
      expect(pendingRecovery).toBeDefined();
    });
    await recoveredPage.save();
    expect(updates).toHaveLength(2);
    if (!pendingRecovery) throw new Error("expected one recovery replay");
    respond(pendingRecovery, preference());
    await Promise.all([firstLoad, duplicateLoad]);

    expect(updates).toHaveLength(2);
    expect(headerValue(updates[1], "Idempotency-Key")).toBe(
      headerValue(updates[0], "Idempotency-Key"),
    );
    expect(gets).toHaveLength(1);
    expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
      "succeeded",
    );
    expect(recoveredPage.data.status).toBe("ready");
  });

  it("lets a replacement session supersede recovery without downgrading a succeeded attempt", async () => {
    const updates: WechatMiniprogram.RequestOption[] = [];
    let oldRecovery: WechatMiniprogram.RequestOption | undefined;
    let replacementRecovery: WechatMiniprogram.RequestOption | undefined;
    const page = await notificationSettingsPage({
      request: (options) => {
        if (options.method === "GET") {
          respond(options, settings());
          return;
        }
        updates.push(options);
        if (updates.length === 1) {
          failRequest(options);
        } else if (updates.length === 2) {
          oldRecovery = options;
        } else {
          replacementRecovery = options;
        }
      },
    });
    await page.save();
    const recoveredPage = page.instantiatePage();
    const oldLoad = recoveredPage.loadSettings();
    await vi.waitFor(() => {
      expect(oldRecovery).toBeDefined();
    });

    recoveredPage.globalData.generation = "generation-replacement";
    recoveredPage.onShow();
    await vi.waitFor(() => {
      expect(replacementRecovery).toBeDefined();
    });
    const replacementLoad = recoveredPage.loadSettings();
    expect(updates).toHaveLength(3);
    if (!oldRecovery || !replacementRecovery) {
      throw new Error("expected both recovery requests");
    }
    respond(oldRecovery, preference());
    await vi.waitFor(() => {
      expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
        "succeeded",
      );
    });
    failRequest(replacementRecovery);
    await Promise.all([oldLoad, replacementLoad]);

    expect(updates).toHaveLength(3);
    expect(new Set(updates.map((request) => headerValue(request, "Idempotency-Key"))).size).toBe(1);
    expect(updates[2]?.header).toMatchObject({ Authorization: "Bearer token-1" });
    expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
      "succeeded",
    );
    expect(recoveredPage.data.status).toBe("ready");
    expect(recoveredPage.data.message).toBe("已确认上次保存成功，并恢复最新督学偏好。");
  });

  it("keeps an HTTP 500 recovery unknown and does not load over the unresolved result", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    let updateCount = 0;
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        updateCount += 1;
        if (updateCount === 1) {
          failRequest(options);
          return;
        }
        respondError(options, 500, "服务暂时不可用。");
      },
    });

    await page.save();
    const recoveredPage = page.instantiatePage();
    recoveredPage.onShow();
    await vi.waitFor(() => {
      expect(recoveredPage.data.status).toBe("error");
    });

    expect(requests).toHaveLength(2);
    expect(headerValue(requests[0], "Idempotency-Key")).toMatch(/^coach-preference-/u);
    expect(headerValue(requests[1], "Idempotency-Key")).toBe(
      headerValue(requests[0], "Idempotency-Key"),
    );
    expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
      "unknown",
    );
    expect(recoveredPage.data.message).toBe("保存结果待确认，请再次保存以确认结果。");
    expect(requests.some((request) => request.method === "GET")).toBe(false);
  });

  it("keeps HTTP 408 unknown and reuses the original key", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        respondError(options, 408, "请求处理超时。");
      },
    });

    await page.save();
    await page.save();

    expect(requests).toHaveLength(2);
    expect(headerValue(requests[0], "Idempotency-Key")).toMatch(/^coach-preference-/u);
    expect(headerValue(requests[1], "Idempotency-Key")).toBe(
      headerValue(requests[0], "Idempotency-Key"),
    );
    expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
      "unknown",
    );
    expect(page.data.message).toBe("保存结果待确认，请再次保存以确认结果。");
  });

  it("keeps a recovery HTTP 408 unknown without loading settings", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        if (requests.length === 1) {
          failRequest(options);
          return;
        }
        respondError(options, 408, "请求处理超时。");
      },
    });
    await page.save();
    const recoveredPage = page.instantiatePage();

    await recoveredPage.loadSettings();
    await recoveredPage.loadSettings();

    expect(requests).toHaveLength(3);
    expect(new Set(requests.map((request) => headerValue(request, "Idempotency-Key"))).size).toBe(
      1,
    );
    expect(requests.some((request) => request.method === "GET")).toBe(false);
    expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
      "unknown",
    );
    expect(recoveredPage.data).toMatchObject({
      status: "error",
      requestId: "coach-request-error",
      message: "保存结果待确认，请再次保存以确认结果。",
    });
  });

  it("marks a deterministic recovery conflict terminal before loading current settings", async () => {
    const methods: Array<string | undefined> = [];
    let updateCount = 0;
    const page = await notificationSettingsPage({
      request: (options) => {
        methods.push(options.method);
        if (options.method === "GET") {
          respond(options, settings());
          return;
        }
        updateCount += 1;
        if (updateCount === 1) {
          failRequest(options);
          return;
        }
        respondError(options, 409, "偏好版本已更新，请重新保存。");
      },
    });

    await page.save();
    const recoveredPage = page.instantiatePage();
    recoveredPage.onShow();
    await vi.waitFor(() => {
      expect(recoveredPage.data.status).toBe("ready");
    });

    expect(methods).toEqual(["PUT", "PUT", "GET"]);
    expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
      "terminal",
    );
    expect(recoveredPage.data.message).toBe("偏好版本已更新，请重新保存。");
  });

  it("terminalizes an invalid recovery payload and does not retry it on the next load", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        respond(options, settings());
      },
    });
    const attempts = new OperationAttemptStore("user-a");
    const invalid = await attempts.begin({
      scope: coachPreferenceAttemptScope,
      canonicalInput: { malformed: true },
      idempotencyKeyPrefix: "coach-preference",
      expiresAtMs: Date.now() + 60_000,
    });
    attempts.markTransportUnknown(coachPreferenceAttemptScope, invalid.idempotencyKey);

    await page.loadSettings();
    expect(attempts.load(coachPreferenceAttemptScope)?.state).toBe("terminal");
    expect(page.data.status).toBe("ready");
    expect(page.data.message).toBe("旧恢复记录无法校验，已停止重试。");
    await page.loadSettings();

    expect(requests.map((request) => request.method)).toEqual(["GET", "GET"]);
    expect(attempts.load(coachPreferenceAttemptScope)?.state).toBe("terminal");
  });

  it("marks successful and explicit HTTP-failed preference attempts terminally", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    let updateCount = 0;
    let settingsVersionId = "00000000-0000-4000-8000-000000000010";
    const refreshedVersionId = "00000000-0000-4000-8000-000000000012";
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        if (options.method === "GET") {
          respond(options, settings(settingsVersionId));
          return;
        }
        updateCount += 1;
        if (updateCount === 1) {
          respond(options, preference());
          return;
        }
        settingsVersionId = refreshedVersionId;
        respondError(options, 409, "偏好版本已更新，请重新保存。");
      },
    });

    await page.save();
    const succeeded = new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope);
    expect(succeeded?.state).toBe("succeeded");
    expect(page.data.message).toBe("督学偏好已保存。微信提醒仍按可用单次授权发送。");

    await page.save();
    const terminal = new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope);
    expect(terminal?.state).toBe("terminal");
    expect(terminal?.idempotencyKey).not.toBe(succeeded?.idempotencyKey);
    expect(page.data.message).toBe("偏好版本已更新，请重新保存。");
    expect((page.data.draft as { preferenceVersionId: string }).preferenceVersionId).toBe(
      refreshedVersionId,
    );
    expect(requests.map((request) => request.method)).toEqual(["PUT", "GET", "PUT", "GET"]);
  });

  it("releases busy state when preference update succeeds but settings reload fails", async () => {
    const methods: Array<string | undefined> = [];
    let pendingGet: WechatMiniprogram.RequestOption | undefined;
    const page = await notificationSettingsPage({
      request: (options) => {
        methods.push(options.method);
        if (options.method === "GET") {
          pendingGet = options;
          return;
        }
        respond(options, preference());
      },
    });
    const saving = page.save();
    await vi.waitFor(() => {
      expect(pendingGet).toBeDefined();
    });
    page.data.authorizing = true;
    page.data.disabling = true;
    page.data.syncing = true;
    if (!pendingGet) throw new Error("expected the settings reload request");
    failRequest(pendingGet);

    await saving;

    expect(methods).toEqual(["PUT", "GET"]);
    expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
      "succeeded",
    );
    expect(page.data).toMatchObject({
      status: "error",
      saving: false,
      authorizing: false,
      disabling: false,
      syncing: false,
    });
    expect(page.data.errorMessage).toBe("网络请求失败。");
  });

  it("cancels an unfinished attempt only when a preference control changes the draft", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        failRequest(options);
      },
    });

    await page.save();
    const previous = new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope);
    page.toggleMaster({ detail: { value: false } } as WechatMiniprogram.CustomEvent<{
      value: boolean;
    }>);
    expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
      "cancelled",
    );

    await page.save();
    const replacement = new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope);
    expect(replacement?.idempotencyKey).not.toBe(previous?.idempotencyKey);
    expect(replacement?.state).toBe("unknown");
    expect(headerValue(requests[0], "Idempotency-Key")).not.toBe(
      headerValue(requests[1], "Idempotency-Key"),
    );
  });

  it("ignores an in-flight preference edit and completes the owned attempt after success", async () => {
    let pendingUpdate: WechatMiniprogram.RequestOption | undefined;
    const page = await notificationSettingsPage({
      request: (options) => {
        if (options.method === "GET") {
          respond(options, settings());
          return;
        }
        pendingUpdate = options;
      },
    });

    const saving = page.save();
    await vi.waitFor(() => {
      expect(pendingUpdate).toBeDefined();
    });
    page.toggleMaster({ detail: { value: false } } as WechatMiniprogram.CustomEvent<{
      value: boolean;
    }>);
    expect((page.data.draft as { enabled: boolean }).enabled).toBe(true);
    if (!pendingUpdate) throw new Error("expected an in-flight preference request");
    respond(pendingUpdate, preference());

    await expect(saving).resolves.toBeUndefined();
    expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
      "succeeded",
    );
    expect(page.data.saving).toBe(false);
    expect((page.data.draft as { enabled: boolean }).enabled).toBe(true);
  });

  it("ignores an in-flight preference edit and records an unknown transport result", async () => {
    let pendingUpdate: WechatMiniprogram.RequestOption | undefined;
    const page = await notificationSettingsPage({
      request: (options) => {
        pendingUpdate = options;
      },
    });

    const saving = page.save();
    await vi.waitFor(() => {
      expect(pendingUpdate).toBeDefined();
    });
    page.toggleMaster({ detail: { value: false } } as WechatMiniprogram.CustomEvent<{
      value: boolean;
    }>);
    expect((page.data.draft as { enabled: boolean }).enabled).toBe(true);
    if (!pendingUpdate) throw new Error("expected an in-flight preference request");
    failRequest(pendingUpdate);

    await expect(saving).resolves.toBeUndefined();
    expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
      "unknown",
    );
    expect(page.data.saving).toBe(false);
    expect((page.data.draft as { enabled: boolean }).enabled).toBe(true);
  });

  it("does not open or send disable while a preference save is pending", async () => {
    let pendingSave: WechatMiniprogram.RequestOption | undefined;
    const requests: WechatMiniprogram.RequestOption[] = [];
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        pendingSave = options;
      },
    });

    const saving = page.save();
    await vi.waitFor(() => {
      expect(pendingSave).toBeDefined();
    });
    page.requestDisable();
    await page.disableAll();

    expect(page.showModal).not.toHaveBeenCalled();
    expect(requests).toHaveLength(1);
    if (!pendingSave) throw new Error("expected the pending preference save");
    failRequest(pendingSave);
    await saving;
  });

  it("does not start a preference save while disable is pending", async () => {
    let pendingDisable: WechatMiniprogram.RequestOption | undefined;
    const requests: WechatMiniprogram.RequestOption[] = [];
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        if (options.method === "GET") {
          respond(options, settings());
          return;
        }
        pendingDisable = options;
      },
    });

    const disabling = page.disableAll();
    await vi.waitFor(() => {
      expect(pendingDisable).toBeDefined();
    });
    await page.save();

    expect(requests).toHaveLength(1);
    expect(requests[0]?.url).toContain("/preferences/disable");
    if (!pendingDisable) throw new Error("expected the pending disable request");
    respond(pendingDisable, preference());
    await disabling;
    expect(requests.map((request) => request.method)).toEqual(["POST", "GET"]);
  });

  it("refreshes the latest preference version after a disable conflict", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    const refreshedVersionId = "00000000-0000-4000-8000-000000000099";
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        if (options.method === "POST") {
          respondError(options, 409, "偏好版本已更新，请重新确认关闭。");
          return;
        }
        respond(options, settings(refreshedVersionId));
      },
    });

    await page.disableAll();

    expect(requests.map((request) => request.method)).toEqual(["POST", "GET"]);
    expect((requests[0]?.data as Record<string, unknown>).basedOnPreferenceVersionId).toBe(
      "00000000-0000-4000-8000-000000000010",
    );
    expect((page.data.draft as { preferenceVersionId: string }).preferenceVersionId).toBe(
      refreshedVersionId,
    );
    expect(page.data).toMatchObject({
      status: "ready",
      disabling: false,
      message: "偏好版本已更新，请重新确认关闭。",
    });
  });

  it("holds the global interaction lock while the WeChat authorization prompt is open", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
      },
    });
    page.data.templateConfigured = true;
    page.data.templateId = "template-id-1";

    page.requestWechatAuthorization();
    expect(page.data.authorizing).toBe(true);
    await page.save();
    page.openWechatSettings();
    page.requestDisable();
    await page.disableAll();

    expect(requests).toHaveLength(0);
    expect(page.openSetting).not.toHaveBeenCalled();
    expect(page.showModal).not.toHaveBeenCalled();
    const prompt = page.requestSubscribeMessage.mock.calls[0]?.[0] as
      WechatMiniprogram.RequestSubscribeMessageOption | undefined;
    if (!prompt) throw new Error("expected the WeChat authorization prompt");
    prompt.fail?.({ errMsg: "requestSubscribeMessage:fail cancel", errCode: 20004 });
    expect(page.data.authorizing).toBe(false);
  });

  it("does not submit an old authorization prompt after session replacement", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        respond(options, settings());
      },
    });
    page.data.templateConfigured = true;
    page.data.templateId = "template-id-1";
    page.requestWechatAuthorization();
    const prompt = page.requestSubscribeMessage.mock.calls[0]?.[0] as
      WechatMiniprogram.RequestSubscribeMessageOption | undefined;
    if (!prompt) throw new Error("expected the WeChat authorization prompt");

    page.globalData.accessToken = "token-new-session";
    page.globalData.generation = "generation-new-session";
    page.onShow();
    await vi.waitFor(() => {
      expect(page.data.status).toBe("ready");
    });
    page.data.message = "新会话设置已加载";
    prompt.success?.({
      "template-id-1": "accept",
      errMsg: "requestSubscribeMessage:ok",
    });
    await Promise.resolve();

    expect(requests.map((request) => request.method)).toEqual(["GET"]);
    expect(requests[0]?.header).toMatchObject({ Authorization: "Bearer token-new-session" });
    expect(page.data).toMatchObject({
      authorizing: false,
      message: "新会话设置已加载",
    });
  });

  it("does not submit an old settings snapshot after session replacement", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        respond(options, settings());
      },
    });
    page.data.templateConfigured = true;
    page.data.templateId = "template-id-1";
    page.openWechatSettings();
    const setting = page.openSetting.mock.calls[0]?.[0] as
      WechatMiniprogram.OpenSettingOption | undefined;
    if (!setting) throw new Error("expected the WeChat settings prompt");

    page.globalData.accessToken = "token-new-session";
    page.globalData.generation = "generation-new-session";
    page.onShow();
    await vi.waitFor(() => {
      expect(page.data.status).toBe("ready");
    });
    page.data.message = "新会话设置已加载";
    setting.success?.({
      authSetting: {},
      subscriptionsSetting: {
        mainSwitch: true,
        itemSettings: { "template-id-1": "accept" },
      },
      errMsg: "openSetting:ok",
    });
    await Promise.resolve();

    expect(requests.map((request) => request.method)).toEqual(["GET"]);
    expect(requests[0]?.header).toMatchObject({ Authorization: "Bearer token-new-session" });
    expect(page.data).toMatchObject({
      syncing: false,
      message: "新会话设置已加载",
    });
  });

  it("does not confirm an old disable modal after session replacement", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        respond(options, settings());
      },
    });
    page.requestDisable();
    const modal = page.showModal.mock.calls[0]?.[0] as
      WechatMiniprogram.ShowModalOption | undefined;
    if (!modal) throw new Error("expected the disable confirmation modal");

    page.globalData.accessToken = "token-new-session";
    page.globalData.generation = "generation-new-session";
    page.onShow();
    await vi.waitFor(() => {
      expect(page.data.status).toBe("ready");
    });
    page.data.message = "新会话设置已加载";
    modal.success?.({
      confirm: true,
      cancel: false,
      content: "",
      errMsg: "showModal:ok",
    });
    await Promise.resolve();

    expect(requests.map((request) => request.method)).toEqual(["GET"]);
    expect(requests[0]?.header).toMatchObject({ Authorization: "Bearer token-new-session" });
    expect(page.data).toMatchObject({
      disabling: false,
      message: "新会话设置已加载",
    });
  });

  it("isolates preference attempts by current authenticated user instead of token", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        failRequest(options);
      },
    });

    await page.save();
    page.globalData.accessToken = "token-2";
    page.globalData.userId = "user-b";
    page.globalData.generation = "generation-b";
    await page.save();

    const firstKey = headerValue(requests[0], "Idempotency-Key");
    const secondKey = headerValue(requests[1], "Idempotency-Key");
    expect(firstKey).not.toBe(secondKey);
    expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
      "unknown",
    );
    expect(new OperationAttemptStore("user-b").load(coachPreferenceAttemptScope)?.state).toBe(
      "unknown",
    );
    expect(requests[1]).toBeDefined();
    expect(requests[1]?.header).toMatchObject({ Authorization: "Bearer token-2" });
  });

  it("does not let an old recovery result write UI after same-user session replacement", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    let pendingRecovery: WechatMiniprogram.RequestOption | undefined;
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        if (requests.length === 1) {
          failRequest(options);
          return;
        }
        pendingRecovery = options;
      },
    });
    await page.save();
    const recoveredPage = page.instantiatePage();
    const loading = recoveredPage.loadSettings();
    await vi.waitFor(() => {
      expect(pendingRecovery).toBeDefined();
    });

    recoveredPage.globalData.accessToken = "token-replacement";
    recoveredPage.globalData.generation = "generation-replacement";
    Object.assign(recoveredPage.data, {
      status: "ready",
      saving: true,
      authorizing: true,
      disabling: true,
      syncing: true,
      message: "替换会话页面状态",
      errorMessage: "替换会话错误哨兵",
      requestId: "replacement-request",
    });
    const dataSentinel = JSON.parse(JSON.stringify(recoveredPage.data)) as Record<string, unknown>;
    const setData = vi.spyOn(recoveredPage, "setData");
    if (!pendingRecovery) throw new Error("expected the pending recovery request");
    respond(pendingRecovery, preference());
    await loading;

    expect(setData).not.toHaveBeenCalled();
    expect(recoveredPage.data).toEqual(dataSentinel);
    expect(requests).toHaveLength(2);
    expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
      "succeeded",
    );
  });

  it.each([
    {
      label: "success",
      settle: (request: WechatMiniprogram.RequestOption) => {
        respond(request, preference());
      },
      expectedState: "succeeded",
    },
    {
      label: "transport failure",
      settle: (request: WechatMiniprogram.RequestOption) => {
        failRequest(request);
      },
      expectedState: "unknown",
    },
  ])(
    "does not let an old same-user session $label overwrite any new-session UI state",
    async ({ settle, expectedState }) => {
      let pendingUpdate: WechatMiniprogram.RequestOption | undefined;
      const requests: WechatMiniprogram.RequestOption[] = [];
      const page = await notificationSettingsPage({
        request: (options) => {
          requests.push(options);
          pendingUpdate = options;
        },
      });

      const saving = page.save();
      await vi.waitFor(() => {
        expect(pendingUpdate).toBeDefined();
      });
      page.globalData.accessToken = "token-new-session";
      page.globalData.generation = "generation-new-session";
      Object.assign(page.data, {
        status: "ready",
        saving: true,
        authorizing: true,
        disabling: true,
        syncing: true,
        message: "新会话页面状态",
        errorMessage: "新会话错误哨兵",
        requestId: "new-session-request",
      });
      const dataSentinel = JSON.parse(JSON.stringify(page.data)) as Record<string, unknown>;
      const setData = vi.spyOn(page, "setData");
      if (!pendingUpdate) throw new Error("expected an in-flight preference request");
      settle(pendingUpdate);

      await expect(saving).resolves.toBeUndefined();
      expect(requests).toHaveLength(1);
      expect(setData).not.toHaveBeenCalled();
      expect(page.data).toEqual(dataSentinel);
      expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
        expectedState,
      );
    },
  );

  it("supersedes an in-flight preference save when a new session is shown", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    const updates: WechatMiniprogram.RequestOption[] = [];
    const page = await notificationSettingsPage({
      request: (options) => {
        requests.push(options);
        if (options.method === "GET") {
          respond(options, settings("00000000-0000-4000-8000-000000000077"));
          return;
        }
        updates.push(options);
      },
    });
    const saving = page.save();
    await vi.waitFor(() => {
      expect(updates).toHaveLength(1);
    });

    page.globalData.accessToken = "token-new-session";
    page.globalData.generation = "generation-new-session";
    page.onShow();
    await vi.waitFor(() => {
      expect(updates).toHaveLength(2);
    });
    const recovery = updates[1];
    if (!recovery) throw new Error("expected the replacement-session recovery request");
    respond(recovery, preference());
    await vi.waitFor(() => {
      expect(page.data.status).toBe("ready");
    });
    page.data.message = "新会话设置已加载";
    const original = updates[0];
    if (!original) throw new Error("expected the original preference request");
    respond(original, preference());
    await saving;

    expect(requests.map((request) => request.method)).toEqual(["PUT", "PUT", "GET"]);
    expect(requests[1]?.header).toMatchObject({ Authorization: "Bearer token-new-session" });
    expect(requests[2]?.header).toMatchObject({ Authorization: "Bearer token-new-session" });
    expect(page.data).toMatchObject({
      saving: false,
      message: "新会话设置已加载",
    });
    expect((page.data.draft as { preferenceVersionId: string }).preferenceVersionId).toBe(
      "00000000-0000-4000-8000-000000000077",
    );
    expect(new OperationAttemptStore("user-a").load(coachPreferenceAttemptScope)?.state).toBe(
      "succeeded",
    );
  });
});

interface NotificationSettingsPage extends Record<string, unknown> {
  data: Record<string, unknown>;
  setData(update: Record<string, unknown>): void;
  onShow(): void;
  onUnload(): void;
  loadSettings(): Promise<void>;
  save(): Promise<void>;
  requestWechatAuthorization(): void;
  openWechatSettings(): void;
  requestDisable(): void;
  disableAll(): Promise<void>;
  toggleMaster(event: WechatMiniprogram.CustomEvent<{ value: boolean }>): void;
}

interface NotificationSettingsPageInstance extends NotificationSettingsPage {
  globalData: { accessToken: string; userId: string; generation: string };
  storage: Map<string, unknown>;
  showModal: ReturnType<typeof vi.fn>;
  requestSubscribeMessage: ReturnType<typeof vi.fn>;
  openSetting: ReturnType<typeof vi.fn>;
  instantiatePage(): NotificationSettingsPageInstance;
}

async function notificationSettingsPage(input: {
  request: (options: WechatMiniprogram.RequestOption) => void;
}): Promise<NotificationSettingsPageInstance> {
  const storage = new Map<string, unknown>();
  const showModal = vi.fn();
  const requestSubscribeMessage = vi.fn();
  const openSetting = vi.fn();
  const globalData = { accessToken: "token-1", userId: "user-a", generation: "generation-a" };
  let entropy = 0;
  let captured: unknown;
  vi.stubGlobal("getApp", () => ({ globalData }));
  vi.stubGlobal("Page", (definition: unknown) => {
    captured = definition;
  });
  vi.stubGlobal("wx", {
    request: input.request,
    reLaunch: () => Promise.resolve(),
    showModal,
    canIUse: () => true,
    requestSubscribeMessage,
    openSetting,
    getStorageSync: (key: string) => storage.get(key) ?? "",
    setStorageSync: (key: string, value: unknown) => storage.set(key, value),
    removeStorageSync: (key: string) => storage.delete(key),
    getRandomValues: () => {
      const bytes = new Uint8Array(16);
      entropy += 1;
      bytes[15] = entropy;
      return Promise.resolve({ randomValues: bytes.buffer });
    },
  });
  await import("../miniprogram/pages/notification-settings/index");
  const definition = captured as NotificationSettingsPage;

  function instantiatePage(): NotificationSettingsPageInstance {
    const instance = pageInstance(definition);
    const page: NotificationSettingsPageInstance = {
      ...instance,
      globalData,
      storage,
      showModal,
      requestSubscribeMessage,
      openSetting,
      instantiatePage,
    };
    page.data.draft = preferenceDraft();
    return page;
  }

  return instantiatePage();
}

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

function respondError(
  options: WechatMiniprogram.RequestOption,
  statusCode: number,
  message: string,
): void {
  options.success?.({
    data: { code: "coach_preference_conflict", message, requestId: "coach-request-error" },
    statusCode,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  });
}

function failRequest(options: WechatMiniprogram.RequestOption): void {
  options.fail?.({
    errMsg: "request:fail timeout",
    errno: 0,
    exception: { reasons: [], retryCount: 0 },
    useHttpDNS: false,
  });
}

function preferenceDraft() {
  return {
    preferenceVersionId: "00000000-0000-4000-8000-000000000010",
    enabled: true,
    taskRemindersEnabled: true,
    reflectionRemindersEnabled: true,
    retestRemindersEnabled: true,
    mockRemindersEnabled: true,
    weeklySummaryEnabled: true,
    receiveStart: "07:30",
    receiveEnd: "22:30",
    quietEnabled: false,
    quietStart: "",
    quietEnd: "",
    dailyLimit: "2",
  };
}

function preference(preferenceVersionId = "00000000-0000-4000-8000-000000000010") {
  return {
    preferenceVersionId,
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

function settings(preferenceVersionId = "00000000-0000-4000-8000-000000000010") {
  return {
    preference: preference(preferenceVersionId),
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
