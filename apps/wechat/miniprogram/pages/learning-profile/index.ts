import { LearningBrainApi } from "../../api/learning-brain";
import type { LearningBrainProfileVersion } from "../../api/generated/types.gen";
import { routes } from "../../app/navigation/routes";
import {
  errorView,
  percent,
  requireAuthenticatedClient,
} from "../../features/learning-brain/presentation";

type ModuleView = LearningBrainProfileVersion["modules"][number] & {
  metricLabel: string;
  readinessLabel: string;
};
type ScoreRange = { lower: number; upper: number };

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    profile: null as LearningBrainProfileVersion | null,
    totalScoreLabel: "证据不足",
    xingceScoreLabel: "证据不足",
    essayScoreLabel: "证据不足",
    fingerprintLabel: "",
    evidenceCountLabel: "0",
    executionCountLabel: "0",
    modules: [] as ModuleView[],
  },

  onShow() {
    void this.loadProfile();
  },
  async loadProfile() {
    const client = requireAuthenticatedClient();
    if (!client) return;
    try {
      const { data } = await new LearningBrainApi(client).getProfile();
      const estimate = data.ability.estimate;
      const score = (range: ScoreRange | null | undefined): string =>
        range ? `${range.lower.toFixed(1)}–${range.upper.toFixed(1)}` : "证据不足";
      const readinessLabels = {
        unavailable: "暂无证据",
        developing: "建立中",
        reliable: "有验证",
        stale: "需更新",
        conflicted: "有冲突",
      } as const;
      this.setData({
        status: "ready",
        profile: data,
        totalScoreLabel: score(estimate?.total),
        xingceScoreLabel: score(estimate?.xingce),
        essayScoreLabel: score(estimate?.shenlun),
        fingerprintLabel: `${data.inputFingerprint.slice(0, 10)}…`,
        evidenceCountLabel: String(
          data.evidenceSummary.scoreEvidenceCount +
            data.evidenceSummary.xingceAttemptCount +
            data.evidenceSummary.xingceMasteryCount +
            data.evidenceSummary.essayMasteryCount +
            data.evidenceSummary.taskExecutionCount,
        ),
        executionCountLabel: String(data.evidenceSummary.taskExecutionCount),
        modules: data.modules.map((module) => ({
          ...module,
          metricLabel:
            module.track === "xingce"
              ? `正确率 ${percent(module.correctRate)}`
              : `${String(module.evidenceCount)} 条迁移/掌握证据`,
          readinessLabel: readinessLabels[module.readiness],
        })),
      });
    } catch (error) {
      const view = errorView(error, "学习档案加载失败，请重试。");
      this.setData({ status: "error", errorMessage: view.message, requestId: view.requestId });
    }
  },

  openModule(event: WechatMiniprogram.BaseEvent) {
    const track = String(event.currentTarget.dataset.track ?? "");
    const moduleKey = String(event.currentTarget.dataset.key ?? "");
    if (!track || !moduleKey) return;
    void wx.navigateTo({
      url: `${routes.abilityModule}?track=${track}&moduleKey=${encodeURIComponent(moduleKey)}`,
    });
  },
  openPattern(event: WechatMiniprogram.BaseEvent) {
    const patternId = String(event.currentTarget.dataset.id ?? "");
    if (patternId) void wx.navigateTo({ url: `${routes.errorPattern}?patternId=${patternId}` });
  },
  openCompanion() {
    void wx.navigateTo({ url: routes.companion });
  },
  retry() {
    void this.loadProfile();
  },
});
