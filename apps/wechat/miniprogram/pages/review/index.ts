import { openReviewCourt, submitReviewVerdict } from "../../api/endpoints";
import type { DocketCard } from "../../api/generated/types.gen";

type CardVM = {
  cardId: string;
  questionId: string;
  moduleName: string;
  worldLabel: string;
  title: string;
  narrative: string;
  stem: string;
  options: Array<{ label: string; text: string }>;
};

const DEMO_CARDS: CardVM[] = [
  {
    cardId: "demo-c1",
    questionId: "demo-q1",
    moduleName: "资料分析",
    worldLabel: "增长率世界",
    title: "上周栽过的增长率陷阱",
    narrative: "这题你上周做错过一次。当时把「同比增长」看成了「环比增长」，我们再对一次账。",
    stem: "2023 年某市 GDP 为 1.2 万亿元，同比增长 5%。则 2022 年该市 GDP 约为：",
    options: [
      { label: "A", text: "1.14 万亿元" },
      { label: "B", text: "1.20 万亿元" },
      { label: "C", text: "1.26 万亿元" },
      { label: "D", text: "1.05 万亿元" },
    ],
  },
  {
    cardId: "demo-c2",
    questionId: "demo-q2",
    moduleName: "判断推理",
    worldLabel: "翻译推理世界",
    title: "逆否命题又来了",
    narrative: "「只有…才…」的翻译方向你错过两次，这张卡是第三次见面，这次把它钉死。",
    stem: "「只有通过笔试，才能进入面试。」以下哪项与题干含义一致？",
    options: [
      { label: "A", text: "通过笔试就能进入面试" },
      { label: "B", text: "进入面试的人都通过了笔试" },
      { label: "C", text: "没进入面试的人都没通过笔试" },
      { label: "D", text: "通过笔试是进入面试的充分条件" },
    ],
  },
];

const DEMO_ANSWERS: Record<string, string> = { "demo-c1": "A", "demo-c2": "B" };

const DEMO_LINES: Record<string, { pass: string; fail: string }> = {
  "demo-c1": {
    pass: "对了。基期量 = 现期量 ÷ (1 + 增长率)，这次方向没走反，这个坑算填上了。",
    fail: "还是这个坑：1.2 ÷ 1.05 ≈ 1.14。看到「同比增长」先写公式再动笔，不心算。",
  },
  "demo-c2": {
    pass: "稳了。「只有 A 才 B」翻译成 B→A，你这次方向对了。",
    fail: "再记一次：「只有 A 才 B」= B→A。进面试的人必然过了笔试，选 B。",
  },
};

function toVM(cards: DocketCard[]): CardVM[] {
  return cards.map((c) => ({
    cardId: c.cardId,
    questionId: c.questionId,
    moduleName: c.moduleName,
    worldLabel: c.worldLabel,
    title: c.title,
    narrative: c.narrative,
    stem: c.stem,
    options: c.options.map((o) => ({ label: o.label, text: o.text })),
  }));
}

Page({
  data: {
    loading: true,
    usingDemo: false,
    docketId: "",
    companionLine: "",
    cards: [] as CardVM[],
    index: 0,
    phase: "answer" as "answer" | "verdict" | "done",
    selected: "",
    stamp: "",
    verdictLine: "",
    correctCount: 0,
    submitting: false,
  },
  _cardStartedAt: 0,

  onLoad() {
    void this.load();
  },

  async load() {
    try {
      const court = await openReviewCourt();
      this.setData({
        loading: false,
        docketId: court.docketId,
        companionLine: court.companionLine,
        cards: toVM(court.cards),
      });
    } catch (err) {
      console.warn("[review] open-court fallback to demo", err);
      this.setData({
        loading: false,
        usingDemo: true,
        companionLine: "今天有两笔旧账要对。不着急，一张一张来，错过的地方才是分数长出来的地方。",
        cards: DEMO_CARDS,
      });
    }
    this._cardStartedAt = Date.now();
  },

  onSelect(e: WechatMiniprogram.TouchEvent) {
    if (this.data.phase !== "answer") return;
    const { label } = e.currentTarget.dataset as { label: string };
    this.setData({ selected: label });
  },

  async onJudge() {
    const card = this.data.cards[this.data.index];
    if (!card || !this.data.selected || this.data.submitting) return;
    this.setData({ submitting: true });
    const elapsedSeconds = Math.max(1, Math.round((Date.now() - this._cardStartedAt) / 1000));

    if (this.data.usingDemo) {
      const correct = DEMO_ANSWERS[card.cardId] === this.data.selected;
      const lines = DEMO_LINES[card.cardId];
      this.applyVerdict(correct, lines ? (correct ? lines.pass : lines.fail) : "");
      return;
    }

    try {
      const res = await submitReviewVerdict(this.data.docketId, card.cardId, {
        selectedAnswer: this.data.selected,
        elapsedSeconds,
      });
      this.applyVerdict(res.correct, res.companionLine);
    } catch (err) {
      console.error("[review] verdict failed", err);
      this.setData({ submitting: false });
      wx.showToast({ title: "网络不稳，再试一次", icon: "none" });
    }
  },

  applyVerdict(correct: boolean, line: string) {
    this.setData({
      phase: "verdict",
      stamp: correct ? "准" : "驳",
      verdictLine: line,
      correctCount: this.data.correctCount + (correct ? 1 : 0),
      submitting: false,
    });
  },

  onNextCard() {
    const next = this.data.index + 1;
    if (next >= this.data.cards.length) {
      this.setData({ phase: "done" });
      return;
    }
    this._cardStartedAt = Date.now();
    this.setData({ index: next, phase: "answer", selected: "", stamp: "", verdictLine: "" });
  },

  onBackToday() {
    wx.switchTab({ url: "/pages/today/index" });
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/today/index" }) });
  },
});
