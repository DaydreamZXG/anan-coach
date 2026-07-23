import { authedV2 } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import type { WeeklyPlanDay, WeeklyPlanVersion } from "../../api/generated/types.gen";

type ViewState = "loading" | "ready" | "error";

interface DayView extends WeeklyPlanDay {
  weekdayLabel: string;
  modeLabel: string;
}

const WEEKDAY_LABELS = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const MODE_LABELS = { training: "针对训练", mock: "整卷模考", recovery: "恢复复盘" } as const;

/** 周计划：GET /api/v2/plans/week，展示 5+1+1 节奏与每日概要 */
Page({
  data: {
    statusBarHeight: 20,
    viewState: "loading" as ViewState,
    errorDesc: "",
    requestId: "",
    versionNo: 0,
    rhythm: "",
    weekStart: "",
    reasonLabel: "",
    days: [] as DayView[],
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    void this.loadWeek();
  },

  async loadWeek() {
    this.setData({ viewState: "loading", errorDesc: "", requestId: "" });
    try {
      const data = await authedV2<WeeklyPlanVersion>({ method: "GET", path: "/api/v2/plans/week" });
      this.setData({
        viewState: "ready",
        versionNo: data.versionNo,
        rhythm: data.rhythm,
        weekStart: data.weekStart,
        reasonLabel: data.replanReasonCodes.join("、") || "本周节奏初始化",
        days: data.days.map((day) => ({
          ...day,
          weekdayLabel: WEEKDAY_LABELS[day.weekday] ?? "",
          modeLabel: MODE_LABELS[day.mode],
        })),
      });
    } catch (err) {
      const apiErr = err as ApiError;
      this.setData({
        viewState: "error",
        errorDesc: "周计划加载失败，请重试。",
        requestId: apiErr.requestId ?? "",
      });
    }
  },

  onRetry() {
    void this.loadWeek();
  },

  onBack() {
    wx.navigateBack({
      fail: () => wx.switchTab({ url: "/pages/today/index" }),
    });
  },
});
