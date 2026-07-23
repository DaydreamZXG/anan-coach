import { authedV2 } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import type { PlanBasisTask, PlanBasisView } from "../../api/generated/types.gen";

type ViewState = "loading" | "ready" | "error";

interface BasisTaskView extends PlanBasisTask {
  trackLabel: string;
  priorityLabel: string;
  evidenceLabel: string;
}

interface PolicyMetric {
  label: string;
  value: string;
}

const TRACK_LABELS = { xingce: "行测", essay: "申论", recovery: "恢复", mock: "模考" } as const;

/** 计划依据：GET /api/v2/plans/today/basis，解释今日计划怎么算出来 */
Page({
  data: {
    statusBarHeight: 20,
    viewState: "loading" as ViewState,
    errorDesc: "",
    requestId: "",
    versionNo: 0,
    headline: "",
    rationale: "",
    algorithmVersion: "",
    conflictLabel: "",
    tasks: [] as BasisTaskView[],
    policyMetrics: [] as PolicyMetric[],
    invalidationConditions: [] as string[],
    hasConflicts: false,
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    void this.loadBasis();
  },

  async loadBasis() {
    this.setData({ viewState: "loading", errorDesc: "", requestId: "" });
    try {
      const data = await authedV2<PlanBasisView>({
        method: "GET",
        path: "/api/v2/plans/today/basis",
      });
      this.setData({
        viewState: "ready",
        versionNo: data.versionNo,
        headline: data.headline,
        rationale: data.rationale,
        algorithmVersion: data.policy.algorithmVersion,
        conflictLabel: data.conflicts.join("；"),
        hasConflicts: data.conflicts.length > 0,
        tasks: data.tasks.map((task) => ({
          ...task,
          trackLabel: TRACK_LABELS[task.track],
          priorityLabel: task.priorityScore.toFixed(3),
          evidenceLabel: `${String(task.evidenceRefs.length)} 条版本化依据`,
        })),
        policyMetrics: [
          { label: "岗位收益", value: `${String(Math.round(data.policy.goalGainWeight * 100))}%` },
          {
            label: "能力收益",
            value: `${String(Math.round(data.policy.abilityGainWeight * 100))}%`,
          },
          {
            label: "遗忘紧迫",
            value: `${String(Math.round(data.policy.verificationUrgencyWeight * 100))}%`,
          },
          { label: "时间适配", value: `${String(Math.round(data.policy.timeFitWeight * 100))}%` },
          {
            label: "负荷扣减",
            value: `-${String(Math.round(data.policy.loadPenaltyWeight * 100))}%`,
          },
          {
            label: "轨道平衡",
            value: `+${String(Math.round(data.policy.trackBalanceMaxBonus * 100))}%`,
          },
        ],
        invalidationConditions: data.invalidationConditions,
      });
    } catch (err) {
      const apiErr = err as ApiError;
      this.setData({
        viewState: "error",
        errorDesc: "计划依据加载失败，请重试。",
        requestId: apiErr.requestId ?? "",
      });
    }
  },

  onRetry() {
    void this.loadBasis();
  },

  onBack() {
    wx.navigateBack({
      fail: () => wx.switchTab({ url: "/pages/today/index" }),
    });
  },
});
