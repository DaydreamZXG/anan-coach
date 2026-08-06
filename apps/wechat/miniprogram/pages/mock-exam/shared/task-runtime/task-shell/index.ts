import { taskShellSyncTone } from "./state";
import { measureSystemChrome } from "../../../../../components/safe-top/metrics";

Component({
  options: {
    multipleSlots: true,
  },
  data: {
    syncTone: "pending",
    statusBarHeight: 0,
    navigationHeight: 44,
    menuBottom: 0,
    capsuleReserve: 0,
  },
  properties: {
    layoutVariant: { type: String, value: "standard" },
    title: { type: String, value: "" },
    titleAvailable: { type: Boolean, value: false },
    modeLabel: { type: String, value: "" },
    current: { type: Number, value: 1 },
    total: { type: Number, value: 1 },
    elapsedLabel: { type: String, value: "00:00" },
    syncLabel: { type: String, value: "已保存" },
    materialAvailable: { type: Boolean, value: false },
    answerSheetAvailable: { type: Boolean, value: true },
    draftAvailable: { type: Boolean, value: false },
    markAvailable: { type: Boolean, value: true },
    timerPausable: { type: Boolean, value: false },
    timerPaused: { type: Boolean, value: false },
    marked: { type: Boolean, value: false },
    toolsAvailable: { type: Boolean, value: true },
    companionAvailable: { type: Boolean, value: false },
    focusToolAvailable: { type: Boolean, value: false },
    footerAvailable: { type: Boolean, value: true },
    headerAvailable: { type: Boolean, value: true },
  },
  observers: {
    syncLabel(syncLabel: string) {
      this.setData({ syncTone: taskShellSyncTone(syncLabel) });
    },
  },
  lifetimes: {
    attached() {
      this.measureSystemChrome();
    },
  },
  pageLifetimes: {
    show() {
      this.measureSystemChrome();
    },
  },
  methods: {
    measureSystemChrome() {
      this.setData(measureSystemChrome(8));
    },
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
    openCompanion() {
      this.triggerEvent("companion");
    },
    openFocusTool() {
      this.triggerEvent("focustool");
    },
  },
});
