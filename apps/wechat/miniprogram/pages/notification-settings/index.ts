import { CoachNotificationApi } from "../../api/coach-notifications";
import { ApiClient, ApiClientError } from "../../api/client";
import type {
  CoachNotificationHistoryItem,
  CoachNotificationSettings,
  UpdateCoachPreferenceCommand,
} from "../../api/generated/types.gen";
import { currentAuthSession, sameSessionOwner, type ActiveAuthSession } from "../../auth/session";
import { createIdempotencyKey } from "../../platform/command-id";
import {
  OperationAttemptStore,
  type OperationAttemptRecord,
} from "../../platform/operation-attempt-store";

const coachPreferenceAttemptScope = "coach-preference:update@v1";
const coachPreferenceAttemptExpiryMs = 7 * 24 * 60 * 60 * 1_000;
const coachPreferencePendingMessage = "保存结果待确认，请再次保存以确认结果。";
const coachPreferenceRecoveredMessage = "已确认上次保存成功，并恢复最新督学偏好。";

type CoachPreferenceRecovery =
  { kind: "continue"; message?: string } | { kind: "unknown"; message: string; requestId: string };

interface CoachSettingsLoad {
  session: ActiveAuthSession;
  generation: number;
  promise: Promise<void>;
}

type CoachMutationKind = "preference" | "authorization" | "settings" | "disable";

interface CoachMutationLease {
  kind: CoachMutationKind;
  nonce: number;
  session: ActiveAuthSession;
  templateId?: string;
  preferenceVersionId?: string;
}

interface CoachPageRuntime {
  loadGeneration: number;
  mutationNonce: number;
  settingsLoading: boolean;
  settingsLoad?: CoachSettingsLoad;
  mutationLease?: CoachMutationLease;
}

interface CoachBusyPage {
  data: {
    saving: boolean;
    authorizing: boolean;
    disabling: boolean;
    syncing: boolean;
  };
}

const coachPageRuntimes = new WeakMap<object, CoachPageRuntime>();

type ReminderField =
  | "taskRemindersEnabled"
  | "reflectionRemindersEnabled"
  | "retestRemindersEnabled"
  | "mockRemindersEnabled"
  | "weeklySummaryEnabled";

interface HistoryView extends CoachNotificationHistoryItem {
  categoryLabel: string;
  stateLabel: string;
  timeLabel: string;
}

interface SettingsDraft {
  preferenceVersionId: string;
  enabled: boolean;
  taskRemindersEnabled: boolean;
  reflectionRemindersEnabled: boolean;
  retestRemindersEnabled: boolean;
  mockRemindersEnabled: boolean;
  weeklySummaryEnabled: boolean;
  receiveStart: string;
  receiveEnd: string;
  quietEnabled: boolean;
  quietStart: string;
  quietEnd: string;
  dailyLimit: string;
}

