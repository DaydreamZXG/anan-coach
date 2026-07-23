import { createWeeklyMockExam, submitMockExam } from "../../api/endpoints";
import type { MockExamSession } from "../../api/generated/types.gen";

type QView = {
  questionId: string;
  moduleName: string;
  stem: string;
  options: Array<{ optionId: string; label: string; text: string }>;
};

const DEMO_QUESTIONS: QView[] = [
  {
    questionId: "demo-m1",
    moduleName: "言语理解",
    stem: "依次填入下列横线处的词语，最恰当的一组是：传统技艺的传承不能只靠情怀______，更需要制度化的保障让匠人能够______地投入创作。",
    options: [
      { optionId: "demo-m1-a", label: "A", text: "维系  心无旁骛" },
      { optionId: "demo-m1-b", label: "B", text: "支撑  全神贯注" },
      { optionId: "demo-m1-c", label: "C", text: "延续  专心致志" },
      { optionId: "demo-m1-d", label: "D", text: "推动  聚精会神" },
    ],
  },
  {
    questionId: "demo-m2",
    moduleName: "资料分析",
    stem: "2024 年一季度某省进出口总额 4820 亿元，同比增长 8.5%，其中出口 2890 亿元。则 2023 年一季度该省进出口总额约为：",
    options: [
      { optionId: "demo-m2-a", label: "A", text: "4443 亿元" },
      { optionId: "demo-m2-b", label: "B", text: "4230 亿元" },
      { optionId: "demo-m2-c", label: "C", text: "5230 亿元" },
      { optionId: "demo-m2-d", label: "D", text: "4118 亿元" },
    ],
  },
];

function toView(session: MockExamSession): QView[] {
  return session.questions.map((q) => ({
    questionId: q.questionId,
    moduleName: q.moduleName,
    stem: q.stem,
    options: q.options.map((o) => ({ optionId: o.optionId, label: o.label, text: o.text })),
  }));
}

Page({
  data: {
    loading: true,
    usingDemo: false,
    phase: "brief" as "brief" | "exam",
    title: "",
    durationMinutes: 0,
    questionCount: 0,
    blueprint: [] as Array<{ moduleName: string; questionCount: number }>,
    sourceGrounding: "",
    questions: [] as QView[],
    index: 0,
    answers: {} as Record<string, string>,
    currentSelected: "",
    answeredCount: 0,
    remainText: "",
    submitting: false,
  },
  _sessionId: "",
  _revision: 0,
  _deadline: 0,
  _timer: 0 as unknown as ReturnType<typeof setInterval> | 0,

  onLoad() {
    void this.load();
  },

  onUnload() {
    if (this._timer) clearInterval(this._timer);
  },

  async load() {
    try {
      const session = await createWeeklyMockExam();
      this._sessionId = session.sessionId;
      this._revision = session.revision;
      this.setData({
        loading: false,
        title: session.title,
        durationMinutes: session.durationMinutes,
        questionCount: session.questionCount,
        blueprint: session.blueprint.map((b) => ({
          moduleName: b.moduleName,
          questionCount: b.questionCount,
        })),
        sourceGrounding: session.sourceGrounding,
        questions: toView(session),
      });
    } catch (err) {
      console.warn("[mock] weekly fallback to demo", err);
      this.setData({
        loading: false,
        usingDemo: true,
        title: "本周全真模考（演示）",
        durationMinutes: 10,
        questionCount: DEMO_QUESTIONS.length,
        blueprint: [
          { moduleName: "言语理解", questionCount: 1 },
          { moduleName: "资料分析", questionCount: 1 },
        ],
        sourceGrounding: "演示卷：后端未连接时的预览数据",
        questions: DEMO_QUESTIONS,
      });
    }
  },

  onStart() {
    this._deadline = Date.now() + this.data.durationMinutes * 60 * 1000;
    this.setData({ phase: "exam" });
    this.tick();
    this._timer = setInterval(() => this.tick(), 1000);
  },

  tick() {
    const left = Math.max(0, this._deadline - Date.now());
    const m = Math.floor(left / 60000);
    const s = Math.floor((left % 60000) / 1000);
    this.setData({ remainText: `${m}:${s < 10 ? "0" : ""}${s}` });
    if (left <= 0) {
      if (this._timer) clearInterval(this._timer);
      wx.showToast({ title: "时间到，自动交卷", icon: "none" });
      void this.doSubmit();
    }
  },

  onSelect(e: WechatMiniprogram.TouchEvent) {
    const { label } = e.currentTarget.dataset as { label: string };
    const q = this.data.questions[this.data.index];
    if (!q) return;
    const answers = { ...this.data.answers, [q.questionId]: label };
    this.setData({
      answers,
      currentSelected: label,
      answeredCount: Object.keys(answers).length,
    });
  },

  onPrev() {
    if (this.data.index === 0) return;
    this.moveTo(this.data.index - 1);
  },

  onNext() {
    if (this.data.index >= this.data.questions.length - 1) return;
    this.moveTo(this.data.index + 1);
  },

  moveTo(i: number) {
    const q = this.data.questions[i];
    this.setData({
      index: i,
      currentSelected: q ? (this.data.answers[q.questionId] ?? "") : "",
    });
  },

  onSubmitTap() {
    const unanswered = this.data.questions.length - this.data.answeredCount;
    wx.showModal({
      title: "确定交卷？",
      content: unanswered > 0 ? `还有 ${unanswered} 题未作答，交卷后不能再改。` : "交卷后不能再改。",
      confirmText: "交卷",
      success: (res) => {
        if (res.confirm) void this.doSubmit();
      },
    });
  },

  async doSubmit() {
    if (this.data.submitting) return;
    this.setData({ submitting: true });
    if (this._timer) clearInterval(this._timer);

    if (this.data.usingDemo) {
      wx.redirectTo({ url: "/pages/mock-report/index" });
      return;
    }

    try {
      const res = await submitMockExam(this._sessionId, {
        answers: this.data.questions.map((q) => ({
          questionId: q.questionId,
          selectedAnswer: this.data.answers[q.questionId] ?? null,
        })),
        expectedRevision: this._revision,
      });
      wx.redirectTo({ url: `/pages/mock-report/index?sessionId=${res.sessionId}` });
    } catch (err) {
      console.error("[mock] submit failed", err);
      this.setData({ submitting: false });
      wx.showToast({ title: "交卷没成功，答案还在，再试一次", icon: "none" });
    }
  },

  onBack() {
    if (this.data.phase === "exam") {
      wx.showModal({
        title: "退出模考？",
        content: "中途退出这场就作废了，下周才有新卷。",
        confirmText: "继续作答",
        cancelText: "退出",
        success: (res) => {
          if (res.cancel) wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/ability/index" }) });
        },
      });
      return;
    }
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/ability/index" }) });
  },
});
