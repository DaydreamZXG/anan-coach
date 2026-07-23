import { fetchAbilityEstimate } from "../../api/endpoints";
import { navigateSmart } from "../../utils/route-map";
import type { AbilityEstimate } from "../../api/generated/types.gen";

const CONFIDENCE_LABEL: Record<string, string> = {
  unavailable: "数据不足",
  low: "低",
  medium: "中",
  high: "高",
};

const SOURCE_LABEL: Record<string, string> = {
  practice: "日常练习",
  mock_exam: "模考",
};

const DEMO: AbilityEstimate = {
  confidence: "medium",
  statusLabel: "估分渐稳",
  dataLine: "行测约 68 分 · 再做一次限时模考可把区间收窄到 ±3",
  xingceScore: 68,
  latestMockReason: "上次模考在 6 天前，样本开始变旧",
  sources: {} as AbilityEstimate["sources"],
  moduleEstimates: [
    { moduleName: "言语理解", accuracy: 0.62, sampleSize: 48, source: "practice" },
    { moduleName: "判断推理", accuracy: 0.71, sampleSize: 62, source: "practice" },
    { moduleName: "资料分析", accuracy: 0.83, sampleSize: 40, source: "mock_exam" },
    { moduleName: "数量关系", accuracy: 0.45, sampleSize: 12, source: "practice" },
    { moduleName: "常识判断", accuracy: 0.58, sampleSize: 20, source: "practice" },
  ],
};

/** 报告下钻入口：错因/掌握度/结算共用一套卡片 */
const REPORTS = [
  { key: "error", title: "错因分析", subtitle: "看看你常栽在哪类题", url: "/pages/error-pattern/index" },
  { key: "mastery", title: "掌握度追踪", subtitle: "哪些知识点已经稳了", url: "/pages/mastery/index" },
  { key: "mock", title: "模考报告", subtitle: "历次模考的分数走势", url: "/pages/mock-report/index" },
];

Page({
  data: {
    loading: true,
    usingDemo: false,
    est: null as AbilityEstimate | null,
    confidenceLabel: "",
    modulesView: [] as Array<{
      moduleName: string;
      accuracyPct: number;
      bandLeft: number;
      bandWidth: number;
      sampleSize: number;
      sourceLabel: string;
      shaky: boolean;
    }>,
    reports: REPORTS,
  },

  onShow() {
    if (typeof this.getTabBar === "function") {
      const tabBar = this.getTabBar();
      if (tabBar) tabBar.setData({ active: "ability" });
    }
    this.load();
  },

  async load() {
    this.setData({ loading: true });
    try {
      const est = await fetchAbilityEstimate();
      this.render(est, false);
    } catch (err) {
      console.error("[ability] estimate failed, fallback to demo", err);
      this.render(DEMO, true);
    }
  },

  render(est: AbilityEstimate, usingDemo: boolean) {
    const modulesView = (est.moduleEstimates || []).map((m) => {
      const accuracyPct = Math.round(m.accuracy * 100);
      // 用样本量反推不确定半宽：样本越少，区间越宽（诚实表达）
      const halfWidth = Math.min(22, Math.round(60 / Math.sqrt(Math.max(1, m.sampleSize))));
      const bandLeft = Math.max(0, accuracyPct - halfWidth);
      const bandRight = Math.min(100, accuracyPct + halfWidth);
      return {
        moduleName: m.moduleName,
        accuracyPct,
        bandLeft,
        bandWidth: bandRight - bandLeft,
        sampleSize: m.sampleSize,
        sourceLabel: SOURCE_LABEL[m.source] || m.source,
        shaky: m.sampleSize < 20,
      };
    });
    this.setData({
      loading: false,
      usingDemo,
      est,
      confidenceLabel: CONFIDENCE_LABEL[est.confidence] || est.confidence,
      modulesView,
    });
  },

  onGoMock() {
    wx.navigateTo({
      url: "/pages/mock-exam/index",
      fail: () => wx.showToast({ title: "模考开发中", icon: "none" }),
    });
  },

  onGoCommunity() {
    wx.navigateTo({
      url: "/pages/method-cards/index",
      fail: () => wx.showToast({ title: "知识卡广场开发中", icon: "none" }),
    });
  },

  onOpenReport(e: WechatMiniprogram.TouchEvent) {
    const { url } = e.currentTarget.dataset as { url: string };
    navigateSmart(url);
  },
});