const emptyDraft: SettingsDraft = {
  preferenceVersionId: "",
  enabled: false,
  taskRemindersEnabled: true,
  reflectionRemindersEnabled: true,
  retestRemindersEnabled: true,
  mockRemindersEnabled: true,
  weeklySummaryEnabled: true,
  receiveStart: "",
  receiveEnd: "",
  quietEnabled: false,
  quietStart: "",
  quietEnd: "",
  dailyLimit: "1",
};

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    message: "",
    saving: false,
    authorizing: false,
    disabling: false,
    syncing: false,
    draft: { ...emptyDraft },
    templateConfigured: false,
    templateId: "",
    templateReleaseId: "",
    availableGrants: 0,
    lastResultLabel: "尚未向微信申请",
    history: [] as HistoryView[],
    deliveryFactNote: "",
  },

  onShow() {
    const session = currentAuthSession();
    if (!session) {
      void wx.reLaunch({ url: "/pages/login/index" });
      return;
    }
    supersedeCoachMutationForSession(this, session);
    if (coachPageRuntime(this).settingsLoad || !hasCoachMutationBusy(this)) {
      void this.loadSettings();
    }
  },

  onUnload() {
    const runtime = coachPageRuntime(this);
    runtime.loadGeneration += 1;
    runtime.mutationNonce += 1;
    runtime.settingsLoading = false;
    delete runtime.settingsLoad;
    delete runtime.mutationLease;
  },

  loadSettings(): Promise<void> {
    const session = currentAuthSession();
    if (!session) return Promise.resolve();
    const runtime = coachPageRuntime(this);
    const existing = runtime.settingsLoad;
    if (existing && sameSessionOwner(existing.session, session)) {
      return existing.promise;
    }
    const generation = runtime.loadGeneration + 1;
    runtime.loadGeneration = generation;
    runtime.settingsLoading = true;
    const promise = this.performSettingsLoad(session, generation);
    runtime.settingsLoad = { session, generation, promise };
    void promise.then(
      () => {
        finishCoachSettingsLoad(this, generation);
      },
      () => {
        finishCoachSettingsLoad(this, generation);
      },
    );
    return promise;
  },

  async performSettingsLoad(session: ActiveAuthSession, generation: number): Promise<void> {
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    let recovery: CoachPreferenceRecovery;
    try {
      recovery = await recoverCoachPreferenceAttempt(session);
    } catch (error) {
      if (!isCurrentCoachSettingsLoad(this, session, generation)) return;
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        status: "error",
        errorMessage: apiError?.message ?? "督学设置没有加载成功，请重试。",
        requestId: apiError?.requestId ?? "",
        saving: false,
        authorizing: false,
        disabling: false,
        syncing: false,
      });
      return;
    }
    if (!isCurrentCoachSettingsLoad(this, session, generation)) return;
    if (recovery.kind === "unknown") {
      this.setData({
        status: "error",
        errorMessage: recovery.message,
        requestId: recovery.requestId,
        message: recovery.message,
        saving: false,
        authorizing: false,
        disabling: false,
        syncing: false,
      });
      return;
    }
    try {
      const response = await new CoachNotificationApi(
        new ApiClient(undefined, session.accessToken),
      ).settings();
      if (!isCurrentCoachSettingsLoad(this, session, generation)) return;
      this.applySettings(response.data);
      if (recovery.message) this.setData({ message: recovery.message });
    } catch (error) {
      if (!isCurrentCoachSettingsLoad(this, session, generation)) return;
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        status: "error",
        errorMessage: apiError?.message ?? "督学设置没有加载成功，请重试。",
        requestId: apiError?.requestId ?? "",
        saving: false,
        authorizing: false,
        disabling: false,
        syncing: false,
      });
    }
  },

  applySettings(settings: CoachNotificationSettings) {
    const preference = settings.preference;
    this.setData({
      status: "ready",
      draft: {
        preferenceVersionId: preference.preferenceVersionId,
        enabled: preference.enabled,
        taskRemindersEnabled: preference.taskRemindersEnabled,
        reflectionRemindersEnabled: preference.reflectionRemindersEnabled,
        retestRemindersEnabled: preference.retestRemindersEnabled,
        mockRemindersEnabled: preference.mockRemindersEnabled,
        weeklySummaryEnabled: preference.weeklySummaryEnabled,
        receiveStart: clockTime(preference.receiveStart),
        receiveEnd: clockTime(preference.receiveEnd),
        quietEnabled: preference.quietEnabled,
        quietStart: clockTime(preference.quietStart),
        quietEnd: clockTime(preference.quietEnd),
        dailyLimit: String(preference.dailyLimit),
      },
      templateConfigured: settings.template.configured,
      templateId: settings.template.templateId ?? "",
      templateReleaseId: settings.template.releaseId ?? "",
      availableGrants: settings.template.availableGrants,
      lastResultLabel: subscriptionResultLabel(
        settings.template.lastResult,
        settings.template.lastResultSource,
      ),
      history: settings.recentDeliveries.map(historyView),
      deliveryFactNote: settings.deliveryFactNote,
      saving: false,
      authorizing: false,
      disabling: false,
      syncing: false,
    });
  },

  retry() {
    if (isCoachPageBusy(this)) return;
    void this.loadSettings();
  },

  toggleMaster(event: WechatMiniprogram.CustomEvent<{ value: boolean }>) {
    if (isCoachPageBusy(this)) return;
    if (this.data.draft.enabled === event.detail.value) return;
    cancelCoachPreferenceAttempt();
    this.setData({ "draft.enabled": event.detail.value, message: "" });
  },

  toggleQuiet(event: WechatMiniprogram.CustomEvent<{ value: boolean }>) {
    if (isCoachPageBusy(this)) return;
    const enabled = event.detail.value;
    const changed =
      this.data.draft.quietEnabled !== enabled ||
      (!enabled && (this.data.draft.quietStart !== "" || this.data.draft.quietEnd !== ""));
    if (!changed) return;
    cancelCoachPreferenceAttempt();
    this.setData({
      "draft.quietEnabled": enabled,
      ...(enabled ? {} : { "draft.quietStart": "", "draft.quietEnd": "" }),
      message: "",
    });
  },

  toggleCategory(event: WechatMiniprogram.CustomEvent<{ value: boolean }>) {
    if (isCoachPageBusy(this)) return;
    const field = String(event.currentTarget.dataset.field ?? "") as ReminderField;
    if (!isReminderField(field)) return;
    if (this.data.draft[field] === event.detail.value) return;
    cancelCoachPreferenceAttempt();
    this.setData({ [`draft.${field}`]: event.detail.value, message: "" });
  },

  chooseReceiveStart(event: WechatMiniprogram.PickerChange) {
    if (isCoachPageBusy(this)) return;
    const value = String(event.detail.value);
    if (this.data.draft.receiveStart === value) return;
    cancelCoachPreferenceAttempt();
    this.setData({ "draft.receiveStart": value, message: "" });
  },

  chooseReceiveEnd(event: WechatMiniprogram.PickerChange) {
    if (isCoachPageBusy(this)) return;
    const value = String(event.detail.value);
    if (this.data.draft.receiveEnd === value) return;
    cancelCoachPreferenceAttempt();
    this.setData({ "draft.receiveEnd": value, message: "" });
  },

  chooseQuietStart(event: WechatMiniprogram.PickerChange) {
    if (isCoachPageBusy(this)) return;
    const value = String(event.detail.value);
    if (this.data.draft.quietStart === value) return;
    cancelCoachPreferenceAttempt();
    this.setData({ "draft.quietStart": value, message: "" });
  },

  chooseQuietEnd(event: WechatMiniprogram.PickerChange) {
    if (isCoachPageBusy(this)) return;
    const value = String(event.detail.value);
    if (this.data.draft.quietEnd === value) return;
    cancelCoachPreferenceAttempt();
    this.setData({ "draft.quietEnd": value, message: "" });
  },

  updateDailyLimit(event: WechatMiniprogram.Input) {
    if (isCoachPageBusy(this)) return;
    if (this.data.draft.dailyLimit === event.detail.value) return;
    cancelCoachPreferenceAttempt();
    this.setData({ "draft.dailyLimit": event.detail.value, message: "" });
  },

  async save() {
    if (isCoachPageBusy(this)) return;
    const session = currentAuthSession();
    if (!session) {
      void wx.reLaunch({ url: "/pages/login/index" });
      return;
    }
    const command = preferenceCommand(this.data.draft);
    if (typeof command === "string") {
      this.setData({ message: command });
      return;
    }
    const lease = beginCoachMutation(this, { kind: "preference", session });
    this.setData({ saving: true, message: "" });
    const attempts = new OperationAttemptStore(session.userId);
    let attempt: OperationAttemptRecord;
    try {
      attempt = await attempts.begin({
        scope: coachPreferenceAttemptScope,
        canonicalInput: command,
        idempotencyKeyPrefix: "coach-preference",
        expiresAtMs: Date.now() + coachPreferenceAttemptExpiryMs,
      });
    } catch {
      if (!isCurrentCoachMutation(this, lease)) return;
      releaseCoachMutation(this, lease);
      this.setData({
        saving: false,
        message: coachPreferencePendingMessage,
      });
      return;
    }

    let requestFailed = false;
    let requestError: unknown;
    try {
      await new CoachNotificationApi(
        new ApiClient(undefined, session.accessToken),
      ).updatePreference(command, attempt.idempotencyKey);
    } catch (error) {
      requestFailed = true;
      requestError = error;
    }

    if (!requestFailed) {
      transitionCoachPreferenceAttempt(attempts, attempt, "succeeded");
      if (!isCurrentCoachMutation(this, lease)) return;
      releaseCoachMutation(this, lease);
      this.setData({
        saving: false,
        message: "督学偏好已保存。微信提醒仍按可用单次授权发送。",
      });
      await this.loadSettings();
      return;
    }

    if (isTerminalCoachPreferenceError(requestError)) {
      transitionCoachPreferenceAttempt(attempts, attempt, "terminal");
    } else {
      transitionCoachPreferenceAttempt(attempts, attempt, "unknown");
    }
    if (!isCurrentCoachMutation(this, lease)) return;
    releaseCoachMutation(this, lease);
    if (isCoachPreferenceConflict(requestError)) {
      const conflictMessage = requestError.message;
      await this.loadSettings();
      if (sameSessionOwner(currentAuthSession(), session) && this.data.status === "ready") {
        this.setData({ message: conflictMessage });
      }
      return;
    }
    this.setData({
      saving: false,
      message: isTerminalCoachPreferenceError(requestError)
        ? requestError.message
        : coachPreferencePendingMessage,
    });
  },

  requestWechatAuthorization() {
    if (isCoachPageBusy(this) || !this.data.templateConfigured || !this.data.templateId) return;
    if (!wx.canIUse("requestSubscribeMessage")) {
      this.setData({ message: "当前微信版本不支持订阅消息，请升级微信后再试。" });
      return;
    }
    const session = currentAuthSession();
    if (!session) return;
    const lease = beginCoachMutation(this, {
      kind: "authorization",
      session,
      templateId: this.data.templateId,
    });
    this.setData({ authorizing: true, message: "" });
    wx.requestSubscribeMessage({
      tmplIds: [lease.templateId ?? ""],
      success: (result) => {
        if (!isCurrentCoachMutation(this, lease)) {
          cancelCoachMutationContinuation(this, lease, "authorizing");
          return;
        }
        const value = (result as unknown as Record<string, unknown>)[lease.templateId ?? ""];
        if (!isSubscriptionResult(value)) {
          releaseCoachMutation(this, lease);
          this.setData({ authorizing: false, message: "微信没有返回可识别的订阅结果。" });
          return;
        }
        void this.persistSubscription("request_prompt", value, true, lease);
      },
      fail: () => {
        if (!isCurrentCoachMutation(this, lease)) {
          cancelCoachMutationContinuation(this, lease, "authorizing");
          return;
        }
        releaseCoachMutation(this, lease);
        this.setData({ authorizing: false, message: "没有完成微信订阅授权，请稍后再试。" });
      },
    });
  },

  openWechatSettings() {
    if (isCoachPageBusy(this) || !this.data.templateConfigured) return;
    const session = currentAuthSession();
    if (!session) return;
    const lease = beginCoachMutation(this, {
      kind: "settings",
      session,
      templateId: this.data.templateId,
    });
    this.setData({ syncing: true, message: "" });
    wx.openSetting({
      withSubscriptions: true,
      success: (result) => {
        if (!isCurrentCoachMutation(this, lease)) {
          cancelCoachMutationContinuation(this, lease, "syncing");
          return;
        }
        const subscriptions = recordValue(result, "subscriptionsSetting");
        const itemSettings = recordValue(subscriptions, "itemSettings");
        const value = recordValue(itemSettings, lease.templateId ?? "");
        if (!isSubscriptionResult(value) || value === "filter") {
          releaseCoachMutation(this, lease);
          this.setData({
            syncing: false,
            message: "微信设置页没有保存这条模板的固定选择。",
          });
          return;
        }
        void this.persistSubscription("settings_snapshot", value, true, lease);
      },
      fail: () => {
        if (!isCurrentCoachMutation(this, lease)) {
          cancelCoachMutationContinuation(this, lease, "syncing");
          return;
        }
        releaseCoachMutation(this, lease);
        this.setData({ syncing: false, message: "微信订阅设置没有打开。" });
      },
    });
  },

  async persistSubscription(
    source: "request_prompt" | "settings_snapshot",
    result: "accept" | "reject" | "ban" | "filter",
    continuation = false,
    existingLease?: CoachMutationLease,
  ) {
    const activeFlag: CoachBusyFlag = source === "request_prompt" ? "authorizing" : "syncing";
    let lease = existingLease;
    if (continuation) {
      if (
        !lease ||
        !this.data[activeFlag] ||
        isCoachPageBusy(this, activeFlag) ||
        !isCurrentCoachMutation(this, lease)
      ) {
        if (lease) cancelCoachMutationContinuation(this, lease, activeFlag);
        return;
      }
    } else {
      if (isCoachPageBusy(this)) return;
      const session = currentAuthSession();
      if (!session) return;
      lease = beginCoachMutation(this, {
        kind: source === "request_prompt" ? "authorization" : "settings",
        session,
        templateId: this.data.templateId,
      });
      this.setData(
        activeFlag === "authorizing"
          ? { authorizing: true, message: "" }
          : { syncing: true, message: "" },
      );
    }
    try {
      const idempotencyKey = await createIdempotencyKey(
        `coach-subscription-${source.replace("_", "-")}`,
      );
      if (!isCurrentCoachMutation(this, lease)) {
        cancelCoachMutationContinuation(this, lease, activeFlag);
        return;
      }
      const response = await new CoachNotificationApi(
        new ApiClient(undefined, lease.session.accessToken),
      ).recordWechatSubscription(
        {
          source,
          templateCode: "coach_reminder",
          templateId: lease.templateId ?? "",
          result,
        },
        idempotencyKey,
      );
      if (!isCurrentCoachMutation(this, lease)) {
        cancelCoachMutationContinuation(this, lease, activeFlag);
        return;
      }
      releaseCoachMutation(this, lease);
      this.setData({
        authorizing: false,
        syncing: false,
        message:
          result === "accept" && source === "request_prompt"
            ? `已增加 1 次微信提醒授权，当前可发送 ${String(response.data.availableGrants)} 次。`
            : subscriptionResultLabel(result, source),
      });
      await this.loadSettings();
    } catch (error) {
      if (!isCurrentCoachMutation(this, lease)) {
        cancelCoachMutationContinuation(this, lease, activeFlag);
        return;
      }
      releaseCoachMutation(this, lease);
      this.setData({
        authorizing: false,
        syncing: false,
        message: error instanceof ApiClientError ? error.message : "订阅结果同步失败。",
      });
    }
  },

  requestDisable() {
    if (isCoachPageBusy(this)) return;
    const session = currentAuthSession();
    if (!session) return;
    const lease = beginCoachMutation(this, {
      kind: "disable",
      session,
      preferenceVersionId: this.data.draft.preferenceVersionId,
    });
    this.setData({ disabling: true, message: "" });
    wx.showModal({
      title: "关闭全部 AI 督学提醒？",
      content: "未发送的消息会取消，剩余单次授权会作废；小程序内今日任务不受影响。",
      confirmText: "关闭提醒",
      confirmColor: "#C83F56",
      success: (result) => {
        if (!isCurrentCoachMutation(this, lease)) {
          cancelCoachMutationContinuation(this, lease, "disabling");
          return;
        }
        if (result.confirm) {
          void this.disableAll(true, lease);
          return;
        }
        releaseCoachMutation(this, lease);
        this.setData({ disabling: false });
      },
      fail: () => {
        if (!isCurrentCoachMutation(this, lease)) {
          cancelCoachMutationContinuation(this, lease, "disabling");
          return;
        }
        releaseCoachMutation(this, lease);
        this.setData({ disabling: false, message: "关闭提醒确认框没有打开。" });
      },
    });
  },

  async disableAll(continuation = false, existingLease?: CoachMutationLease) {
    let lease = existingLease;
    if (continuation) {
      if (
        !lease ||
        !this.data.disabling ||
        isCoachPageBusy(this, "disabling") ||
        !isCurrentCoachMutation(this, lease)
      ) {
        if (lease) cancelCoachMutationContinuation(this, lease, "disabling");
        return;
      }
    } else {
      if (isCoachPageBusy(this)) return;
      const session = currentAuthSession();
      if (!session) return;
      lease = beginCoachMutation(this, {
        kind: "disable",
        session,
        preferenceVersionId: this.data.draft.preferenceVersionId,
      });
      this.setData({ disabling: true, message: "" });
    }
    try {
      const idempotencyKey = await createIdempotencyKey("coach-disable");
      if (!isCurrentCoachMutation(this, lease)) {
        cancelCoachMutationContinuation(this, lease, "disabling");
        return;
      }
      await new CoachNotificationApi(new ApiClient(undefined, lease.session.accessToken)).disable(
        { basedOnPreferenceVersionId: lease.preferenceVersionId ?? "" },
        idempotencyKey,
      );
      if (!isCurrentCoachMutation(this, lease)) {
        cancelCoachMutationContinuation(this, lease, "disabling");
        return;
      }
      releaseCoachMutation(this, lease);
      this.setData({ disabling: false, message: "全部 AI 督学提醒已关闭。" });
      await this.loadSettings();
    } catch (error) {
      if (!isCurrentCoachMutation(this, lease)) {
        cancelCoachMutationContinuation(this, lease, "disabling");
        return;
      }
      releaseCoachMutation(this, lease);
      const conflict = error instanceof ApiClientError && error.statusCode === 409;
      const message = error instanceof ApiClientError ? error.message : "关闭提醒失败。";
      this.setData({ disabling: false, message });
      if (conflict) {
        await this.loadSettings();
        if (sameSessionOwner(currentAuthSession(), lease.session) && this.data.status === "ready") {
          this.setData({ message });
        }
        return;
      }
    }
  },
});

