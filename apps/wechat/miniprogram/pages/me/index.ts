import { AccountApi } from "../../api/account";
import { clearSession } from "../../api/auth";
import { ApiClient, ApiClientError } from "../../api/client";
import type {
  AccountProfileUpdate,
  AccountSessionView,
  QualificationFact,
  QualificationProfile,
  WeeklyStudyPreference,
} from "../../api/generated/types.gen";
import { OnboardingApi } from "../../api/onboarding";
import { PlanningApi } from "../../api/planning";
import { QualificationApi } from "../../api/qualification";
import {
  clearAllPendingAuthReturns,
  clearPendingAuthReturn,
} from "../../app/navigation/auth-return";
import { currentAccessToken, currentUserId, updateAuthDisplayName } from "../../auth/session";
import {
  clearAllEssayPending,
  clearEssayPendingForUser,
} from "../../features/essay-task/pending-store";
import {
  clearAllEssayDraftingDrafts,
  clearEssayDraftingDraftsForUser,
} from "../../features/essay-task/drafting-store";
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
import {
  clearAllDataRightsPending,
  clearDataRightsPendingForUser,
} from "../../features/data-rights/pending-store";
import { routes } from "../../app/navigation/routes";

type SessionView = AccountSessionView & {
  lastSeenLabel: string;
};
type AvatarStyle = AccountProfileUpdate["avatarStyle"];

const avatarOptions: ReadonlyArray<{ value: AvatarStyle; label: string }> = [
  { value: "ocean", label: "远海" },
  { value: "aurora", label: "极光" },
  { value: "sunrise", label: "朝阳" },
  { value: "violet", label: "星云" },
  { value: "graphite", label: "墨石" },
  { value: "mint", label: "薄荷" },
];

function defaultAvatarStyle(): AvatarStyle {
  return "ocean";
}

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

function displayNameOrDefault(value: string | null | undefined): string {
  return value?.trim() || "岸岸学员";
}

function avatarInitial(value: string): string {
  return Array.from(value.trim()).at(0) ?? "岸";
}

