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
import { suppressNativeTabBar } from "../../platform/tab-bar";
import { appShareMessage, appTimelineShare, enableAppShareMenu } from "../../platform/share";
import { PlanningApi } from "../../api/planning";
import { QualificationApi } from "../../api/qualification";
import {
  clearAllPendingAuthReturns,
  clearPendingAuthReturn,
} from "../../app/navigation/auth-return";
import { currentAuthSession, currentUserId, updateAuthDisplayName } from "../../auth/session";
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
import { MePageRequestOwner, type MePageRequestLease } from "./features/request-owner";

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

interface MeRequest {
  runtime: MePageRequestOwner;
  lease: MePageRequestLease;
}

const requestOwners = new WeakMap<object, MePageRequestOwner>();

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
    wechatChannelOpen: false,
  },

  onHide() {
    if (this.data.profileEditorOpen || this.data.wechatChannelOpen) {
      this.setTabBarHidden(false);
    }
    requestOwners.get(this)?.hide();
  },

  onShow() {
    enableAppShareMenu();
    suppressNativeTabBar();
    if (typeof this.getTabBar === "function") {
      this.getTabBar().setData({
        active: "me",
        hidden: this.data.profileEditorOpen || this.data.wechatChannelOpen,
      });
    }
    const auth = currentAuthSession();
    if (!auth) {
      void wx.reLaunch({ url: "/pages/login/index" });
      return;
    }
    const existing = requestOwners.get(this);
    const sameOwner = existing?.show(auth) ?? false;
    if (!sameOwner) {
      existing?.unload();
      const runtime = new MePageRequestOwner(auth);
      requestOwners.set(this, runtime);
      runtime.show(auth);
      resetForSessionChange(this);
    }
    this.setData({
      profileSaving: false,
      revokingSessionId: "",
      loggingOut: false,
      loggingOutAll: false,
    });
    void this.loadAccount();
    void this.loadProfileContext();
  },

  onUnload() {
    requestOwners.get(this)?.unload();
    requestOwners.delete(this);
  },

  onShareAppMessage() {
    return appShareMessage();
  },

  onShareTimeline() {
    return appTimelineShare();
  },

  async loadAccount() {
    const request = beginRequest(this, "account");
    if (!request) return;
    const token = request.lease.owner.accessToken;
    // 已渲染过内容时静默刷新，避免每次切回 Tab 都整页闪骨架屏。
    const silentRefresh = this.data.status === "ready";
    if (silentRefresh) {
      this.setData({ errorMessage: "", requestId: "" });
    } else {
      this.setData({ status: "loading", errorMessage: "", requestId: "" });
    }
    try {
      const response = await new AccountApi(new ApiClient(undefined, token)).getOverview();
      if (!requestIsCurrent(this, request)) return;
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
      if (!requestIsCurrent(this, request)) return;
      if (error instanceof ApiClientError && error.statusCode === 401) {
        this.finishLogout();
        return;
      }
      const apiError = error instanceof ApiClientError ? error : undefined;
      // 静默刷新失败时保留旧数据，只用行内提示说明刷新未成功。
      this.setData({
        status: silentRefresh ? "ready" : "error",
        errorMessage: apiError?.message ?? "档案没加载出来，别急，再试一次。",
        requestId: apiError?.requestId ?? "",
      });
    }
  },

  retry() {
    void this.loadAccount();
    void this.loadProfileContext();
  },

  async loadProfileContext() {
    const request = beginRequest(this, "profile-context");
    if (!request) return;
    const token = request.lease.owner.accessToken;
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
    const onboarding = new OnboardingApi(client);
    const [qualification, progress] = await Promise.allSettled([
      new QualificationApi(client).getProfile(),
      onboarding.getProgress(),
    ]);
    if (!requestIsCurrent(this, request)) return;
    const unauthorized = [qualification, progress].some(
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
      const failure = profileContextFailure(qualification.reason, "报考资料没同步好，再试一次。");
      Object.assign(changes, {
        qualificationContextStatus: "error",
        qualificationContextError: failure.message,
        qualificationContextRequestId: failure.requestId,
      });
    }

    if (progress.status === "rejected") {
      const failure = profileContextFailure(progress.reason, "建档进度没同步好，再试一次。");
      Object.assign(changes, {
        studyContextStatus: "error",
        studyContextError: failure.message,
        studyContextRequestId: failure.requestId,
        targetContextStatus: "error",
        targetContextError: failure.message,
        targetContextRequestId: failure.requestId,
      });
      this.setData(changes);
      return;
    }

    if (!progress.value.data.studyPreferenceReady) {
      Object.assign(changes, {
        studyPreferenceReady: false,
        studyModeLabel: "待设置",
        weekdayHoursLabel: "待设置",
        weekendHoursLabel: "待设置",
        studyRhythmLabel: "待设置",
        studyContextStatus: "ready",
        studyContextError: "",
        studyContextRequestId: "",
        targetPositionLabel: "完成学习时间设置后生成目标计划",
        targetPositionDepartmentLabel: "当前建档尚未完成",
        targetPositionStatusLabel: "待设置",
        targetContextStatus: "ready",
        targetContextError: "",
        targetContextRequestId: "",
      });
      this.setData(changes);
      return;
    }

    const [preference, dashboard] = await Promise.allSettled([
      onboarding.getStudyPreference(),
      new PlanningApi(client).getToday(),
    ]);
    if (!requestIsCurrent(this, request)) return;
    if (
      [preference, dashboard].some(
        (result) =>
          result.status === "rejected" &&
          result.reason instanceof ApiClientError &&
          result.reason.statusCode === 401,
      )
    ) {
      this.finishLogout();
      return;
    }
    if (preference.status === "fulfilled") {
      Object.assign(changes, studyPreferenceView(preference.value.data), {
        studyContextStatus: "ready",
        studyContextError: "",
        studyContextRequestId: "",
      });
    } else {
      const failure = profileContextFailure(preference.reason, "学习时间没同步好，再试一次。");
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
      const failure = profileContextFailure(dashboard.reason, "目标岗位没同步好，再试一次。");
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
    this.setTabBarHidden(true);
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
      this.setTabBarHidden(false);
      this.setData({ profileEditorOpen: false, profileEditorError: "" });
    }
  },

  async saveProfile() {
    const displayName = this.data.profileDraftName.trim().replace(/\s+/gu, " ");
    if (this.data.profileSaving) return;
    if (!displayName || Array.from(displayName).length > 24) {
      this.setData({ profileEditorError: "昵称请填写 1–24 个字。" });
      return;
    }
    const request = beginRequest(this, "save-profile");
    if (!request) return;
    const token = request.lease.owner.accessToken;
    this.setData({ profileSaving: true, profileEditorError: "" });
    try {
      const { data } = await new AccountApi(new ApiClient(undefined, token)).updateProfile({
        displayName,
        avatarStyle: this.data.profileDraftAvatarStyle,
      });
      if (!requestIsCurrent(this, request)) return;
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
      this.setTabBarHidden(false);
      void wx.showToast({ title: "个人资料已更新", icon: "success" });
    } catch (error) {
      if (!requestIsCurrent(this, request)) return;
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        profileSaving: false,
        profileEditorError: apiError?.message ?? "资料没存上，别急，再试一次。",
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

  openCommunity() {
    void wx.navigateTo({ url: routes.communityTrial });
  },

  openCommunityContributions() {
    void wx.navigateTo({ url: routes.communityContributions });
  },

  openMembership() {
    void wx.navigateTo({ url: routes.membership });
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

  openWechatChannel() {
    this.setTabBarHidden(true);
    this.setData({ wechatChannelOpen: true });
  },

  closeWechatChannel() {
    this.setData({ wechatChannelOpen: false });
    this.setTabBarHidden(false);
  },

  setTabBarHidden(hidden: boolean) {
    if (typeof this.getTabBar === "function") {
      this.getTabBar().setData({ hidden });
    }
    suppressNativeTabBar();
  },

  openPrivacy() {
    void wx.navigateTo({ url: routes.privacy });
  },

  openSupport() {
    void wx.navigateTo({ url: routes.support });
  },

  openNotificationSettings() {
    void wx.navigateTo({ url: routes.notificationSettings });
  },

  async openAuthorizationSettings() {
    const request = beginRequest(this, "authorization-settings");
    if (!request) return;
    try {
      await wx.openSetting();
    } catch {
      if (!requestIsCurrent(this, request)) return;
      void wx.showToast({ title: "授权设置没打开，再试一次", icon: "none" });
    }
  },

  showAbout() {
    void wx.showModal({
      title: "关于岸岸",
      content:
        "岸岸是你的备考搭子，和你一起盯目标、看进步、安排每天的学习节奏。\n\n你只管一步步往前走，岸岸一直在旁边。",
      showCancel: false,
      confirmText: "知道了",
    });
  },

  revokeSession(event: WechatMiniprogram.BaseEvent) {
    const sessionId = String(event.currentTarget.dataset.sessionId ?? "");
    const session = this.data.sessions.find((item) => item.sessionId === sessionId);
    if (!session || session.isCurrent || this.data.revokingSessionId) return;
    const runtime = requestOwners.get(this);
    wx.showModal({
      title: "退出这台设备？",
      content: "该设备需要重新通过微信登录，不影响当前设备。",
      confirmText: "确认退出",
      confirmColor: "#C83F56",
      success: (result) => {
        if (result.confirm && requestRuntimeIsActive(this, runtime)) {
          void this.confirmRevokeSession(sessionId);
        }
      },
    });
  },

  async confirmRevokeSession(sessionId: string) {
    const request = beginRequest(this, "revoke-session");
    if (!request) return;
    const token = request.lease.owner.accessToken;
    this.setData({ revokingSessionId: sessionId, errorMessage: "" });
    try {
      await new AccountApi(new ApiClient(undefined, token)).revokeSession(sessionId);
      if (!requestIsCurrent(this, request)) return;
      await this.loadAccount();
    } catch (error) {
      if (!requestIsCurrent(this, request)) return;
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        revokingSessionId: "",
        errorMessage: apiError?.message ?? "设备没退出来，别急，再试一次。",
      });
    }
  },

  requestLogout() {
    if (this.data.loggingOut || this.data.loggingOutAll) return;
    const runtime = requestOwners.get(this);
    wx.showModal({
      title: "退出当前设备？",
      content: "你的资格档案和学习记录仍保存在账户中。",
      confirmText: "退出登录",
      success: (result) => {
        if (result.confirm && requestRuntimeIsActive(this, runtime)) {
          void this.logoutCurrent();
        }
      },
    });
  },

  async logoutCurrent() {
    const request = beginRequest(this, "logout-current");
    if (!request) return;
    const token = request.lease.owner.accessToken;
    this.setData({ loggingOut: true });
    try {
      await new AccountApi(new ApiClient(undefined, token)).logoutCurrent();
      if (!requestIsCurrent(this, request)) return;
      this.finishLogout();
    } catch (error) {
      if (!requestIsCurrent(this, request)) return;
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        loggingOut: false,
        errorMessage: apiError?.message ?? "没退成功，别急，再试一次。",
      });
    }
  },

  requestLogoutAll() {
    if (this.data.loggingOut || this.data.loggingOutAll) return;
    const runtime = requestOwners.get(this);
    wx.showModal({
      title: "退出全部设备？",
      content: "包括当前设备在内的全部登录会话都会失效。",
      confirmText: "全部退出",
      confirmColor: "#C83F56",
      success: (result) => {
        if (result.confirm && requestRuntimeIsActive(this, runtime)) {
          void this.logoutAll();
        }
      },
    });
  },

  async logoutAll() {
    const request = beginRequest(this, "logout-all");
    if (!request) return;
    const token = request.lease.owner.accessToken;
    this.setData({ loggingOutAll: true });
    try {
      await new AccountApi(new ApiClient(undefined, token)).logoutAll();
      if (!requestIsCurrent(this, request)) return;
      this.finishLogout();
    } catch (error) {
      if (!requestIsCurrent(this, request)) return;
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        loggingOutAll: false,
        errorMessage: apiError?.message ?? "全部退出没成功，别急，再试一次。",
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

function beginRequest(page: object, lane: string): MeRequest | null {
  const runtime = requestOwners.get(page);
  const lease = runtime?.begin(lane, currentAuthSession()) ?? null;
  return runtime && lease ? { runtime, lease } : null;
}

function requestIsCurrent(page: object, request: MeRequest): boolean {
  return (
    requestOwners.get(page) === request.runtime &&
    request.runtime.owns(request.lease, currentAuthSession())
  );
}

function requestRuntimeIsActive(page: object, runtime: MePageRequestOwner | undefined): boolean {
  return (
    runtime !== undefined &&
    requestOwners.get(page) === runtime &&
    runtime.active(currentAuthSession())
  );
}

function resetForSessionChange(page: { setData(patch: Record<string, unknown>): void }): void {
  page.setData({
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
    establishedLabel: "",
    studyDayLabel: "国考备考中",
    sessions: [],
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
  });
}

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