function coachPageRuntime(page: object): CoachPageRuntime {
  const existing = coachPageRuntimes.get(page);
  if (existing) return existing;
  const created: CoachPageRuntime = {
    loadGeneration: 0,
    mutationNonce: 0,
    settingsLoading: false,
  };
  coachPageRuntimes.set(page, created);
  return created;
}

type CoachBusyFlag = "saving" | "authorizing" | "disabling" | "syncing";

function beginCoachMutation(
  page: object,
  input: Omit<CoachMutationLease, "nonce">,
): CoachMutationLease {
  const runtime = coachPageRuntime(page);
  runtime.mutationNonce += 1;
  const lease: CoachMutationLease = {
    ...input,
    nonce: runtime.mutationNonce,
  };
  runtime.mutationLease = lease;
  return lease;
}

function isCurrentCoachMutation(page: object, lease: CoachMutationLease): boolean {
  const current = coachPageRuntime(page).mutationLease;
  return (
    current?.nonce === lease.nonce &&
    current.kind === lease.kind &&
    sameSessionOwner(current.session, lease.session) &&
    sameSessionOwner(currentAuthSession(), lease.session)
  );
}

function releaseCoachMutation(page: object, lease: CoachMutationLease): boolean {
  const runtime = coachPageRuntime(page);
  if (runtime.mutationLease?.nonce !== lease.nonce) return false;
  runtime.mutationNonce += 1;
  delete runtime.mutationLease;
  return true;
}

