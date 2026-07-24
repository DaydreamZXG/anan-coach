import { taskShellSyncTone } from "./state";

Component({
  options: {
    multipleSlots: true,
  },
  data: {
    syncTone: "pending",
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
    draftAvailable: { type: Boolean, value: false },
    timerPausable: { type: Boolean, value: false },
    timerPaused: { type: Boolean, value: false },
    marked: { type: Boolean, value: false },
    toolsAvailable: { type: Boolean, value: true },
    focusToolAvailable: { type: Boolean, value: false },
    footerAvailable: { type: Boolean, value: true },
    headerAvailable: { type: Boolean, value: true },
  },
  observers: {
    syncLabel(syncLabel: string) {
      this.setData({ syncTone: taskShellSyncTone(syncLabel) });
    },
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
    openDraft() {
      this.triggerEvent("draft");
    },
    toggleTimer() {
      if (!this.data.timerPausable) return;
      this.triggerEvent("timer");
    },
    toggleMark() {
      this.triggerEvent("mark");
    },
    openTools() {
      this.triggerEvent("tools");
    },
    openFocusTool() {
      this.triggerEvent("focustool");
    },
  },
});
