import { PlanningApi } from "../../api/planning";
import type { PlanChangeHistory } from "../../api/generated/types.gen";
import {
  dateTimeLabel,
  errorView,
  requireAuthenticatedClient,
} from "../../features/learning-brain/presentation";

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    items: [] as Array<
      PlanChangeHistory["items"][number] & {
        generatedLabel: string;
        reasonLabel: string;
        changedInputLabel: string;
      }
    >,
  },
  onLoad() {
    void this.loadHistory();
  },
  async loadHistory() {
    const client = requireAuthenticatedClient();
    if (!client) return;
    try {
      const { data } = await new PlanningApi(client).getHistory();
      this.setData({
        status: "ready",
        items: data.items.map((item) => ({
          ...item,
          generatedLabel: dateTimeLabel(item.generatedAt),
          reasonLabel: item.replanReasonCodes.join("、") || "首次生成",
          changedInputLabel: item.changedInputKeys.join("、") || "初始化",
        })),
      });
    } catch (error) {
      const view = errorView(error, "计划变更记录加载失败。");
      this.setData({ status: "error", errorMessage: view.message, requestId: view.requestId });
    }
  },
  retry() {
    void this.loadHistory();
  },
});
