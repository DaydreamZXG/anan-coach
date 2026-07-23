import { authedV2 } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import type { DailySettlementView, SettlementTask } from "../../api/generated/types.gen";

type ViewState = "loading" | "ready" | "error";

interface SettlementTaskView extends SettlementTask {
  trackLabel: string;
  statusLabel: string;
}

const TRACK_LABELS = { xingce: "行测", essay: "申论", recovery: "恢复", mock: "模考" } as const;
const TASK_STATUS_LABELS = {
  ready: "未开始",
  in_progress: "进行中",
  complete: "已闭环",
  abandoned: "主动结束",
} as const;

/** 每日结算：GET /api/v2/plans/today/settlement，一天结束时的总结算视图 */
Page({
  data: {
    statusBarHeight: 20,
    viewState: "loading" as ViewState,
    errorDesc: "",
    requestId: "",
    elapsedMinutes: 0,
    scheduledMinutes: 0,
    completedTaskCount: 0,
    carryOverTaskCount: 0,
    summary: "",
    riskLabel: "",
    hasRisks: false,
    tasks: [] as SettlementTaskView[],
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    void this.loadSettlement();
  },

  async loadSettlement() {
    this.setData({ viewState: "loading", errorDesc: "", requestId: "" });
    try {
      const data = await authedV2<DailySettlementView>({
        method: "GET",
        path: "/api/v2/plans/today/settlement",
      });
      this.setData({
        viewState: "ready",
        elapsedMinutes: data.elapsedMinutes,
        scheduledMinutes: data.scheduledMinutes,
        completedTaskCount: data.completedTaskCount,
        carryOverTaskCount: data.carryOverTaskCount ?? 0,
        summary: data.summary,
        riskLabel: data.newRisks.join("；"),
        hasRisks: data.newRisks.length > 0,
        tasks: data.tasks.map((task) => ({
          ...task,
          trackLabel: TRACK_LABELS[task.track],
          statusLabel: TASK_STATUS_LABELS[task.status],
        })),
      });
    } catch (err) {
      const apiErr = err as ApiError;
      this.setData({
        viewState: "error",
        errorDesc: "每日结算加载失败，请重试。",
        requestId: apiErr.requestId ?? "",
      });
    }
  },

  onRetry() {
    void this.loadSettlement();
  },

  onBack() {
    wx.navigateBack({
      fail: () => wx.switchTab({ url: "/pages/today/index" }),
    });
  },
});
