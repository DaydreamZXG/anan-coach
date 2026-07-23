import { fetchScoreEvidenceHistory, reportScoreEvidence } from "../../api/endpoints";

type HistoryVM = {
  evidenceId: string;
  yearLabel: string;
  sourceLabel: string;
  scoreLine: string;
  active: boolean;
};

const CONTEXTS = [
  { key: "official_exam", label: "正式考试", desc: "国考、省考等真实成绩" },
  { key: "full_mock", label: "完整模考", desc: "机构或平台的全真模考" },
  { key: "historical_estimate", label: "大概记得", desc: "记不清了，凭印象填" },
] as const;

const SOURCE_LABEL: Record<string, string> = {
  self_report: "自报",
  placement_test: "摸底",
  controlled_mock: "站内模考",
  external_mock: "外部模考",
  official_exam: "官方成绩",
};

function today(): string {
  const d = new Date();
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

Page({
  data: {
    contexts: CONTEXTS.map((c) => ({ key: c.key, label: c.label, desc: c.desc })),
    contextKey: "official_exam",
    occurredDate: "",
    maxDate: today(),
    xingceScore: "",
    shenlunScore: "",
    timed: true,
    submitting: false,
    history: [] as HistoryVM[],
    usingDemo: false,
  },

  onLoad() {
    void this.loadHistory();
  },

  async loadHistory() {
    try {
      const history = await fetchScoreEvidenceHistory();
      this.setData({
        history: history.items.map((it) => {
          const parts: string[] = [];
          if (typeof it.xingceScore === "number") parts.push(`行测 ${it.xingceScore}`);
          if (typeof it.shenlunScore === "number") parts.push(`申论 ${it.shenlunScore}`);
          return {
            evidenceId: it.evidenceId,
            yearLabel: `${it.examYear} 年`,
            sourceLabel: SOURCE_LABEL[it.sourceType] ?? it.sourceType,
            scoreLine: parts.length ? parts.join(" · ") : "分数未填",
            active: it.active,
          };
        }),
      });
    } catch (err) {
      console.warn("[score-import] history fallback to demo", err);
      this.setData({ usingDemo: true });
    }
  },

  onPickContext(e: WechatMiniprogram.TouchEvent) {
    const { key } = e.currentTarget.dataset as { key: string };
    this.setData({ contextKey: key });
  },

  onDateChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ occurredDate: String(e.detail.value) });
  },

  onXingceInput(e: WechatMiniprogram.Input) {
    this.setData({ xingceScore: e.detail.value });
  },

  onShenlunInput(e: WechatMiniprogram.Input) {
    this.setData({ shenlunScore: e.detail.value });
  },

  onTimedChange(e: WechatMiniprogram.SwitchChange) {
    this.setData({ timed: e.detail.value });
  },

  validate(): string | null {
    if (!this.data.occurredDate) return "先选一下考试日期";
    const x = this.data.xingceScore.trim();
    const s = this.data.shenlunScore.trim();
    if (!x && !s) return "行测和申论至少填一科";
    if (x) {
      const n = Number(x);
      if (Number.isNaN(n) || n < 0 || n > 100) return "行测分数要在 0 ~ 100 之间";
    }
    if (s) {
      const n = Number(s);
      if (Number.isNaN(n) || n < 0 || n > 100) return "申论分数要在 0 ~ 100 之间";
    }
    return null;
  },

  async onSubmit() {
    if (this.data.submitting) return;
    const problem = this.validate();
    if (problem) {
      wx.showToast({ title: problem, icon: "none" });
      return;
    }
    this.setData({ submitting: true });

    const x = this.data.xingceScore.trim();
    const s = this.data.shenlunScore.trim();

    if (this.data.usingDemo) {
      wx.showToast({ title: "已记下（演示模式）", icon: "none" });
      setTimeout(() => this.goBack(), 1000);
      return;
    }

    try {
      await reportScoreEvidence(
        {
          occurredAt: `${this.data.occurredDate}T09:00:00+08:00`,
          reportedContext: this.data.contextKey as
            | "official_exam" | "full_mock" | "historical_estimate",
          xingceScore: x ? Number(x) : null,
          shenlunScore: s ? Number(s) : null,
          timedStatus: this.data.timed ? "timed" : "untimed",
        },
        `score-report-${this.data.occurredDate}-${x || "x"}-${s || "s"}`,
      );
      wx.showToast({ title: "记好了，这笔账能用来排计划了", icon: "none" });
      setTimeout(() => this.goBack(), 1200);
    } catch (err) {
      console.error("[score-import] submit failed", err);
      this.setData({ submitting: false });
      wx.showToast({ title: "没提交成功，再试一次", icon: "none" });
    }
  },

  goBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) });
  },

  onBack() {
    this.goBack();
  },
});
