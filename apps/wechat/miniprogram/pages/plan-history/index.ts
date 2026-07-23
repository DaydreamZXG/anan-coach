import { authedV2 } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import type { PlanChangeEntry, PlanChangeHistory } from "../../api/generated/types.gen";

type ViewState = "loading" | "ready" | "error";

interface HistoryItemView extends PlanChangeEntry {
  generatedLabel: string;
  reasonLabel: string;
  changedInputLabel: string;
  dayModeLabel: string;
}

const DAY_MODE_LABELS = {
  normal: "常规日",
  mock: "模考日",
  recovery: "恢复日",
  unavailable: "待定",
} as const;

/** 生成时间格式化为「MM.DD HH:mm」 */
function dateTimeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  const dd = String(date.getDate()).padStart(2, "0");
  const hh = String(date.getHours()).padStart(2, "0");
  const mi = String(date.getMinutes()).padStart(2, "0");
  return `${mm}.${dd} ${hh}:${mi}`;
}

/** 计划历史：GET /api/v2/plans/history，每次计划变更留痕 */
Page({
  data: {
    statusBarHeight: 20,
    viewState: "loading" as ViewState,
    errorDesc: "",
    requestId: "",
    items: [] as HistoryItemView[],
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    void this.loadHistory();
  },

  async loadHistory() {
    this.setData({ viewState: "loading", errorDesc: "", requestId: "" });
    try {
      const data = await authedV2<PlanChangeHistory>({
        method: "GET",
        path: "/api/v2/plans/history",
      });
      this.setData({
        viewState: "ready",
        items: data.items.map((item) => ({
          ...item,
          generatedLabel: dateTimeLabel(item.generatedAt),
          reasonLabel: item.replanReasonCodes.join("、") || "首次生成",
          changedInputLabel: item.changedInputKeys.join("、") || "初始化",
          dayModeLabel: DAY_MODE_LABELS[item.dayMode],
        })),
      });
    } catch (err) {
      const apiErr = err as ApiError;
      this.setData({
        viewState: "error",
        errorDesc: "计划变更记录加载失败。",
        requestId: apiErr.requestId ?? "",
      });
    }
  },

  onRetry() {
    void this.loadHistory();
  },

  onBack() {
    wx.navigateBack({
      fail: () => wx.switchTab({ url: "/pages/today/index" }),
    });
  },
});
