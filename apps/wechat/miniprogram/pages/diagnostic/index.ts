const questions = [
  {
    module: "资料分析",
    stem: "某地区今年完成投资 126 亿元，同比增长 12.5%。去年完成投资约为多少亿元？",
    options: ["108", "112", "116", "120"],
  },
  {
    module: "言语理解",
    stem: "这道原型题用于确认翻页、信心度和用时状态，不用于真实能力定级。",
    options: ["选项 A", "选项 B", "选项 C", "选项 D"],
  },
];

Page({
  data: { index: 0, questions, selected: -1, confidence: 0, complete: false },

  selectAnswer(event: WechatMiniprogram.BaseEvent) {
    this.setData({ selected: Number(event.currentTarget.dataset.index) });
  },

  selectConfidence(event: WechatMiniprogram.BaseEvent) {
    this.setData({ confidence: Number(event.currentTarget.dataset.value) });
  },

  next() {
    if (this.data.index < questions.length - 1) {
      this.setData({ index: this.data.index + 1, selected: -1, confidence: 0 });
      return;
    }
    this.setData({ complete: true });
  },

  backToday() {
    void wx.switchTab({ url: "/pages/today/index" });
  },
});
