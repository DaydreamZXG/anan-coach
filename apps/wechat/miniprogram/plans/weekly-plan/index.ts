import { PlanningApi } from "../../api/planning";
import { OnboardingApi } from "../../api/onboarding";
import type { WeeklyPlanVersion } from "../../api/generated/types.gen";
import { errorView, requireAuthenticatedClient } from "../../features/learning-brain/presentation";
import {
  durationLabel,
  planningReasonLabel,
  weeklyRhythmLabel,
} from "../shared/features/planning/presentation";
import { onboardingNavigationTarget, routes } from "../../app/navigation/routes";
import { navigateBackOr } from "../shared/app/navigation/safe-back";
import { currentAuthSession } from "../../auth/session";
import { storeCompanionLaunchContext } from "../../features/companion/context-store";

const weekdayLabels = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const modeLabels = { training: "针对训练", mock: "整卷模考", recovery: "恢复复盘" } as const;

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    week: null as WeeklyPlanVersion | null,
    reasonLabel: "",
    rhythmLabel: "",
    expandedDayIndex: -1,
    totalScheduledLabel: "",
    completedSummary: "",
    adjustmentLabel: "",
    weekDayChipLabel: "",
    completedChipLabel: "",
    planChipLabel: "",
    hasAdjustmentSuggestion: false,
    adjustmentSuggestion: "",
    days: [] as Array<
      WeeklyPlanVersion["days"][number] & {
        weekdayLabel: string;
        modeLabel: string;
        statusLabel: string;
        statusTone: string;
        plannedDurationLabel: string;
        scheduledDurationLabel: string;
      }
    >,
  },
  onLoad() {
    void this.loadWeek();
  },
  async loadWeek() {
    const client = requireAuthenticatedClient();
    if (!client) return;
    try {
      const { data: progress } = await new OnboardingApi(client).getProgress();
      if (!progress.studyPreferenceReady) {
        await wx.reLaunch({ url: onboardingNavigationTarget(progress.nextStep).url });
        return;
      }
      const { data } = await new PlanningApi(client).getWeek();
      const todayIndex = data.days.findIndex((day) => day.status === "today");
      const completedTaskCount = data.days.reduce(
        (total, day) => total + day.completedTaskCount,
        0,
      );
      const totalTaskCount = data.days.reduce((total, day) => total + day.totalTaskCount, 0);
      const totalScheduledMinutes = data.days.reduce(
        (total, day) => total + day.scheduledMinutes,
        0,
      );
      const reasonLabel = planningReasonLabel(data.replanReasonCodes, "本周安排已生成");
      const hasAdjustmentSuggestion =
        data.versionNo > 1 || Boolean(data.supersedesWeeklyPlanVersionId);
      this.setData({
        status: "ready",
        week: data,
        reasonLabel,
        rhythmLabel: weeklyRhythmLabel(data.rhythm),
        expandedDayIndex: todayIndex,
        totalScheduledLabel: durationLabel(totalScheduledMinutes),
        completedSummary: `${String(completedTaskCount)} / ${String(totalTaskCount)} 项`,
        adjustmentLabel:
          data.versionNo <= 1 ? "初始安排" : `已调整 ${String(data.versionNo - 1)} 次`,
        weekDayChipLabel:
          todayIndex >= 0 ? `本周第 ${String(todayIndex + 1)} 天` : "本周进度待确认",
        completedChipLabel: `已完成 ${String(completedTaskCount)} / ${String(totalTaskCount)} 项`,
        planChipLabel: `计划 ${durationLabel(totalScheduledMinutes)}`,
        hasAdjustmentSuggestion,
        adjustmentSuggestion: hasAdjustmentSuggestion
          ? `这版计划已根据${reasonLabel}更新。你可以查看每天的时间与任务变化。`
          : "",
        days: data.days.map((day) => ({
          ...day,
          weekdayLabel: weekdayLabels[day.weekday] ?? "",
          modeLabel: modeLabels[day.mode],
          statusLabel:
            day.status === "today"
              ? "今天"
              : day.status === "past"
                ? day.totalTaskCount > 0 && day.completedTaskCount >= day.totalTaskCount
                  ? "已完成"
                  : "已结束"
                : day.mode === "recovery"
                  ? "复盘"
                  : day.mode === "mock"
                    ? "模考"
                    : "待执行",
          statusTone:
            day.status === "today"
              ? "today"
              : day.status === "past"
                ? "past"
                : day.mode === "recovery"
                  ? "recovery"
                  : "upcoming",
          plannedDurationLabel: durationLabel(day.plannedMinutes),
          scheduledDurationLabel: durationLabel(day.scheduledMinutes),
        })),
      });
    } catch (error) {
      const view = errorView(error, "周计划加载失败，请重试。");
      this.setData({ status: "error", errorMessage: view.message, requestId: view.requestId });
    }
  },
  retry() {
    void this.loadWeek();
  },
  toggleDay(event: WechatMiniprogram.BaseEvent) {
    const index = Number(event.currentTarget.dataset.index ?? -1);
    if (!Number.isInteger(index) || index < 0 || index >= this.data.days.length) return;
    this.setData({ expandedDayIndex: this.data.expandedDayIndex === index ? -1 : index });
  },
  openAdjustment() {
    void wx.navigateTo({ url: routes.dayAdjustment });
  },
  goToday() {
    void wx.switchTab({ url: routes.today });
  },
  askAnan() {
    const auth = currentAuthSession();
    if (!auth) {
      void wx.reLaunch({ url: routes.login });
      return;
    }
    const week = this.data.week;
    if (!week) return;
    storeCompanionLaunchContext(auth.userId, {
      sourceLabel: "本周学习安排",
      title: `${week.weekStart} 起 · ${this.data.rhythmLabel}`,
      summary: `本周安排 ${this.data.totalScheduledLabel}，已完成 ${this.data.completedSummary}，${this.data.adjustmentLabel}。${this.data.reasonLabel}`,
      suggestedQuestion:
        "结合这版周计划、我的能力和目标岗位，帮我检查本周节奏；如需调整，请先说明依据和会改变哪些天。",
      routeId: "plans/weekly-plan/index",
      workspace: "today",
      objectRefs: [
        {
          type: "plan",
          id: week.weeklyPlanVersionId,
          version: String(week.versionNo),
        },
      ],
    });
    void wx.switchTab({ url: routes.anan });
  },
  exit() {
    navigateBackOr(routes.today);
  },
});
