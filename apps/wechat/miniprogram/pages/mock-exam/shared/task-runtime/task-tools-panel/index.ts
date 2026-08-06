Component({
  properties: {
    visible: { type: Boolean, value: false },
    preferences: { type: null, value: { fontScale: 1, colorMode: "standard" } },
    disputeAvailable: { type: Boolean, value: true },
    helpAvailable: { type: Boolean, value: true },
    submitAvailable: { type: Boolean, value: false },
    interactionLocked: { type: Boolean, value: false },
    lockedCopy: {
      type: String,
      value: "公式卡、完整解析和解题 AI 在提交前不可用；国考模式不提供计算器。",
    },
  },
  methods: {
    close() {
      this.triggerEvent("close");
    },
    dispute() {
      this.triggerEvent("dispute");
    },
    help() {
      this.triggerEvent("help");
    },
    toggleEyeCare() {
      this.triggerEvent("toggleeyecare");
    },
    decreaseFont() {
      this.triggerEvent("decreasefont");
    },
    increaseFont() {
      this.triggerEvent("increasefont");
    },
    submit() {
      this.triggerEvent("submit");
    },
  },
});
