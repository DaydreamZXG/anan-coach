import { CoachNotificationApi } from "../../api/coach-notifications";
import { ApiClient, ApiClientError } from "../../api/client";
import type {
  CoachNotificationHistoryItem,
  CoachNotificationSettings,
  UpdateCoachPreferenceCommand,
} from "../../api/generated/types.gen";
import { currentAccessToken } from "../../auth/session";
import { createIdempotencyKey } from "../../platform/command-id";

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
    if (!currentAccessToken()) {
      void wx.reLaunch({ url: "/pages/login/index" });
      return;
    }
    if (this.data.status !== "ready" || !this.data.saving) void this.loadSettings();
  },

  async loadSettings() {
    const token = currentAccessToken();
    if (!token) return;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const response = await new CoachNotificationApi(new ApiClient(undefined, token)).settings();
      this.applySettings(response.data);
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        status: "error",
        errorMessage: apiError?.message ?? "督学设置没有加载成功，请重试。",
        requestId: apiError?.requestId ?? "",
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
    void this.loadSettings();
  },

  toggleMaster(event: WechatMiniprogram.CustomEvent<{ value: boolean }>) {
    this.setData({ "draft.enabled": event.detail.value, message: "" });
  },

  toggleQuiet(event: WechatMiniprogram.CustomEvent<{ value: boolean }>) {
    const enabled = event.detail.value;
    this.setData({
      "draft.quietEnabled": enabled,
      ...(enabled ? {} : { "draft.quietStart": "", "draft.quietEnd": "" }),
      message: "",
    });
  },

  toggleCategory(event: WechatMiniprogram.CustomEvent<{ value: boolean }>) {
    const field = String(event.currentTarget.dataset.field ?? "") as ReminderField;
    if (!isReminderField(field)) return;
    this.setData({ [`draft.${field}`]: event.detail.value, message: "" });
  },

  chooseReceiveStart(event: WechatMiniprogram.PickerChange) {
    this.setData({ "draft.receiveStart": String(event.detail.value), message: "" });
  },

  chooseReceiveEnd(event: WechatMiniprogram.PickerChange) {
    this.setData({ "draft.receiveEnd": String(event.detail.value), message: "" });
  },

  chooseQuietStart(event: WechatMiniprogram.PickerChange) {
    this.setData({ "draft.quietStart": String(event.detail.value), message: "" });
  },

  chooseQuietEnd(event: WechatMiniprogram.PickerChange) {
    this.setData({ "draft.quietEnd": String(event.detail.value), message: "" });
  },

  updateDailyLimit(event: WechatMiniprogram.Input) {
    this.setData({ "draft.dailyLimit": event.detail.value, message: "" });
  },

  async save() {
    if (this.data.saving) return;
    const token = currentAccessToken();
    if (!token) return;
    const command = preferenceCommand(this.data.draft);
    if (typeof command === "string") {
      this.setData({ message: command });
      return;
    }
    this.setData({ saving: true, message: "" });
    try {
      await new CoachNotificationApi(new ApiClient(undefined, token)).updatePreference(
        command,
        await createIdempotencyKey("coach-preference"),
      );
      this.setData({ message: "督学偏好已保存。微信提醒仍按可用单次授权发送。" });
      await this.loadSettings();
    } catch (error) {
      this.setData({
        saving: false,
        message: error instanceof ApiClientError ? error.message : "督学偏好保存失败。",
      });
    }
  },

  requestWechatAuthorization() {
    if (this.data.authorizing || !this.data.templateConfigured || !this.data.templateId) return;
    if (!wx.canIUse("requestSubscribeMessage")) {
      this.setData({ message: "当前微信版本不支持订阅消息，请升级微信后再试。" });
      return;
    }
    this.setData({ authorizing: true, message: "" });
    wx.requestSubscribeMessage({
      tmplIds: [this.data.templateId],
      success: (result) => {
        const value = (result as unknown as Record<string, unknown>)[this.data.templateId];
        if (!isSubscriptionResult(value)) {
          this.setData({ authorizing: false, message: "微信没有返回可识别的订阅结果。" });
          return;
        }
        void this.persistSubscription("request_prompt", value);
      },
      fail: () => {
        this.setData({ authorizing: false, message: "没有完成微信订阅授权，请稍后再试。" });
      },
    });
  },

  openWechatSettings() {
    if (this.data.syncing || !this.data.templateConfigured) return;
    this.setData({ syncing: true, message: "" });
    wx.openSetting({
      withSubscriptions: true,
      success: (result) => {
        const subscriptions = recordValue(result, "subscriptionsSetting");
        const itemSettings = recordValue(subscriptions, "itemSettings");
        const value = recordValue(itemSettings, this.data.templateId);
        if (!isSubscriptionResult(value) || value === "filter") {
          this.setData({
            syncing: false,
            message: "微信设置页没有保存这条模板的固定选择。",
          });
          return;
        }
        void this.persistSubscription("settings_snapshot", value);
      },
      fail: () => {
        this.setData({ syncing: false, message: "微信订阅设置没有打开。" });
      },
    });
  },

  async persistSubscription(
    source: "request_prompt" | "settings_snapshot",
    result: "accept" | "reject" | "ban" | "filter",
  ) {
    const token = currentAccessToken();
    if (!token) return;
    try {
      const response = await new CoachNotificationApi(
        new ApiClient(undefined, token),
      ).recordWechatSubscription(
        {
          source,
          templateCode: "coach_reminder",
          templateId: this.data.templateId,
          result,
        },
        await createIdempotencyKey(`coach-subscription-${source.replace("_", "-")}`),
      );
      this.setData({
        message:
          result === "accept" && source === "request_prompt"
            ? `已增加 1 次微信提醒授权，当前可发送 ${String(response.data.availableGrants)} 次。`
            : subscriptionResultLabel(result, source),
      });
      await this.loadSettings();
    } catch (error) {
      this.setData({
        authorizing: false,
        syncing: false,
        message: error instanceof ApiClientError ? error.message : "订阅结果同步失败。",
      });
    }
  },

  requestDisable() {
    if (this.data.disabling) return;
    wx.showModal({
      title: "关闭全部 AI 督学提醒？",
      content: "未发送的消息会取消，剩余单次授权会作废；小程序内今日任务不受影响。",
      confirmText: "关闭提醒",
      confirmColor: "#C83F56",
      success: (result) => {
        if (result.confirm) void this.disableAll();
      },
    });
  },

  async disableAll() {
    const token = currentAccessToken();
    if (!token) return;
    this.setData({ disabling: true, message: "" });
    try {
      await new CoachNotificationApi(new ApiClient(undefined, token)).disable(
        { basedOnPreferenceVersionId: this.data.draft.preferenceVersionId },
        await createIdempotencyKey("coach-disable"),
      );
      this.setData({ message: "全部 AI 督学提醒已关闭。" });
      await this.loadSettings();
    } catch (error) {
      this.setData({
        disabling: false,
        message: error instanceof ApiClientError ? error.message : "关闭提醒失败。",
      });
    }
  },
});

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
