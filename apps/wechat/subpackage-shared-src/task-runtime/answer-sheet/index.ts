Component({
  properties: {
    visible: { type: Boolean, value: false },
    current: { type: Number, value: 1 },
    sections: { type: null, value: [] },
    currentAnchorId: { type: String, value: "" },
    answered: { type: Number, value: 0 },
    unanswered: { type: Number, value: 0 },
    marked: { type: Number, value: 0 },
    subtitle: { type: String, value: "" },
    insightAvailable: { type: Boolean, value: false },
    hint: { type: String, value: "点题号即可直接切换" },
    submitLabel: { type: String, value: "确认交卷" },
  },
  data: {
    scrollTarget: "",
  },
  observers: {
    "visible,currentAnchorId"(visible: boolean, currentAnchorId: string) {
      if (!visible || !currentAnchorId) return;
      this.setData({ scrollTarget: "" });
      wx.nextTick(() => {
        if (!this.data.visible) return;
        this.setData({ scrollTarget: currentAnchorId });
      });
    },
  },
  methods: {
    close() {
      this.triggerEvent("close");
    },
    selectQuestion(event: WechatMiniprogram.BaseEvent) {
      this.triggerEvent("select", { index: Number(event.currentTarget.dataset.index) });
    },
    submit() {
      this.triggerEvent("submit");
    },
    noop() {},
  },
});
