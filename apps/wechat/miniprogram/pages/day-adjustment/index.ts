import { PlanningApi } from "../../api/planning";
import type {
  DailyCapacityCommand,
  DailyCapacityPreview,
  TodayDashboard,
} from "../../api/generated/types.gen";
import { routes } from "../../app/navigation/routes";
import {
  plannedDuration,
  presentCapacityChanges,
  type CapacityChangeView,
} from "../../features/planning/capacity-preview";
import { errorView, requireAuthenticatedClient } from "../../features/learning-brain/presentation";
import { createIdempotencyKey } from "../../platform/command-id";
import { formatCapacityDuration, prepareCapacityCommand } from "../today/capacity";

interface EnergyOption {
  label: string;
  value: number;
}

const ENERGY_OPTIONS: readonly EnergyOption[] = [
  { label: "有点累", value: 2 },
  { label: "正常", value: 3 },
  { label: "状态不错", value: 4 },
];

const TIME_PRESETS = [
  { label: "1 小时", minutes: 60 },
  { label: "2 小时", minutes: 120 },
  { label: "4 小时", minutes: 240 },
  { label: "今天不学", minutes: 0 },
] as const;

const MINUTES_STEP = 30;
const MAX_VISIBLE_MINUTES = 600;

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    dashboard: null as TodayDashboard | null,
    step: "edit" as "edit" | "preview",
    minutes: 0,
    selectedDurationLabel: "今天不安排",
    currentDurationLabel: "",
    currentTaskCount: 0,
    currentFocus: "",
    energy: 3,
    stress: 3,
    confidenceLevel: 3,
    energyOptions: ENERGY_OPTIONS,
    timePresets: TIME_PRESETS,
    minutesStep: MINUTES_STEP,
    preview: null as DailyCapacityPreview | null,
    previewChanges: [] as CapacityChangeView[],
    previewHeadline: "",
    previewRationale: "",
    previewImpact: "",
    previewing: false,
    submitting: false,
  },

  onLoad() {
    void this.loadToday();
  },

  async loadToday() {
    const client = requireAuthenticatedClient();
    if (!client) return;
    this.setData({
      status: "loading",
      errorMessage: "",
      requestId: "",
      step: "edit",
      preview: null,
    });
    try {
      const { data } = await new PlanningApi(client).getToday();
      const activeTasks = data.tasks.filter(
        (task) => task.status === "ready" || task.status === "in_progress",
      );
      this.setData({
        status: "ready",
        dashboard: data,
        minutes: data.capacity.availableMinutes,
        selectedDurationLabel: formatCapacityDuration(data.capacity.availableMinutes),
        currentDurationLabel: formatCapacityDuration(
          activeTasks.reduce((total, task) => total + task.estimatedMinutes, 0),
        ),
        currentTaskCount: activeTasks.length,
        currentFocus: activeTasks[0]?.title ?? data.headline,
        energy: normalizedEnergy(data.capacity.energy),
        stress: data.capacity.stress,
        confidenceLevel: data.capacity.confidenceLevel,
        errorMessage: "",
      });
    } catch (error) {
      const view = errorView(error, "今天的安排没有加载成功，请重试。");
      this.setData({
        status: "error",
        errorMessage: view.message,
        requestId: view.requestId,
      });
    }
  },

  exit() {
    void wx.navigateBack({ delta: 1 });
  },

  adjustMinutes(event: WechatMiniprogram.BaseEvent) {
    if (this.data.previewing || this.data.submitting) return;
    const delta = Number(event.currentTarget.dataset.delta);
    const current = Number(this.data.minutes);
    if (!Number.isInteger(delta) || !Number.isInteger(current)) return;
    const next = Math.max(0, Math.min(MAX_VISIBLE_MINUTES, current + delta));
    this.updateMinutes(next);
  },

  chooseMinutes(event: WechatMiniprogram.BaseEvent) {
    if (this.data.previewing || this.data.submitting) return;
    const minutes = Number(event.currentTarget.dataset.minutes);
    if (!Number.isInteger(minutes) || minutes < 0 || minutes > MAX_VISIBLE_MINUTES) return;
    this.updateMinutes(minutes);
  },

  updateMinutes(minutes: number) {
    this.setData({
      minutes,
      selectedDurationLabel: formatCapacityDuration(minutes),
      preview: null,
      errorMessage: "",
      requestId: "",
    });
  },

  chooseEnergy(event: WechatMiniprogram.BaseEvent) {
    if (this.data.previewing || this.data.submitting) return;
    const energy = Number(event.currentTarget.dataset.value);
    if (![2, 3, 4].includes(energy)) return;
    this.setData({
      energy,
      preview: null,
      errorMessage: "",
      requestId: "",
    });
  },

  commandFromDraft(): DailyCapacityCommand | null {
    const dashboard = this.data.dashboard;
    if (!dashboard) return null;
    const prepared = prepareCapacityCommand(
      {
        availableMinutes: String(this.data.minutes),
        energy: this.data.energy,
        stress: this.data.stress,
        confidenceLevel: this.data.confidenceLevel,
      },
      dashboard.capacity.capacityVersionId,
    );
    if (!prepared.command) {
      this.setData({ errorMessage: prepared.error });
      return null;
    }
    return prepared.command;
  },

  async generatePreview() {
    const client = requireAuthenticatedClient();
    const command = this.commandFromDraft();
    if (!client || !command || this.data.previewing || this.data.submitting) return;
    this.setData({ previewing: true, errorMessage: "", requestId: "" });
    try {
      const { data } = await new PlanningApi(client).previewTodayCapacity(command);
      const proposed = data.proposedPlan;
      const previewChanges = presentCapacityChanges(data.currentPlan.tasks, proposed.tasks);
      const taskDuration = plannedDuration(proposed.tasks);
      const capacityDuration = formatCapacityDuration(proposed.capacity.availableMinutes);
      const goalEvidence = proposed.goalGap.evidenceSummary.trim();
      this.setData({
        previewing: false,
        step: "preview",
        preview: data,
        previewChanges,
        previewHeadline: proposed.headline,
        previewRationale: proposed.rationale,
        previewImpact: [
          `调整后安排 ${String(proposed.tasks.length)} 项任务，共 ${taskDuration}；今天可用 ${capacityDuration}。`,
          goalEvidence ? `目标依据：${goalEvidence}` : "",
        ]
          .filter(Boolean)
          .join(" "),
      });
    } catch (error) {
      const view = errorView(error, "调整预览没有生成成功，请重新载入后再试。");
      this.setData({
        previewing: false,
        errorMessage: view.message,
        requestId: view.requestId,
      });
    }
  },

  backToEdit() {
    if (this.data.submitting) return;
    this.setData({
      step: "edit",
      errorMessage: "",
      requestId: "",
    });
  },

  async confirmAdjustment() {
    const client = requireAuthenticatedClient();
    const command = this.commandFromDraft();
    if (!client || !command || !this.data.preview || this.data.submitting || this.data.previewing) {
      return;
    }
    this.setData({ submitting: true, errorMessage: "", requestId: "" });
    try {
      await new PlanningApi(client).setTodayCapacity(
        command,
        await createIdempotencyKey("capacity"),
      );
      this.setData({ submitting: false });
      void wx.switchTab({ url: routes.today });
    } catch (error) {
      const view = errorView(error, "今天的安排没有保存成功，请重新载入后再试。");
      this.setData({
        submitting: false,
        errorMessage: view.message,
        requestId: view.requestId,
      });
    }
  },

  retry() {
    void this.loadToday();
  },
});

function normalizedEnergy(value: number): number {
  if (value <= 2) return 2;
  if (value >= 4) return 4;
  return 3;
}
