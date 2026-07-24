import { PlanningApi } from "../../api/planning";
import type { PlanChangeHistory } from "../../api/generated/types.gen";
import {
  dateTimeLabel,
  errorView,
  requireAuthenticatedClient,
} from "../../features/learning-brain/presentation";
import {
  changedInputLabel,
  durationLabel,
  planningDayModeLabel,
  planningReasonLabel,
} from "../../features/planning/presentation";

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
        dayModeLabel: string;
        editionLabel: string;
        scheduledDurationLabel: string;
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
          reasonLabel: planningReasonLabel(item.replanReasonCodes, "首次生成学习安排"),
          changedInputLabel: changedInputLabel(item.changedInputKeys),
          dayModeLabel: planningDayModeLabel(item.dayMode),
          editionLabel: item.versionNo > 1 ? `第 ${String(item.versionNo)} 版计划` : "首次计划",
          scheduledDurationLabel: durationLabel(item.scheduledMinutes),
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
