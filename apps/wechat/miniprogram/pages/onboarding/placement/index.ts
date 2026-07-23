import { fetchPlacementTest, submitPlacementTest } from "../../../api/endpoints";
import { ApiError } from "../../../api/client";
import type {
  PlacementAnswer,
  PlacementSubmitResponse,
  PlacementTestTask,
  QuestionDetail,
} from "../../../api/generated/types.gen";

type Phase = "loading" | "answering" | "result" | "error";

interface ModuleBar {
  name: string;
  percent: number;
  detail: string;
}

Page({
  data: {
    statusBarHeight: 20,
    steps: [1, 2, 3, 4, 5, 6],
    step: 5,
    phase: "loading" as Phase,
    task: null as PlacementTestTask | null,
    current: null as QuestionDetail | null,
    currentIndex: 0,
    currentNo: 1,
    currentSelected: "",
    isLast: false,
    /** 已选答案：questionId -> 选项 label */
    selected: {} as Record<string, string>,
    submitting: false,
    requestId: "",
    result: null as PlacementSubmitResponse | null,
    scoreText: "",
    moduleBars: [] as ModuleBar[],
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    this.load();
  },

  async load() {
    this.setData({ phase: "loading" });
    try {
      const task = await fetchPlacementTest();
      this.setData({
        task,
        phase: "answering",
        currentIndex: 0,
        selected: {},
        currentSelected: "",
      });
      this.updateCurrent();
    } catch (err) {
      wx.showToast({ title: "网络不太稳定，稍后再试", icon: "none" });
      const apiErr = err as ApiError;
      this.setData({ phase: "error", requestId: apiErr.requestId ?? "" });
    }
  },

  /** 根据 currentIndex 同步当前题与已选态 */
  updateCurrent() {
    const task = this.data.task;
    if (!task) return;
    const q = task.questions[this.data.currentIndex];
    if (!q) return;
    this.setData({
      current: q,
      currentNo: this.data.currentIndex + 1,
      currentSelected: this.data.selected[q.questionId] ?? "",
      isLast: this.data.currentIndex === task.questions.length - 1,
    });
  },

  onPickOption(e: WechatMiniprogram.TouchEvent) {
    const q = this.data.current;
    if (!q) return;
    const label = e.currentTarget.dataset.label as string;
    this.setData({
      [`selected.${q.questionId}`]: label,
      currentSelected: label,
    });
  },

  onFooterTap() {
    if (this.data.isLast) {
      void this.onSubmitAnswers();
    } else {
      this.onNext();
    }
  },

  onNext() {
    const task = this.data.task;
    if (!task || !this.data.currentSelected) return;
    if (this.data.currentIndex >= task.questions.length - 1) return;
    this.setData({ currentIndex: this.data.currentIndex + 1 });
    this.updateCurrent();
  },

  async onSubmitAnswers() {
    const task = this.data.task;
    if (!task || !this.data.currentSelected || this.data.submitting) return;
    this.setData({ submitting: true });
    try {
      // 未作答的题按 "_" 提交
      const answers: PlacementAnswer[] = task.questions.map((q) => ({
        questionId: q.questionId,
        selectedAnswer: this.data.selected[q.questionId] ?? "_",
      }));
      const result = await submitPlacementTest(answers);
      this.setData({
        result,
        scoreText: `${Math.round(result.estimatedXingceScore * 10) / 10}`,
        moduleBars: (result.moduleResults ?? []).map((m) => ({
          name: m.moduleName,
          percent: m.accuracy <= 1 ? Math.round(m.accuracy * 100) : Math.round(m.accuracy),
          detail: `${m.correct}/${m.total} 题`,
        })),
        phase: "result",
      });
    } catch {
      wx.showToast({ title: "网络不太稳定，稍后再试", icon: "none" });
    } finally {
      this.setData({ submitting: false });
    }
  },

  onSkip() {
    wx.showModal({
      title: "跳过定位测试？",
      content: "跳过后岸岸先按较少的信息安排，之后可以补测。",
      confirmText: "跳过",
      cancelText: "继续答题",
      success: (res) => {
        if (res.confirm) {
          wx.redirectTo({ url: "/pages/onboarding/result/index" });
        }
      },
    });
  },

  onFinish() {
    wx.redirectTo({ url: "/pages/onboarding/result/index" });
  },
});