type QualificationKey = QualificationFact["key"];

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    accountIdLabel: "",
    accountStatusLabel: "",
    wechatLinkedLabel: "",
    profileVersion: 0,
    displayName: "岸岸学员",
    avatarInitial: "岸",
    avatarStyle: defaultAvatarStyle(),
    avatarOptions,
    profileEditorOpen: false,
    profileDraftName: "",
    profileDraftInitial: "岸",
    profileDraftAvatarStyle: defaultAvatarStyle(),
    profileSaving: false,
    profileEditorError: "",
    establishedLabel: "",
    studyDayLabel: "国考备考中",
    sessions: [] as SessionView[],
    revokingSessionId: "",
    loggingOut: false,
    loggingOutAll: false,
    qualificationReady: false,
    educationMajorLabel: "待补充",
    candidateIdentityLabel: "待补充",
    politicalStatusLabel: "待补充",
    regionLabel: "待补充",
    workExperienceLabel: "待补充",
    serviceProjectLabel: "待补充",
    studyPreferenceReady: false,
    studyModeLabel: "待设置",
    weekdayHoursLabel: "待设置",
    weekendHoursLabel: "待设置",
    studyRhythmLabel: "待设置",
    targetPositionLabel: "尚未确认主目标岗位",
    targetPositionDepartmentLabel: "确认岗位后显示招录机关",
    targetPositionStatusLabel: "待确认",
    qualificationContextStatus: "loading",
    qualificationContextError: "",
    qualificationContextRequestId: "",
    studyContextStatus: "loading",
    studyContextError: "",
    studyContextRequestId: "",
    targetContextStatus: "loading",
    targetContextError: "",
    targetContextRequestId: "",
  },

  onHide() {
    if (this.data.profileEditorOpen) {
      void wx.showTabBar({ animation: false }).catch(() => undefined);
    }
  },

  onShow() {
    if (typeof this.getTabBar === "function") {
      this.getTabBar().setData({ active: "me" });
    }
    if (!currentAccessToken()) {
      void wx.reLaunch({ url: "/pages/login/index" });
      return;
    }
    void this.loadAccount();
    void this.loadProfileContext();
  },

  async loadAccount() {
    const token = currentAccessToken();
    if (!token) return;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const response = await new AccountApi(new ApiClient(undefined, token)).getOverview();
      const displayName = displayNameOrDefault(response.data.displayName);
      updateAuthDisplayName(response.data.displayName);
      this.setData({
        status: "ready",
        accountIdLabel: response.data.accountId.slice(0, 8).toUpperCase(),
        accountStatusLabel: response.data.accountStatus === "active" ? "正常" : "需处理",
        wechatLinkedLabel: response.data.wechatLinked ? "已绑定" : "未绑定",
        profileVersion: response.data.profileVersion,
        displayName,
        avatarInitial: avatarInitial(displayName),
        avatarStyle: response.data.avatarStyle,
        establishedLabel: dateLabel(response.data.establishedAt),
        studyDayLabel: studyDayLabel(response.data.establishedAt),
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
    void this.loadProfileContext();
  },

  async loadProfileContext() {
    const token = currentAccessToken();
    if (!token) return;
    this.setData({
      qualificationContextStatus: "loading",
      qualificationContextError: "",
      qualificationContextRequestId: "",
      studyContextStatus: "loading",
      studyContextError: "",
      studyContextRequestId: "",
      targetContextStatus: "loading",
      targetContextError: "",
      targetContextRequestId: "",
    });
    const client = new ApiClient(undefined, token);
    const [qualification, preference, dashboard] = await Promise.allSettled([
      new QualificationApi(client).getProfile(),
      new OnboardingApi(client).getStudyPreference(),
      new PlanningApi(client).getToday(),
    ]);
    const unauthorized = [qualification, preference, dashboard].some(
      (result) =>
        result.status === "rejected" &&
        result.reason instanceof ApiClientError &&
        result.reason.statusCode === 401,
    );
    if (unauthorized) {
      this.finishLogout();
      return;
    }
    const changes: Record<string, unknown> = {};
    if (qualification.status === "fulfilled") {
      Object.assign(changes, qualificationView(qualification.value.data), {
        qualificationContextStatus: "ready",
        qualificationContextError: "",
        qualificationContextRequestId: "",
      });
    } else {
      const failure = profileContextFailure(qualification.reason, "报考资料没有同步完成，请重试。");
      Object.assign(changes, {
        qualificationContextStatus: "error",
        qualificationContextError: failure.message,
        qualificationContextRequestId: failure.requestId,
      });
    }
    if (preference.status === "fulfilled") {
      Object.assign(changes, studyPreferenceView(preference.value.data), {
        studyContextStatus: "ready",
        studyContextError: "",
        studyContextRequestId: "",
      });
    } else {
      const failure = profileContextFailure(preference.reason, "学习时间没有同步完成，请重试。");
      Object.assign(changes, {
        studyContextStatus: "error",
        studyContextError: failure.message,
        studyContextRequestId: failure.requestId,
      });
    }
    if (dashboard.status === "fulfilled") {
      const positionName = dashboard.value.data.goalGap.positionName?.trim();
      const departmentName = dashboard.value.data.goalGap.departmentName?.trim();
      Object.assign(changes, {
        targetPositionLabel: positionName || "尚未确认主目标岗位",
        targetPositionDepartmentLabel:
          departmentName || (positionName ? "招录机关待补充" : "确认岗位后显示招录机关"),
        targetPositionStatusLabel: positionName ? "目标已确认" : "待确认",
        targetContextStatus: "ready",
        targetContextError: "",
        targetContextRequestId: "",
      });
    } else {
      const failure = profileContextFailure(dashboard.reason, "目标岗位没有同步完成，请重试。");
      Object.assign(changes, {
        targetContextStatus: "error",
        targetContextError: failure.message,
        targetContextRequestId: failure.requestId,
      });
    }
    this.setData(changes);
  },

  retryProfileContext() {
    void this.loadProfileContext();
  },

  openProfileEditor() {
    if (this.data.profileSaving) return;
    // custom-tab-bar 在部分渲染层级高于页面弹层，弹窗期间按官方方案隐藏。
    void wx.hideTabBar({ animation: false }).catch(() => undefined);
    this.setData({
      profileEditorOpen: true,
      profileDraftName: this.data.displayName === "岸岸学员" ? "" : this.data.displayName,
      profileDraftInitial:
        this.data.displayName === "岸岸学员" ? "岸" : avatarInitial(this.data.displayName),
      profileDraftAvatarStyle: this.data.avatarStyle,
      profileEditorError: "",
    });
  },

  inputProfileName(event: WechatMiniprogram.Input) {
    this.setData({
      profileDraftName: event.detail.value,
      profileDraftInitial: avatarInitial(event.detail.value),
      profileEditorError: "",
    });
  },

  chooseAvatarStyle(event: WechatMiniprogram.BaseEvent) {
    const value = String(event.currentTarget.dataset.value ?? "");
    if (avatarOptions.some((option) => option.value === value)) {
      this.setData({
        profileDraftAvatarStyle: value as AvatarStyle,
        profileEditorError: "",
      });
    }
  },

  closeProfileEditor() {
    if (!this.data.profileSaving) {
      void wx.showTabBar({ animation: false }).catch(() => undefined);
      this.setData({ profileEditorOpen: false, profileEditorError: "" });
    }
  },

  async saveProfile() {
    const token = currentAccessToken();
    const displayName = this.data.profileDraftName.trim().replace(/\s+/gu, " ");
    if (!token || this.data.profileSaving) return;
    if (!displayName || Array.from(displayName).length > 24) {
      this.setData({ profileEditorError: "昵称请填写 1–24 个字。" });
      return;
    }
    this.setData({ profileSaving: true, profileEditorError: "" });
    try {
      const { data } = await new AccountApi(new ApiClient(undefined, token)).updateProfile({
        displayName,
        avatarStyle: this.data.profileDraftAvatarStyle,
      });
      updateAuthDisplayName(data.displayName);
      this.setData({
        displayName: data.displayName,
        avatarInitial: avatarInitial(data.displayName),
        avatarStyle: data.avatarStyle,
        profileDraftName: data.displayName,
        profileDraftInitial: avatarInitial(data.displayName),
        profileDraftAvatarStyle: data.avatarStyle,
        profileSaving: false,
        profileEditorOpen: false,
      });
      void wx.showTabBar({ animation: false }).catch(() => undefined);
      void wx.showToast({ title: "个人资料已更新", icon: "success" });
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        profileSaving: false,
        profileEditorError: apiError?.message ?? "个人资料没有保存成功，请重试。",
      });
    }
  },

  noop() {},

  openLearningProfile() {
    void wx.switchTab({ url: routes.ability });
  },

  openQualificationProfile() {
    void wx.navigateTo({ url: routes.profileArchive });
  },

  openStudyPreference() {
    void wx.navigateTo({ url: routes.studyPreference });
  },

  openPositions() {
    void wx.navigateTo({ url: routes.positions });
  },

  openPrivateKnowledge() {
    void wx.navigateTo({ url: routes.privateKnowledge });
  },

  openLearningPackets() {
    void wx.navigateTo({ url: routes.learningPackets });
  },

  openCompanion() {
    void wx.switchTab({ url: routes.anan });
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

  async openAuthorizationSettings() {
    try {
      await wx.openSetting();
    } catch {
      void wx.showToast({ title: "授权设置没有打开，请重试", icon: "none" });
    }
  },

  showAbout() {
    void wx.showModal({
      title: "关于岸岸",
      content: "岸岸是围绕国考目标岗位、能力变化和每日行动工作的 AI 备考伙伴。",
      showCancel: false,
      confirmText: "知道了",
    });
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
      clearEssayDraftingDraftsForUser(userId);
      clearOnboardingPendingForUser(userId);
      clearPositionStateForUser(userId);
      clearPendingQualificationProfileEdit(userId);
      clearDataRightsPendingForUser(userId);
    } else {
      clearAllPendingAuthReturns();
      clearAllEssayPending();
      clearAllEssayDraftingDrafts();
      clearAllOnboardingPending();
      clearAllPositionState();
      clearAllQualificationProfileEditPending();
      clearAllDataRightsPending();
    }
    clearSession();
    void wx.reLaunch({ url: "/pages/login/index" });
  },
});

