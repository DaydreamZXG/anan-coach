import { PlanningApi } from "../../api/planning";
import type { DailySettlementView } from "../../api/generated/types.gen";
import {
  errorView,
  requireAuthenticatedClient,
  trackLabel,
} from "../../features/learning-brain/presentation";
import { routes } from "../../app/navigation/routes";
import { navigateBackOr } from "../shared/app/navigation/safe-back";
import { currentAuthSession } from "../../auth/session";
import { storeCompanionLaunchContext } from "../../features/companion/context-store";

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    settlement: null as DailySettlementView | null,
    riskLabel: "",
    elapsedLabel: "",
    scheduledLabel: "",
    completionLabel: "",
    varianceLabel: "",
    hasSummary: false,
    primaryOpenTask: null as
      | (DailySettlementView["tasks"][number] & {
          trackLabel: string;
          statusLabel: string;
          statusTone: string;
          elapsedLabel: string;
          plannedLabel: string;
        })
      | null,
    additionalOpenTaskCount: 0,
    tasks: [] as Array<
      DailySettlementView["tasks"][number] & {
        trackLabel: string;
        statusLabel: string;
        statusTone: string;
        elapsedLabel: string;
        plannedLabel: string;
      }
    >,
  },

  onLoad() {
    void this.loadSettlement();
  },
  async loadSettlement() {
    const client = requireAuthenticatedClient();
    if (!client) return;
    try {
      const { data } = await new PlanningApi(client).getTodaySettlement();
      const statusLabels = {
        ready: "未开始",
        in_progress: "进行中",
        complete: "已闭环",
        abandoned: "主动结束",
      } as const;
      const tasks = data.tasks.map((task) => ({
        ...task,
        trackLabel: trackLabel[task.track],
        statusLabel: statusLabels[task.status],
        statusTone:
          task.status === "complete"
            ? "complete"
            : task.status === "abandoned"
              ? "abandoned"
              : task.status === "in_progress"
                ? "active"
                : "open",
        elapsedLabel: durationLabel(task.elapsedMinutes),
        plannedLabel: durationLabel(task.plannedMinutes),
      }));
      const openTasks = tasks.filter(
        (task) => task.status === "ready" || task.status === "in_progress",
      );
      this.setData({
        status: "ready",
        settlement: data,
        riskLabel: data.newRisks.join("；"),
        elapsedLabel: durationLabel(data.elapsedMinutes),
        scheduledLabel: durationLabel(data.scheduledMinutes),
        completionLabel: `${String(data.completedTaskCount)} / ${String(data.tasks.length)} 项`,
        varianceLabel: varianceLabel(data.elapsedMinutes, data.scheduledMinutes),
        hasSummary: data.summary.trim().length > 0,
        primaryOpenTask: openTasks[0] ?? null,
        additionalOpenTaskCount: Math.max(0, openTasks.length - 1),
        tasks,
      });
    } catch (error) {
      const view = errorView(error, "每日结算加载失败，请重试。");
      this.setData({ status: "error", errorMessage: view.message, requestId: view.requestId });
    }
  },
  retry() {
    void this.loadSettlement();
  },
  exit() {
    navigateBackOr(routes.today);
  },
  returnToday() {
    void wx.switchTab({ url: routes.today });
  },
  openWeeklyPlan() {
    void wx.navigateTo({ url: routes.weeklyPlan });
  },
  askAnan() {
    const auth = currentAuthSession();
    if (!auth) {
      void wx.reLaunch({ url: routes.login });
      return;
    }
    const settlement = this.data.settlement;
    if (!settlement) return;
    storeCompanionLaunchContext(auth.userId, {
      sourceLabel: "今日学习结算",
      title: `${settlement.localDate} · ${this.data.completionLabel}`,
      summary: settlement.summary,
      suggestedQuestion: "结合今天的完成记录和新风险，帮我判断明天第一件最应该做的事，并说明依据。",
      routeId: "plans/daily-settlement/index",
      workspace: "today",
      objectRefs: [],
    });
    void wx.switchTab({ url: routes.anan });
  },
});

function durationLabel(minutes: number): string {
  const value = Math.max(0, minutes);
  if (value === 0) return "0 小时";
  if (value < 60) return `${String(value)} 分钟`;
  const hours = value / 60;
  return `${Number.isInteger(hours) ? String(hours) : hours.toFixed(1)} 小时`;
}

function varianceLabel(elapsedMinutes: number, scheduledMinutes: number): string {
  const difference = elapsedMinutes - scheduledMinutes;
  if (difference === 0) return "与计划一致";
  const label = durationLabel(Math.abs(difference));
  return difference > 0 ? `比计划多 ${label}` : `比计划少 ${label}`;
}