function cancelCoachMutationContinuation(
  page: CoachBusyPage & { setData(update: Record<string, unknown>): void },
  lease: CoachMutationLease,
  activeFlag: CoachBusyFlag,
): void {
  if (!sameSessionOwner(currentAuthSession(), lease.session)) return;
  if (!releaseCoachMutation(page, lease)) return;
  page.setData({ [activeFlag]: false });
}

function supersedeCoachMutationForSession(
  page: CoachBusyPage & { setData(update: Record<string, unknown>): void },
  session: ActiveAuthSession,
): void {
  const runtime = coachPageRuntime(page);
  const lease = runtime.mutationLease;
  if (!lease || sameSessionOwner(lease.session, session)) return;
  runtime.mutationNonce += 1;
  delete runtime.mutationLease;
  page.setData({
    saving: false,
    authorizing: false,
    disabling: false,
    syncing: false,
  });
}

function isCoachPageBusy(page: CoachBusyPage & object, allowedFlag?: CoachBusyFlag): boolean {
  if (coachPageRuntime(page).settingsLoading) return true;
  return hasCoachMutationBusy(page, allowedFlag);
}

function hasCoachMutationBusy(page: CoachBusyPage, allowedFlag?: CoachBusyFlag): boolean {
  const flags: CoachBusyFlag[] = ["saving", "authorizing", "disabling", "syncing"];
  return flags.some((flag) => flag !== allowedFlag && page.data[flag]);
}

