import { enrollRetakeProgram, fetchRetakeWorkspace } from "../../api/endpoints";
import type { RetakeProgramView, RetakeWorkspace } from "../../api/generated/types.gen";

type EvidenceVM = {
  evidenceId: string;
  yearLabel: string;
  sourceLabel: string;
  scoreLine: string;
};

type ProgramVM = {
  sourceExamYear: number;
  targetExamYear: number;
  xingceScore: string;
  essayScore: string;
  limitation: string;
  trainingFocus: string[];
  stateLabel: string;
};

const DEMO_EVIDENCE: EvidenceVM[] = [
  {
    evidenceId: "demo-e1",
    yearLabel: "2025 年国考",
    sourceLabel: "自报成绩",
    scoreLine: "行测 62.3 · 申论 58.0",
  },
  {
    evidenceId: "demo-e2",
    yearLabel: "2024 年省考",
    sourceLabel: "自报成绩",
    scoreLine: "行测 58.5 · 申论未填",
  },
];

const DEMO_PROGRAM: ProgramVM = {
  sourceExamYear: 2025,
  targetExamYear: 2026,
  xingceScore: "62.3",
  essayScore: "58.0",
  limitation: "行测资料分析和数量拖了后腿，申论大作文论证单薄",
  trainingFocus: ["资料分析提速到 25 分钟内", "数量关系只抢基础 6 题", "申论大作文每周一篇精改"],
  stateLabel: "进行中",
};

function toEvidenceVM(ws: RetakeWorkspace): EvidenceVM[] {
  return ws.eligibleScoreEvidence.map((e) => {
    const parts: string[] = [];
    if (typeof e.xingceScore === "number") parts.push(`行测 ${e.xingceScore}`);
    if (typeof e.essayScore === "number") parts.push(`申论 ${e.essayScore}`);
    if (parts.length === 0 && typeof e.totalScore === "number") parts.push(`总分 ${e.totalScore}`);
    return {
      evidenceId: e.evidenceId,
      yearLabel: `${e.examYear} 年考试`,
      sourceLabel: e.sourceType === "official_exam" ? "官方成绩" : "自报成绩",
      scoreLine: parts.length ? parts.join(" · ") : "分数未填",
    };
  });
}

function toProgramVM(p: RetakeProgramView): ProgramVM {
  const ls = p.lossStructure;
  return {
    sourceExamYear: p.sourceExamYear,
    targetExamYear: p.targetExamYear,
    xingceScore: typeof ls.xingceScore === "number" ? String(ls.xingceScore) : "—",
    essayScore: typeof ls.essayScore === "number" ? String(ls.essayScore) : "—",
    limitation: ls.limitation,
    trainingFocus: ls.trainingFocus,
    stateLabel: p.state === "active" ? "进行中" : p.state === "completed" ? "已完成" : "已退出",
  };
}

Page({
  data: {
    loading: true,
    usingDemo: false,
    scoreUseBoundary: "",
    targetExamYear: 0,
    program: null as ProgramVM | null,
    evidence: [] as EvidenceVM[],
    selectedEvidenceId: "",
    enrolling: false,
  },

  onLoad() {
    void this.load();
  },

  async load() {
    try {
      const ws = await fetchRetakeWorkspace();
      this.setData({
        loading: false,
        scoreUseBoundary: ws.scoreUseBoundary,
        targetExamYear: ws.targetExamYear,
        program: ws.program ? toProgramVM(ws.program) : null,
        evidence: toEvidenceVM(ws),
      });
    } catch (err) {
      console.warn("[retake] workspace fallback to demo", err);
      this.setData({
        loading: false,
        usingDemo: true,
        scoreUseBoundary: "上一次的分数只用来定训练重点，不做任何奖惩",
        targetExamYear: 2026,
        program: null,
        evidence: DEMO_EVIDENCE,
      });
    }
  },

  onPickEvidence(e: WechatMiniprogram.TouchEvent) {
    const { id } = e.currentTarget.dataset as { id: string };
    this.setData({ selectedEvidenceId: id });
  },

  async onEnroll() {
    const id = this.data.selectedEvidenceId;
    if (!id || this.data.enrolling) return;
    this.setData({ enrolling: true });

    if (this.data.usingDemo) {
      setTimeout(() => {
        this.setData({ program: DEMO_PROGRAM, enrolling: false });
        wx.showToast({ title: "恢复计划已生成", icon: "none" });
      }, 800);
      return;
    }

    try {
      const res = await enrollRetakeProgram(
        { sourceScoreEvidenceId: id },
        `retake-enroll-${id}`,
      );
      this.setData({ program: toProgramVM(res.program), enrolling: false });
      wx.showToast({ title: "恢复计划已生成", icon: "none" });
    } catch (err) {
      console.error("[retake] enroll failed", err);
      this.setData({ enrolling: false });
      wx.showToast({ title: "没生成成功，再试一次", icon: "none" });
    }
  },

  onGoToday() {
    wx.switchTab({ url: "/pages/today/index" });
  },

  onGoImport() {
    wx.navigateTo({
      url: "/pages/score-import/index",
      fail: () => wx.showToast({ title: "跳转失败，再点一次", icon: "none" }),
    });
  },

  onShow() {
    // 从成绩导入页返回时刷新凭证列表
    if (!this.data.loading) void this.load();
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/today/index" }) });
  },
});
