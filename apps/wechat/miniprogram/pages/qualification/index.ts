import { fetchQualificationGaps } from "../../api/endpoints";

type GapVM = {
  factKey: string;
  factLabel: string;
  typeLabel: string;
  actionLabel: string;
  affectedLine: string;
};

const FACT_LABEL: Record<string, string> = {
  education: "学历",
  degree: "学位",
  major: "专业",
  fresh_graduate: "应届身份",
  political_status: "政治面貌",
  work_years: "工作年限",
  grassroots_years: "基层年限",
  service_project: "服务项目经历",
  hukou: "户籍",
  age: "年龄",
  certifications: "证书",
  other_conditions: "其他条件",
};

const GAP_TYPE: Record<string, string> = {
  missing_fact: "还没填",
  conflicting_fact: "信息冲突",
  position_confirmation: "需人工确认",
};

Page({
  data: {
    loading: true,
    usingDemo: false,
    complete: false,
    examYear: 0,
    totalAffected: 0,
    gaps: [] as GapVM[],
  },

  onLoad() {
    void this.load();
  },

  async load() {
    try {
      const sum = await fetchQualificationGaps();
      this.setData({
        loading: false,
        complete: sum.complete,
        examYear: sum.examYear,
        totalAffected: sum.totalAffectedPositions,
        gaps: sum.gaps.map((g) => ({
          factKey: g.factKey,
          factLabel: FACT_LABEL[g.factKey] ?? g.factKey,
          typeLabel: GAP_TYPE[g.gapType] ?? g.gapType,
          actionLabel: g.action === "complete_profile" ? "去补资料" : "去核对岗位",
          affectedLine: `影响 ${g.affectedPositionCount} 个候选岗位`,
        })),
      });
    } catch (err) {
      console.warn("[qualification] fallback to demo", err);
      this.setData({
        loading: false,
        usingDemo: true,
        complete: false,
        examYear: 2026,
        totalAffected: 12,
        gaps: [
          { factKey: "political_status", factLabel: "政治面貌", typeLabel: "还没填", actionLabel: "去补资料", affectedLine: "影响 8 个候选岗位" },
          { factKey: "grassroots_years", factLabel: "基层年限", typeLabel: "还没填", actionLabel: "去补资料", affectedLine: "影响 4 个候选岗位" },
        ],
      });
    }
  },

  onAction(e: WechatMiniprogram.TouchEvent) {
    const { action } = e.currentTarget.dataset as { action: string };
    if (action === "去核对岗位") {
      wx.navigateTo({ url: "/pages/goal/index" });
      return;
    }
    // 资料表单在引导流里，重进引导补齐
    wx.navigateTo({ url: "/pages/onboarding/index" });
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) });
  },
});