function finishCoachSettingsLoad(page: object, generation: number): void {
  const runtime = coachPageRuntime(page);
  if (runtime.settingsLoad?.generation !== generation) return;
  runtime.settingsLoading = false;
  delete runtime.settingsLoad;
}

function isCurrentCoachSettingsLoad(
  page: object,
  session: ActiveAuthSession,
  generation: number,
): boolean {
  const runtime = coachPageRuntime(page);
  return (
    runtime.settingsLoad?.generation === generation &&
    sameSessionOwner(runtime.settingsLoad.session, session) &&
    sameSessionOwner(currentAuthSession(), session)
  );
}

async function recoverCoachPreferenceAttempt(
  session: ActiveAuthSession,
): Promise<CoachPreferenceRecovery> {
  const attempts = new OperationAttemptStore(session.userId);
  const attempt = attempts.load(coachPreferenceAttemptScope);
  if (!attempt || (attempt.state !== "pending" && attempt.state !== "unknown")) {
    return { kind: "continue" };
  }
  if (!isUpdateCoachPreferenceCommand(attempt.canonicalInput)) {
    transitionCoachPreferenceAttempt(attempts, attempt, "terminal");
    return {
      kind: "continue",
      message: "旧恢复记录无法校验，已停止重试。",
    };
  }

  let requestFailed = false;
  let requestError: unknown;
  try {
    await new CoachNotificationApi(new ApiClient(undefined, session.accessToken)).updatePreference(
      attempt.canonicalInput,
      attempt.idempotencyKey,
    );
  } catch (error) {
    requestFailed = true;
    requestError = error;
  }

  if (!requestFailed) {
    transitionCoachPreferenceAttempt(attempts, attempt, "succeeded");
    const state = currentCoachPreferenceAttemptState(attempts, attempt);
    return {
      kind: "continue",
      ...(state === "succeeded" ? { message: coachPreferenceRecoveredMessage } : {}),
    };
  }
  if (isTerminalCoachPreferenceError(requestError)) {
    transitionCoachPreferenceAttempt(attempts, attempt, "terminal");
    if (currentCoachPreferenceAttemptState(attempts, attempt) === "succeeded") {
      return { kind: "continue", message: coachPreferenceRecoveredMessage };
    }
    return {
      kind: "continue",
      message: requestError.message,
    };
  }
  transitionCoachPreferenceAttempt(attempts, attempt, "unknown");
  const state = currentCoachPreferenceAttemptState(attempts, attempt);
  if (state === "succeeded") {
    return { kind: "continue", message: coachPreferenceRecoveredMessage };
  }
  if (state === "terminal" || state === "cancelled") {
    return { kind: "continue" };
  }
  return {
    kind: "unknown",
    message: coachPreferencePendingMessage,
    requestId: requestError instanceof ApiClientError ? requestError.requestId : "",
  };
}

