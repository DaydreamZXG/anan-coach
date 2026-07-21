import { AccountApi } from "../../api/account";
import { ApiClient, ApiClientError } from "../../api/client";
import { DailyOpeningApi } from "../../api/daily-opening";
import type {
  AccountSessionView,
  OpeningPreferenceCommand,
  OpeningPreferenceView,
} from "../../api/generated/types.gen";
import {
  clearAllPendingAuthReturns,
  clearPendingAuthReturn,
} from "../../app/navigation/auth-return";
import { clearAuthSession, currentAccessToken, currentUserId } from "../../auth/session";
import {
  clearAllEssayPending,
  clearEssayPendingForUser,
} from "../../features/essay-task/pending-store";
import {
  clearAllOnboardingPending,
  clearOnboardingPendingForUser,
} from "../../features/onboarding/pending-store";
import {
  clearAllPositionState,
  clearPositionStateForUser,
} from "../../features/positions/state-store";
import {
  clearAllQualificationProfileEditPending,
  clearPendingQualificationProfileEdit,
} from "../../features/qualification/profile-edit-pending";
import { routes } from "../../app/navigation/routes";
import { createCapacityIdempotencyKey } from "../today/capacity";

type SessionView = AccountSessionView & {
  lastSeenLabel: string;
};

function dateLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  const year = String(date.getFullYear());
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}.${month}.${day}`;
}

function sessionView(session: AccountSessionView): SessionView {
  return {
    ...session,
    lastSeenLabel: `${dateLabel(session.lastSeenAt)} 活跃`,
  };
}

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    accountIdLabel: "",
    accountStatusLabel: "",
    wechatLinkedLabel: "",
    profileVersion: 0,
    establishedLabel: "",
    sessions: [] as SessionView[],
    revokingSessionId: "",
    loggingOut: false,
    loggingOutAll: false,
    openingPreference: null as OpeningPreferenceView | null,
    openingPreferenceLabel: "读取中",
    openingPreferenceWriting: false,
  },

  onShow() {
    if (!currentAccessToken()) {
      void wx.reLaunch({ url: "/pages/login/index" });
      return;
    }
    void this.loadAccount();
    void this.loadDailyOpeningPreference();
  },

  async loadAccount() {
    const token = currentAccessToken();
    if (!token) return;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const response = await new AccountApi(new ApiClient(undefined, token)).getOverview();
      this.setData({
        status: "ready",
        accountIdLabel: response.data.accountId.slice(0, 8).toUpperCase(),
        accountStatusLabel: response.data.accountStatus === "active" ? "正常" : "需处理",
        wechatLinkedLabel: response.data.wechatLinked ? "已绑定" : "未绑定",
        profileVersion: response.data.profileVersion,
        establishedLabel: dateLabel(response.data.establishedAt),
        sessions: response.data.sessions.map(sessionView),
      });
    } catch (error) {
      if (error instanceof ApiClientError && error.statusCode === 401) {
        this.finishLogout();
        return;
      }
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        status: "error",
        errorMessage: apiError?.message ?? "账户档案加载失败，请重试。",
        requestId: apiError?.requestId ?? "",
      });
    }
  },

  retry() {
    void this.loadAccount();
    void this.loadDailyOpeningPreference();
  },

  async loadDailyOpeningPreference() {
    const token = currentAccessToken();
    if (!token) return;
    try {
      const response = await new DailyOpeningApi(new ApiClient(undefined, token)).getPreference();
      const preference = response.data;
      this.setData({
        openingPreference: preference,
        openingPreferenceLabel: preference.enabled
          ? `已开启 · 天气${preference.weatherEnabled ? "开" : "关"} · 咖啡${preference.coffeePromptEnabled ? "开" : "关"}`
          : "已关闭",
      });
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        openingPreferenceLabel: "设置读取失败",
        errorMessage: apiError?.message ?? "每日开场设置读取失败。",
      });
    }
  },

  manageDailyOpening() {
    const preference = this.data.openingPreference;
    if (!preference || this.data.openingPreferenceWriting) return;
    const actions = preference.enabled
      ? [
          "关闭每日开场",
          preference.weatherEnabled ? "关闭天气" : "开启天气",
          preference.coffeePromptEnabled ? "关闭咖啡询问" : "开启咖啡询问",
        ]
      : ["开启每日开场"];
    wx.showActionSheet({
      itemList: actions,
      success: (result) => {
        const command: OpeningPreferenceCommand = {
          enabled: preference.enabled,
          weatherEnabled: preference.weatherEnabled,
          coffeePromptEnabled: preference.coffeePromptEnabled,
        };
        if (!preference.enabled) command.enabled = true;
        else if (result.tapIndex === 0) command.enabled = false;
        else if (result.tapIndex === 1) command.weatherEnabled = !preference.weatherEnabled;
        else if (result.tapIndex === 2)
          command.coffeePromptEnabled = !preference.coffeePromptEnabled;
        void this.updateDailyOpeningPreference(command);
      },
    });
  },

  async updateDailyOpeningPreference(command: OpeningPreferenceCommand) {
    const token = currentAccessToken();
    if (!token || this.data.openingPreferenceWriting) return;
    this.setData({ openingPreferenceWriting: true, errorMessage: "" });
    try {
      const key = await createCapacityIdempotencyKey();
      await new DailyOpeningApi(new ApiClient(undefined, token)).updatePreference(command, key);
      this.setData({ openingPreferenceWriting: false });
      await this.loadDailyOpeningPreference();
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        openingPreferenceWriting: false,
        errorMessage: apiError?.message ?? "每日开场设置没有写入，请重试。",
      });
    }
  },

  openCommunityDraft() {
    void wx.navigateTo({ url: routes.communityDraft });
  },

  openCommunityTrial() {
    void wx.navigateTo({
      url: `${routes.communityTrial}?track=xingce&applicability=${encodeURIComponent("资料分析")}`,
    });
  },

  openCommunityContributions() {
    void wx.navigateTo({ url: routes.communityContributions });
  },

  openLearningProfile() {
    void wx.navigateTo({ url: routes.learningProfile });
  },

  openQualificationProfile() {
    void wx.navigateTo({ url: routes.qualificationProfile });
  },

  openPrivateKnowledge() {
    void wx.navigateTo({ url: routes.privateKnowledge });
  },

  openCompanion() {
    void wx.navigateTo({ url: routes.companion });
  },

  openPlanHistory() {
    void wx.navigateTo({ url: routes.planHistory });
  },

  openReportCenter() {
    void wx.navigateTo({ url: routes.reportCenter });
  },

  openPrivacy() {
    void wx.navigateTo({ url: routes.privacy });
  },

  openNotificationSettings() {
    void wx.navigateTo({ url: routes.notificationSettings });
  },

  openSupport() {
    void wx.navigateTo({ url: routes.support });
  },

  revokeSession(event: WechatMiniprogram.BaseEvent) {
    const sessionId = String(event.currentTarget.dataset.sessionId ?? "");
    const session = this.data.sessions.find((item) => item.sessionId === sessionId);
    if (!session || session.isCurrent || this.data.revokingSessionId) return;
    wx.showModal({
      title: "退出这台设备？",
      content: "该设备需要重新通过微信登录，不影响当前设备。",
      confirmText: "确认退出",
      confirmColor: "#C83F56",
      success: (result) => {
        if (result.confirm) void this.confirmRevokeSession(sessionId);
      },
    });
  },

  async confirmRevokeSession(sessionId: string) {
    const token = currentAccessToken();
    if (!token) return;
    this.setData({ revokingSessionId: sessionId, errorMessage: "" });
    try {
      await new AccountApi(new ApiClient(undefined, token)).revokeSession(sessionId);
      await this.loadAccount();
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        revokingSessionId: "",
        errorMessage: apiError?.message ?? "设备退出失败，请重试。",
      });
    }
  },

  requestLogout() {
    if (this.data.loggingOut || this.data.loggingOutAll) return;
    wx.showModal({
      title: "退出当前设备？",
      content: "你的资格档案和学习记录仍保存在账户中。",
      confirmText: "退出登录",
      success: (result) => {
        if (result.confirm) void this.logoutCurrent();
      },
    });
  },

  async logoutCurrent() {
    const token = currentAccessToken();
    if (!token) return;
    this.setData({ loggingOut: true });
    try {
      await new AccountApi(new ApiClient(undefined, token)).logoutCurrent();
      this.finishLogout();
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        loggingOut: false,
        errorMessage: apiError?.message ?? "退出失败，请重试。",
      });
    }
  },

  requestLogoutAll() {
    if (this.data.loggingOut || this.data.loggingOutAll) return;
    wx.showModal({
      title: "退出全部设备？",
      content: "包括当前设备在内的全部登录会话都会失效。",
      confirmText: "全部退出",
      confirmColor: "#C83F56",
      success: (result) => {
        if (result.confirm) void this.logoutAll();
      },
    });
  },

  async logoutAll() {
    const token = currentAccessToken();
    if (!token) return;
    this.setData({ loggingOutAll: true });
    try {
      await new AccountApi(new ApiClient(undefined, token)).logoutAll();
      this.finishLogout();
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        loggingOutAll: false,
        errorMessage: apiError?.message ?? "全部设备退出失败，请重试。",
      });
    }
  },

  finishLogout() {
    const userId = currentUserId();
    if (userId) {
      clearPendingAuthReturn(userId);
      clearEssayPendingForUser(userId);
      clearOnboardingPendingForUser(userId);
      clearPositionStateForUser(userId);
      clearPendingQualificationProfileEdit(userId);
    } else {
      clearAllPendingAuthReturns();
      clearAllEssayPending();
      clearAllOnboardingPending();
      clearAllPositionState();
      clearAllQualificationProfileEditPending();
    }
    clearAuthSession();
    void wx.reLaunch({ url: "/pages/login/index" });
  },
});
