import { fetchPlacementTest, submitPlacementTest } from "../../api/endpoints";
import type { PlacementTestTask } from "../../api/generated/types.gen";

type QView = {
  questionId: string;
  moduleName: string;
  stem: string;
  options: Array<{ label: string; text: string }>;
};

type ResultVM = { moduleName: string; correct: number; total: number; accuracyPct: number };

const DEMO_QUESTIONS: QView[] = [
  {
    questionId: "demo-p1",
    moduleName: "言语理解",
    stem: "填入横线处最恰当的一项是：好的政策不能只停留在纸面上，关键要____到基层的每一个环节。",
    options: [
      { label: "A", text: "落实" },
      { label: "B", text: "贯彻" },
      { label: "C", text: "渗透" },
      { label: "D", text: "执行" },
    ],
  },
  {
    questionId: "demo-p2",
    moduleName: "判断推理",
    stem: "所有优秀的公务员都善于倾听。小张善于倾听。由此可以推出：",
    options: [
      { label: "A", text: "小张是优秀的公务员" },
      { label: "B", text: "小张可能是优秀的公务员" },
      { label: "C", text: "善于倾听的都是优秀公务员" },
      { label: "D", text: "小张不是优秀的公务员" },
    ],
  },
  {
    questionId: "demo-p3",
    moduleName: "资料分析",
    stem: "某县 2023 年粮食产量 48 万吨，比上年增长 6.7%。2022 年该县粮食产量约为：",
    options: [
      { label: "A", text: "45 万吨" },
      { label: "B", text: "44 万吨" },
      { label: "C", text: "46 万吨" },
      { label: "D", text: "51 万吨" },
    ],
  },
];

function toView(task: PlacementTestTask): QView[] {
  return task.questions.map((q) => ({
    questionId: q.questionId,
    moduleName: q.moduleName ?? "综合",
    stem: q.stem,
    options: q.options.map((o) => ({ label: o.label, text: o.optionText })),
  }));
}

Page({
  data: {
    loading: true,
    usingDemo: false,
    phase: "brief" as "brief" | "test" | "result",
    title: "",
    questionCount: 0,
    durationMinutes: 0,
    modulePlan: [] as Array<{ moduleName: string; count: number }>,
    questions: [] as QView[],
    index: 0,
    answers: {} as Record<string, string>,
    currentSelected: "",
    // 结果
    estimatedScore: 0,
    results: [] as ResultVM[],
    submitting: false,
  },

  onLoad() {
    void this.load();
  },

  async load() {
    try {
      const task = await fetchPlacementTest();
      this.setData({
        loading: false,
        title: task.title,
        questionCount: task.questionCount,
        durationMinutes: task.durationMinutes,
        modulePlan: task.modulePlan.map((m) => ({ moduleName: m.moduleName, count: m.count })),
        questions: toView(task),
      });
    } catch (err) {
      console.warn("[placement] fallback to demo", err);
      this.setData({
        loading: false,
        usingDemo: true,
        title: "10 分钟摸个底（演示）",
        questionCount: DEMO_QUESTIONS.length,
        durationMinutes: 10,
        modulePlan: [
          { moduleName: "言语理解", count: 1 },
          { moduleName: "判断推理", count: 1 },
          { moduleName: "资料分析", count: 1 },
        ],
        questions: DEMO_QUESTIONS,
      });
    }
  },

  onStart() {
    this.setData({ phase: "test" });
  },

  onSelect(e: WechatMiniprogram.TouchEvent) {
    const { label } = e.currentTarget.dataset as { label: string };
    const q = this.data.questions[this.data.index];
    if (!q) return;
    this.setData({
      answers: { ...this.data.answers, [q.questionId]: label },
      currentSelected: label,
    });
  },

  onNext() {
    const next = this.data.index + 1;
    if (next >= this.data.questions.length) {
      void this.submit();
      return;
    }
    const q = this.data.questions[next];
    this.setData({
      index: next,
      currentSelected: q ? (this.data.answers[q.questionId] ?? "") : "",
    });
  },

  async submit() {
    if (this.data.submitting) return;
    this.setData({ submitting: true });

    if (this.data.usingDemo) {
      this.setData({
        phase: "result",
        estimatedScore: 61,
        results: [
          { moduleName: "言语理解", correct: 1, total: 1, accuracyPct: 100 },
          { moduleName: "判断推理", correct: 0, total: 1, accuracyPct: 0 },
          { moduleName: "资料分析", correct: 1, total: 1, accuracyPct: 100 },
        ],
        submitting: false,
      });
      return;
    }

    try {
      const res = await submitPlacementTest(
        Object.entries(this.data.answers).map(([questionId, selectedAnswer]) => ({
          questionId,
          selectedAnswer,
        })),
      );
      this.setData({
        phase: "result",
        estimatedScore: Math.round(res.estimatedXingceScore),
        results: res.moduleResults.map((m) => ({
          moduleName: m.moduleName,
          correct: m.correct,
          total: m.total,
          accuracyPct: Math.round(m.accuracy * 100),
        })),
        submitting: false,
      });
    } catch (err) {
      console.error("[placement] submit failed", err);
      this.setData({ submitting: false });
      wx.showToast({ title: "提交没成功，答案还在，再试一次", icon: "none" });
    }
  },

  onSkip() {
    wx.showModal({
      title: "先不摸底？",
      content: "没有摸底，岸岸前几天只能按平均水平给你排任务，准头会差一些。",
      confirmText: "还是测一下",
      cancelText: "直接开始",
      success: (res) => {
        if (res.cancel) wx.switchTab({ url: "/pages/today/index" });
      },
    });
  },

  onGoToday() {
    wx.switchTab({ url: "/pages/today/index" });
  },
});