function currentCoachPreferenceAttemptState(
  attempts: OperationAttemptStore,
  attempt: OperationAttemptRecord,
): OperationAttemptRecord["state"] | undefined {
  const current = attempts.load(coachPreferenceAttemptScope);
  return current?.idempotencyKey === attempt.idempotencyKey ? current.state : undefined;
}

function isTerminalCoachPreferenceError(error: unknown): error is ApiClientError {
  return (
    error instanceof ApiClientError &&
    error.classification === "http_response" &&
    error.statusCode >= 400 &&
    error.statusCode < 500 &&
    error.statusCode !== 408
  );
}

function isCoachPreferenceConflict(error: unknown): error is ApiClientError {
  return isTerminalCoachPreferenceError(error) && error.statusCode === 409;
}

function isUpdateCoachPreferenceCommand(value: unknown): value is UpdateCoachPreferenceCommand {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const command = value as Record<string, unknown>;
  const requiredKeys = [
    "basedOnPreferenceVersionId",
    "dailyLimit",
    "enabled",
    "mockRemindersEnabled",
    "quietEnabled",
    "quietEnd",
    "quietStart",
    "receiveEnd",
    "receiveStart",
    "reflectionRemindersEnabled",
    "retestRemindersEnabled",
    "taskRemindersEnabled",
    "weeklySummaryEnabled",
  ];
  const allowedKeys = new Set([...requiredKeys, "timezone"]);
  if (
    requiredKeys.some((key) => !Object.prototype.hasOwnProperty.call(command, key)) ||
    Object.keys(command).some((key) => !allowedKeys.has(key))
  ) {
    return false;
  }
  const hasValidFieldTypes =
    typeof command.basedOnPreferenceVersionId === "string" &&
    typeof command.dailyLimit === "number" &&
    Number.isInteger(command.dailyLimit) &&
    command.dailyLimit >= 1 &&
    command.dailyLimit <= 24 &&
    typeof command.enabled === "boolean" &&
    typeof command.mockRemindersEnabled === "boolean" &&
    typeof command.quietEnabled === "boolean" &&
    isNullableClockTime(command.quietEnd) &&
    isNullableClockTime(command.quietStart) &&
    isNullableClockTime(command.receiveEnd) &&
    isNullableClockTime(command.receiveStart) &&
    typeof command.reflectionRemindersEnabled === "boolean" &&
    typeof command.retestRemindersEnabled === "boolean" &&
    typeof command.taskRemindersEnabled === "boolean" &&
    typeof command.weeklySummaryEnabled === "boolean" &&
    (command.timezone === undefined || command.timezone === "Asia/Shanghai");
  if (!hasValidFieldTypes) return false;

  const candidate = command as unknown as UpdateCoachPreferenceCommand;
  const categoryEnabled =
    candidate.taskRemindersEnabled ||
    candidate.reflectionRemindersEnabled ||
    candidate.retestRemindersEnabled ||
    candidate.mockRemindersEnabled ||
    candidate.weeklySummaryEnabled;
  return (
    isUuid(candidate.basedOnPreferenceVersionId) &&
    (!candidate.enabled ||
      (categoryEnabled &&
        candidate.receiveStart !== null &&
        candidate.receiveEnd !== null &&
        candidate.receiveStart !== candidate.receiveEnd)) &&
    (candidate.quietEnabled
      ? candidate.quietStart !== null &&
        candidate.quietEnd !== null &&
        candidate.quietStart !== candidate.quietEnd
      : candidate.quietStart === null && candidate.quietEnd === null)
  );
}

