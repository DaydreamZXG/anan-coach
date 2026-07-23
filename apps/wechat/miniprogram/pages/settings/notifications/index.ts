import { authedV2, idemKey } from "../../../api/endpoints";
import type {
  CoachNotificationSettings,
  CoachPreferenceView,
  UpdateCoachPreferenceCommand,
} from "../../../api/generated/types.gen";

/** 开关项对应的 CoachPreferenceView 布尔字段 */
type ToggleKey =
  | "enabled"
  | "taskRemindersEnabled"
  | "reflectionRemindersEnabled"
  | "retestRemindersEnabled"
  | "mockRemindersEnabled"
  | "weeklySummaryEnabled"
  | "quietEnabled";

interface ToggleItem {
  key: ToggleKey;
  name: string;
  desc: string;
  checked: boolean;
  saving: boolean;
}

const TOGGLES: Array<Omit<ToggleItem, "checked" | "saving">> = [
  { key: "enabled", name: "教练消息总开关", desc: "关闭后岸岸不再主动给你发任何教练消息" },
  { key: "taskRemindersEnabled", name: "任务提醒", desc: "今天的任务快截止时提醒你" },
  { key: "reflectionRemindersEnabled", name: "复盘提醒", desc: "有题目待复盘时提醒你" },
  { key: "retestRemindersEnabled", name: "重测提醒", desc: "到验证点时提醒你重测确认掌握" },
  { key: "mockRemindersEnabled", name: "模考提醒", desc: "模考日前提醒你留出整卷时间" },
  { key: "weeklySummaryEnabled", name: "每周总结", desc: "每周推送一次学习进展总结" },
  { key: "quietEnabled", name: "夜间免打扰", desc: "免打扰时段内不推送任何消息" },
];

Page({
  data: {
    statusBarHeight: 20,
    viewState: "loading" as "loading" | "error" | "ready",
    items: [] as ToggleItem[],
  },

  /** 当前通知偏好（写接口需要全量字段与 preferenceVersionId） */
  preference: null as CoachPreferenceView | null,

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    void this.load();
  },

  onBack() {
    wx.navigateBack({
      fail: () => {
        wx.switchTab({ url: "/pages/profile/index" });
      },
    });
  },

  onRetry() {
    void this.load();
  },

  async load() {
    this.setData({ viewState: "loading" });
    try {
      const settings = await authedV2<CoachNotificationSettings>({
        method: "GET",
        path: "/api/v2/coach-notifications/settings",
      });
      this.preference = settings.preference;
      const items = TOGGLES.map((t) => ({
        ...t,
        checked: settings.preference[t.key],
        saving: false,
      }));
      this.setData({ viewState: "ready", items });
    } catch {
      this.setData({ viewState: "error" });
    }
  },

  patchItem(key: ToggleKey, patch: Partial<Pick<ToggleItem, "checked" | "saving">>) {
    const items = this.data.items.map((it) => (it.key === key ? { ...it, ...patch } : it));
    this.setData({ items });
  },

  /** 以当前偏好为基础，改动一个开关字段，构造全量写请求体 */
  buildBody(key: ToggleKey, value: boolean): UpdateCoachPreferenceCommand {
    const pref = this.preference as CoachPreferenceView;
    return {
      basedOnPreferenceVersionId: pref.preferenceVersionId,
      dailyLimit: pref.dailyLimit,
      enabled: key === "enabled" ? value : pref.enabled,
      mockRemindersEnabled: key === "mockRemindersEnabled" ? value : pref.mockRemindersEnabled,
      quietEnabled: key === "quietEnabled" ? value : pref.quietEnabled,
      quietEnd: pref.quietEnd,
      quietStart: pref.quietStart,
      receiveEnd: pref.receiveEnd,
      receiveStart: pref.receiveStart,
      reflectionRemindersEnabled:
        key === "reflectionRemindersEnabled" ? value : pref.reflectionRemindersEnabled,
      retestRemindersEnabled:
        key === "retestRemindersEnabled" ? value : pref.retestRemindersEnabled,
      taskRemindersEnabled: key === "taskRemindersEnabled" ? value : pref.taskRemindersEnabled,
      timezone: pref.timezone,
      weeklySummaryEnabled: key === "weeklySummaryEnabled" ? value : pref.weeklySummaryEnabled,
    };
  },

  /** 开关切换即保存；失败回滚开关并提示 */
  async onToggle(e: WechatMiniprogram.SwitchChange) {
    const key = e.currentTarget.dataset.key as ToggleKey;
    const value = e.detail.value;
    if (!this.preference) {
      this.patchItem(key, { checked: !value });
      return;
    }
    this.patchItem(key, { saving: true });
    try {
      const next = await authedV2<CoachPreferenceView>({
        method: "PUT",
        path: "/api/v2/coach-notifications/preferences",
        body: this.buildBody(key, value),
        idempotencyKey: idemKey(),
      });
      this.preference = next;
      this.patchItem(key, { saving: false });
    } catch {
      this.patchItem(key, { checked: !value, saving: false });
      wx.showToast({ title: "网络不太稳定，稍后再试", icon: "none" });
    }
  },
});