function qualificationView(profile: QualificationProfile) {
  const education = factText(profile, "education");
  const major = factText(profile, "major");
  const workYears = factNumber(profile, "work_years");
  const serviceProject = factText(profile, "service_project");
  return {
    qualificationReady: profile.facts.length > 0,
    educationMajorLabel:
      education || major ? [education, major].filter(Boolean).join(" · ") : "待补充",
    candidateIdentityLabel:
      factBoolean(profile, "fresh_graduate") === null
        ? "待补充"
        : factBoolean(profile, "fresh_graduate")
          ? "应届高校毕业生"
          : "社会人员",
    politicalStatusLabel: factText(profile, "political_status") || "待补充",
    regionLabel: factText(profile, "hukou") || "未填写户籍 / 生源地",
    workExperienceLabel:
      workYears === null ? "待补充" : workYears === 0 ? "无" : `${String(workYears)} 年工作经历`,
    serviceProjectLabel: serviceProject && serviceProject !== "无" ? serviceProject : "无",
  };
}

function studyPreferenceView(preference: WeeklyStudyPreference) {
  return {
    studyPreferenceReady: true,
    studyModeLabel: "每周节奏已设置",
    weekdayHoursLabel: hoursLabel(preference.weekdayMinutes),
    weekendHoursLabel: hoursLabel(preference.weekendMinutes),
    studyRhythmLabel: `长块 ${hoursLabel(preference.longBlockMinutes)}`,
  };
}

function fact(profile: QualificationProfile, key: QualificationKey): QualificationFact | undefined {
  return profile.facts.find((item) => item.key === key);
}

function factText(profile: QualificationProfile, key: QualificationKey): string {
  const value = fact(profile, key)?.value;
  return typeof value === "string" ? value.trim() : "";
}

function factNumber(profile: QualificationProfile, key: QualificationKey): number | null {
  const value = fact(profile, key)?.value;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function factBoolean(profile: QualificationProfile, key: QualificationKey): boolean | null {
  const value = fact(profile, key)?.value;
  return typeof value === "boolean" ? value : null;
}

function hoursLabel(minutes: number): string {
  const hours = minutes / 60;
  return `${Number.isInteger(hours) ? String(hours) : hours.toFixed(1)} h`;
}

function studyDayLabel(value: string): string {
  const established = new Date(value);
  if (Number.isNaN(established.getTime())) return "国考备考中";
  const elapsed = Math.max(0, Date.now() - established.getTime());
  const days = Math.floor(elapsed / 86_400_000) + 1;
  return `国考备考第 ${String(days)} 天`;
}

function profileContextFailure(
  error: unknown,
  fallback: string,
): { message: string; requestId: string } {
  const apiError = error instanceof ApiClientError ? error : null;
  return {
    message: apiError?.message ?? fallback,
    requestId: apiError?.requestId ?? "",
  };
}