function isNullableClockTime(value: unknown): value is string | null {
  return (
    value === null ||
    (typeof value === "string" && /^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/u.test(value))
  );
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu.test(value);
}

function transitionCoachPreferenceAttempt(
  attempts: OperationAttemptStore,
  attempt: OperationAttemptRecord,
  outcome: "succeeded" | "terminal" | "unknown",
): void {
  const current = attempts.load(coachPreferenceAttemptScope);
  if (
    !current ||
    current.idempotencyKey !== attempt.idempotencyKey ||
    (current.state !== "pending" && current.state !== "unknown")
  ) {
    return;
  }
  if (outcome === "succeeded") {
    attempts.markSucceeded(coachPreferenceAttemptScope, attempt.idempotencyKey);
    return;
  }
  if (outcome === "terminal") {
    attempts.markTerminal(coachPreferenceAttemptScope, attempt.idempotencyKey);
    return;
  }
  attempts.markTransportUnknown(coachPreferenceAttemptScope, attempt.idempotencyKey);
}

function cancelCoachPreferenceAttempt(): void {
  const session = currentAuthSession();
  if (!session) return;
  const attempts = new OperationAttemptStore(session.userId);
  const attempt = attempts.load(coachPreferenceAttemptScope);
  if (attempt?.state === "pending" || attempt?.state === "unknown") {
    attempts.cancel(coachPreferenceAttemptScope, attempt.idempotencyKey);
  }
}

