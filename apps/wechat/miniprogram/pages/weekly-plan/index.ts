import { PlanningApi } from "../../api/planning";
import type { WeeklyPlanVersion } from "../../api/generated/types.gen";
import { errorView, requireAuthenticatedClient } from "../../features/learning-brain/presentation";

const weekdayLabels = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const modeLabels = { training: "针对训练", mock: "整卷模考", recovery: "恢复复盘" } as const;

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    week: null as WeeklyPlanVersion | null,
    reasonLabel: "",
    days: [] as Array<
      WeeklyPlanVersion["days"][number] & { weekdayLabel: string; modeLabel: string }
    >,
  },
  onLoad() {
    void this.loadWeek();
  },
  async loadWeek() {
    const client = requireAuthenticatedClient();
    if (!client) return;
    try {
      const { data } = await new PlanningApi(client).getWeek();
      this.setData({
        status: "ready",
        week: data,
        reasonLabel: data.replanReasonCodes.join("、") || "本周节奏初始化",
        days: data.days.map((day) => ({
          ...day,
          weekdayLabel: weekdayLabels[day.weekday] ?? "",
          modeLabel: modeLabels[day.mode],
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
});
