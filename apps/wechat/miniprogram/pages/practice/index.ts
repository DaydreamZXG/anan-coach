import {
  startTaskSession,
  submitTaskSession,
} from "../../api/endpoints";
import type {
  SafeQuestion,
  TaskSessionProjection,
} from "../../api/generated/types.gen";

type QView = {
  questionId: string;
  moduleName: string;
  stem: string;
  options: Array<{ optionId: string; label: string; text: string }>;
};

const DEMO_QUESTIONS: QView[] = [
  {
    questionId: "dq1",
    moduleName: "言语理解",
    stem: "下列句子中，没有语病的一项是：",
    options: [
      { optionId: "a", label: "A", text: "通过这次学习，使我认识到了自己的不足。" },
      { optionId: "b", label: "B", text: "能否杜绝浪费，是勤俭节约的重要体现。" },
      { optionId: "c", label: "C", text: "他不仅学习成绩优异，而且乐于助人。" },
      { optionId: "d", label: "D", text: "为了避免今后不再发生类似事故，我们加强了培训。" },
    ],
  },
  {
    questionId: "dq2",
    moduleName: "言语理解",
    stem: "填入横线处最恰当的一项是：改革不是____，而是循序渐进的过程。",
    options: [
      { optionId: "a", label: "A", text: "一蹴而就" },
      { optionId: "b", label: "B", text: "一劳永逸" },
      { optionId: "c", label: "C", text: "一成不变" },
      { optionId: "d", label: "D", text: "一往无前" },
    ],
  },
  {
    questionId: "dq3",
    moduleName: "言语理解",
    stem: "与「未雨绸缪」意思最接近的是：",
    options: [
      { optionId: "a", label: "A", text: "亡羊补牢" },
      { optionId: "b", label: "B", text: "防患未然" },
      { optionId: "c", label: "C", text: "临渴掘井" },
      { optionId: "d", label: "D", text: "望梅止渴" },
    ],
  },
];

function toView(qs: SafeQuestion[]): QView[] {
  return qs.map((q) => ({
    questionId: q.questionId,
    moduleName: q.moduleName,
    stem: q.stem,
    options: q.options.map((o) => ({
      optionId: o.optionId,
      label: o.label,
      text: o.text,
    })),
  }));
}

Page({
  data: {
    loading: true,
    usingDemo: false,
    sessionId: "",
    revision: 0,
    questions: [] as QView[],
    index: 0,
    answers: {} as Record<string, string>,
    elapsed: 0,
    elapsedText: "00:00",
    submitting: false,
  },
  _timer: 0 as unknown as ReturnType<typeof setInterval>,

  onLoad(query: Record<string, string>) {
    this.load(query.taskId || "");
  },

  onUnload() {
    if (this._timer) clearInterval(this._timer);
  },

  async load(taskId: string) {
    try {
      const proj: TaskSessionProjection = await startTaskSession(taskId);
      this.setData({
        loading: false,
        sessionId: proj.sessionId,
        revision: proj.revision,
        questions: toView(proj.bundle.questions),
      });
    } catch (err) {
      console.error("[practice] start failed, fallback to demo", err);
      this.setData({ loading: false, usingDemo: true, questions: DEMO_QUESTIONS });
    }
    this.startTimer();
  },

  startTimer() {
    this._timer = setInterval(() => {
      const elapsed = this.data.elapsed + 1;
      const m = String(Math.floor(elapsed / 60)).padStart(2, "0");
      const s = String(elapsed % 60).padStart(2, "0");
      this.setData({ elapsed, elapsedText: `${m}:${s}` });
    }, 1000);
  },

  onSelect(e: WechatMiniprogram.TouchEvent) {
    const { qid, oid } = e.currentTarget.dataset as { qid: string; oid: string };
    this.setData({ [`answers.${qid}`]: oid });
  },

  onPrev() {
    if (this.data.index > 0) this.setData({ index: this.data.index - 1 });
  },

  onNext() {
    if (this.data.index < this.data.questions.length - 1) {
      this.setData({ index: this.data.index + 1 });
    }
  },

  async onSubmit() {
    if (this.data.submitting) return;
    const answered = Object.keys(this.data.answers).length;
    if (answered < this.data.questions.length) {
      const ok = await new Promise<boolean>((resolve) => {
        wx.showModal({
          title: "还有题没做完",
          content: `还有 ${this.data.questions.length - answered} 题未作答，确定交卷吗？`,
          success: (r) => resolve(r.confirm),
        });
      });
      if (!ok) return;
    }
    this.setData({ submitting: true });
    if (this._timer) clearInterval(this._timer);

    if (!this.data.usingDemo && this.data.sessionId) {
      try {
        await submitTaskSession(this.data.sessionId, {
          expectedRevision: this.data.revision,
          totalElapsedSeconds: this.data.elapsed,
        });
      } catch (err) {
        console.error("[practice] submit failed", err);
      }
    }
    wx.redirectTo({
      url: `/pages/daily-settlement/index?answered=${answered}&total=${this.data.questions.length}&seconds=${this.data.elapsed}`,
      fail: () => wx.switchTab({ url: "/pages/today/index" }),
    });
  },

  onBack() {
    wx.showModal({
      title: "退出练习",
      content: "当前进度不会保存，确定退出？",
      success: (r) => {
        if (r.confirm) wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/today/index" }) });
      },
    });
  },
});
