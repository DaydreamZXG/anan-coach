Component({
  options: {
    multipleSlots: true,
  },
  properties: {
    title: { type: String, value: "" },
    modeLabel: { type: String, value: "" },
    current: { type: Number, value: 1 },
    total: { type: Number, value: 1 },
    elapsedLabel: { type: String, value: "00:00" },
    syncLabel: { type: String, value: "已保存" },
    materialAvailable: { type: Boolean, value: false },
    answerSheetAvailable: { type: Boolean, value: true },
    toolsAvailable: { type: Boolean, value: true },
    focusToolAvailable: { type: Boolean, value: false },
    footerAvailable: { type: Boolean, value: true },
  },
  methods: {
    exit() {
      this.triggerEvent("exit");
    },
    openMaterial() {
      this.triggerEvent("material");
    },
    openAnswerSheet() {
      this.triggerEvent("answersheet");
    },
    openTools() {
      this.triggerEvent("tools");
    },
    openFocusTool() {
      this.triggerEvent("focustool");
    },
  },
});