function clockTime(value: string | null): string {
  return value ? value.slice(0, 5) : "";
}

function preferenceCommand(draft: SettingsDraft): UpdateCoachPreferenceCommand | string {
  const dailyLimit = Number(draft.dailyLimit);
  if (!Number.isInteger(dailyLimit) || dailyLimit < 1 || dailyLimit > 24) {
    return "每日频率上限请输入 1–24 的整数。";
  }
  if (draft.enabled && (!draft.receiveStart || !draft.receiveEnd)) {
    return "开启督学前，请先设置完整接收时段。";
  }
  if (draft.enabled && draft.receiveStart === draft.receiveEnd) {
    return "接收开始和结束时间不能相同。";
  }
  const categories = [
    draft.taskRemindersEnabled,
    draft.reflectionRemindersEnabled,
    draft.retestRemindersEnabled,
    draft.mockRemindersEnabled,
    draft.weeklySummaryEnabled,
  ];
  if (draft.enabled && !categories.some(Boolean)) return "请至少保留一类督学提醒。";
  if (draft.quietEnabled && (!draft.quietStart || !draft.quietEnd)) {
    return "开启免打扰前，请先设置完整时间。";
  }
  if (draft.quietEnabled && draft.quietStart === draft.quietEnd) {
    return "免打扰开始和结束时间不能相同。";
  }
  return {
    basedOnPreferenceVersionId: draft.preferenceVersionId,
    enabled: draft.enabled,
    taskRemindersEnabled: draft.taskRemindersEnabled,
    reflectionRemindersEnabled: draft.reflectionRemindersEnabled,
    retestRemindersEnabled: draft.retestRemindersEnabled,
    mockRemindersEnabled: draft.mockRemindersEnabled,
    weeklySummaryEnabled: draft.weeklySummaryEnabled,
    receiveStart: draft.receiveStart || null,
    receiveEnd: draft.receiveEnd || null,
    quietEnabled: draft.quietEnabled,
    quietStart: draft.quietEnabled ? draft.quietStart || null : null,
    quietEnd: draft.quietEnabled ? draft.quietEnd || null : null,
    dailyLimit,
    timezone: "Asia/Shanghai",
  };
}

function isReminderField(value: string): value is ReminderField {
  return [
    "taskRemindersEnabled",
    "reflectionRemindersEnabled",
    "retestRemindersEnabled",
    "mockRemindersEnabled",
    "weeklySummaryEnabled",
  ].includes(value);
}

function isSubscriptionResult(value: unknown): value is "accept" | "reject" | "ban" | "filter" {
  return value === "accept" || value === "reject" || value === "ban" || value === "filter";
}

function recordValue(value: unknown, key: string): unknown {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  return (value as Record<string, unknown>)[key];
}

function subscriptionResultLabel(
  result: "accept" | "reject" | "ban" | "filter" | null,
  source: "request_prompt" | "settings_snapshot" | null,
): string {
  if (result === null) return "尚未向微信申请";
  if (result === "accept") {
    return source === "request_prompt" ? "最近一次已授权" : "微信侧保持允许";
  }
  if (result === "reject") return "微信侧已拒绝";
  if (result === "ban") return "该模板被微信限制";
  return "模板被微信过滤";
}

function historyView(item: CoachNotificationHistoryItem): HistoryView {
  const stateLabel = {
    queued: "等待发送",
    sending: "正在请求微信",
    platform_accepted: "微信已受理",
    platform_rejected: "微信未受理",
    failed: "发送失败",
    outcome_unknown: "发送结果待核对",
    canceled: "已取消",
  }[item.state];
  const categoryLabel = {
    task: "今日任务",
    reflection: "复盘",
    retest: "复测",
    mock: "模考",
    weekly_summary: "周总结",
  }[item.category];
  return {
    ...item,
    categoryLabel,
    stateLabel: item.openedAt ? `${stateLabel} · 已打开` : stateLabel,
    timeLabel: dateTimeLabel(item.createdAt),
  };
}

function dateTimeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${month}.${day} ${hour}:${minute}`;
}
