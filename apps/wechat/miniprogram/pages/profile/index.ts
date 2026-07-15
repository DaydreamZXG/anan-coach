import { AccountApi } from "../../api/account";
import { ApiClient, ApiClientError } from "../../api/client";
import type { AccountSessionView } from "../../api/generated/types.gen";
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
  },

  onShow() {
    if (!currentAccessToken()) {
      void wx.reLaunch({ url: "/pages/login/index" });
      return;
    }
    void this.loadAccount();
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
    } else {
      clearAllPendingAuthReturns();
      clearAllEssayPending();
      clearAllOnboardingPending();
      clearAllPositionState();
    }
    clearAuthSession();
    void wx.reLaunch({ url: "/pages/login/index" });
  },
});
