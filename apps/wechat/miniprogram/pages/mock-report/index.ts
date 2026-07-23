import { fetchMockExamReview, markMockReviewViewed } from "../../api/endpoints";
import type { MockExamReview } from "../../api/generated/types.gen";

type ModuleVM = {
  moduleName: string;
  correct: number;
  total: number;
  accuracyPct: number;
  changeText: string;
  changeClass: string;
};

type ClusterVM = {
  nodeId: string;
  displayPath: string;
  wrongCount: number;
  fixAction: string;
};

const DEMO: {
  score: number;
  rangeText: string;
  modules: ModuleVM[];
  clusters: ClusterVM[];
  nextWeekReason: string;
} = {
  score: 68,
  rangeText: "波动区间 64 ~ 72",
  modules: [
    { moduleName: "言语理解", correct: 15, total: 20, accuracyPct: 75, changeText: "+5%", changeClass: "is-up" },
    { moduleName: "判断推理", correct: 14, total: 20, accuracyPct: 70, changeText: "持平", changeClass: "" },
    { moduleName: "资料分析", correct: 10, total: 15, accuracyPct: 67, changeText: "-3%", changeClass: "is-down" },
    { moduleName: "数量关系", correct: 4, total: 10, accuracyPct: 40, changeText: "+2%", changeClass: "is-up" },
    { moduleName: "常识判断", correct: 9, total: 15, accuracyPct: 60, changeText: "持平", changeClass: "" },
  ],
  clusters: [
    {
      nodeId: "demo-n1",
      displayPath: "资料分析 · 增长率 · 基期计算",
      wrongCount: 3,
      fixAction: "先补基期量公式卡，再做 10 题定向练",
    },
    {
      nodeId: "demo-n2",
      displayPath: "判断推理 · 翻译推理 · 逆否等价",
      wrongCount: 2,
      fixAction: "重做上周错过的两张复盘卡",
    },
  ],
  nextWeekReason: "下周主攻资料分析基期计算：这周 3 题全栽在同一个知识点上，把它拔掉最划算。",
};

Page({
  data: {
    loading: true,
    usingDemo: false,
    score: 0,
    metricName: "综合表现指数",
    rangeText: "",
    modules: [] as ModuleVM[],
    clusters: [] as ClusterVM[],
    nextWeekReason: "",
    submittedAt: "",
  },

  onLoad(query: Record<string, string>) {
    const sessionId = query.sessionId || "";
    void this.load(sessionId);
  },

  async load(sessionId: string) {
    if (!sessionId) {
      this.applyDemo();
      return;
    }
    try {
      const review = await fetchMockExamReview(sessionId);
      this.applyReview(review);
      if (!review.viewed) {
        markMockReviewViewed(sessionId).catch(() => undefined);
      }
    } catch (err) {
      console.warn("[mock-report] fallback to demo", err);
      this.applyDemo();
    }
  },

  applyDemo() {
    this.setData({
      loading: false,
      usingDemo: true,
      score: DEMO.score,
      rangeText: DEMO.rangeText,
      modules: DEMO.modules,
      clusters: DEMO.clusters,
      nextWeekReason: DEMO.nextWeekReason,
    });
  },

  applyReview(review: MockExamReview) {
    const modules: ModuleVM[] = review.modules.map((m) => {
      const delta = m.changeFromPrevious;
      let changeText = "持平";
      let changeClass = "";
      if (typeof delta === "number" && delta !== 0) {
        const pct = Math.round(delta * 100);
        changeText = `${pct > 0 ? "+" : ""}${pct}%`;
        changeClass = pct > 0 ? "is-up" : "is-down";
      }
      return {
        moduleName: m.moduleName,
        correct: m.correct,
        total: m.total,
        accuracyPct: Math.round(m.accuracy * 100),
        changeText,
        changeClass,
      };
    });

    this.setData({
      loading: false,
      score: Math.round(review.estimatedScore),
      metricName: review.metricName === "balanced_performance_index" ? "均衡表现指数" : "题量加权指数",
      rangeText: `波动区间 ${Math.round(review.performanceRange.lower)} ~ ${Math.round(review.performanceRange.upper)}`,
      modules,
      clusters: review.wrongClusters.map((c) => ({
        nodeId: c.nodeId,
        displayPath: c.displayPath,
        wrongCount: c.wrongCount,
        fixAction: c.fixAction,
      })),
      nextWeekReason: review.nextWeekReason,
      submittedAt: review.submittedAt.slice(0, 10),
    });
  },

  onBackAbility() {
    wx.switchTab({ url: "/pages/ability/index" });
  },

  onBackToday() {
    wx.switchTab({ url: "/pages/today/index" });
  },
});
