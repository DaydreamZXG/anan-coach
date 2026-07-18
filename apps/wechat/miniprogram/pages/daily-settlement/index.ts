import { PlanningApi } from "../../api/planning";
import type { DailySettlementView } from "../../api/generated/types.gen";
import {
  errorView,
  requireAuthenticatedClient,
  trackLabel,
} from "../../features/learning-brain/presentation";

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    settlement: null as DailySettlementView | null,
    riskLabel: "",
    tasks: [] as Array<
      DailySettlementView["tasks"][number] & { trackLabel: string; statusLabel: string }
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
      this.setData({
        status: "ready",
        settlement: data,
        riskLabel: data.newRisks.join("；"),
        tasks: data.tasks.map((task) => ({
          ...task,
          trackLabel: trackLabel[task.track],
          statusLabel: statusLabels[task.status],
        })),
      });
    } catch (error) {
      const view = errorView(error, "每日结算加载失败，请重试。");
      this.setData({ status: "error", errorMessage: view.message, requestId: view.requestId });
    }
  },
  retry() {
    void this.loadSettlement();
  },
});
