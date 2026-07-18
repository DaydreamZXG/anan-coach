import { PlanningApi } from "../../api/planning";
import type { PlanBasisView } from "../../api/generated/types.gen";
import {
  errorView,
  requireAuthenticatedClient,
  trackLabel,
} from "../../features/learning-brain/presentation";

type BasisTaskView = PlanBasisView["tasks"][number] & {
  trackLabel: string;
  priorityLabel: string;
  evidenceLabel: string;
};

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    basis: null as PlanBasisView | null,
    conflictLabel: "",
    tasks: [] as BasisTaskView[],
    policyMetrics: [] as Array<{ label: string; value: string }>,
  },

  onLoad() {
    void this.loadBasis();
  },

  async loadBasis() {
    const client = requireAuthenticatedClient();
    if (!client) return;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const { data } = await new PlanningApi(client).getTodayBasis();
      this.setData({
        status: "ready",
        basis: data,
        conflictLabel: data.conflicts.join("；"),
        tasks: data.tasks.map((task) => ({
          ...task,
          trackLabel: trackLabel[task.track],
          priorityLabel: task.priorityScore.toFixed(3),
          evidenceLabel: `${String(task.evidenceRefs.length)} 条版本化依据`,
        })),
        policyMetrics: [
          {
            label: "岗位收益",
            value: `${String(Math.round(data.policy.goalGainWeight * 100))}%`,
          },
          {
            label: "能力收益",
            value: `${String(Math.round(data.policy.abilityGainWeight * 100))}%`,
          },
          {
            label: "遗忘紧迫",
            value: `${String(Math.round(data.policy.verificationUrgencyWeight * 100))}%`,
          },
          {
            label: "时间适配",
            value: `${String(Math.round(data.policy.timeFitWeight * 100))}%`,
          },
          {
            label: "负荷扣减",
            value: `-${String(Math.round(data.policy.loadPenaltyWeight * 100))}%`,
          },
          {
            label: "轨道平衡",
            value: `+${String(Math.round(data.policy.trackBalanceMaxBonus * 100))}%`,
          },
        ],
      });
    } catch (error) {
      const view = errorView(error, "计划依据加载失败，请重试。");
      this.setData({ status: "error", errorMessage: view.message, requestId: view.requestId });
    }
  },

  retry() {
    void this.loadBasis();
  },
});
