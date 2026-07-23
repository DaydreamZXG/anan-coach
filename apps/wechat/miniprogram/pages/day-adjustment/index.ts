import { authedV2, idemKey } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import type {
  DailyCapacityCommand,
  DailyCapacityCommandResult,
  TodayDashboard,
} from "../../api/generated/types.gen";

type ViewState = "loading" | "ready" | "error";

interface CapacityTimeOption {
  minutes: number;
  value: string;
  label: string;
  caption: string;
}

type ScaleMetric = "energy" | "stress" | "confidence";

const SCALE_LABELS: Readonly<Record<ScaleMetric, readonly string[]>> = {
  energy: ["极低", "偏低", "一般", "不错", "充沛"],
  stress: ["放松", "较低", "一般", "较高", "很高"],
  confidence: ["很不确定", "把握较低", "一般", "把握较高", "很确定"],
};

/** 时长格式化：0 → 今天不安排，整点/半点 → 小时，其余 → 分钟 */
function formatCapacityDuration(minutes: number): string {
  if (!Number.isInteger(minutes) || minutes < 0 || minutes > 1440) return "时间待确认";
  if (minutes === 0) return "今天不安排";
  if (minutes % 60 === 0) return `${String(minutes / 60)} 小时`;
  if (minutes % 30 === 0) return `${(minutes / 60).toFixed(1).replace(/\.0$/u, "")} 小时`;
  return `${String(minutes)} 分钟`;
}

function durationCaption(minutes: number): string {
  if (minutes === 0) return "完全休息";
  if (minutes <= 60) return "碎片日";
  if (minutes <= 180) return "在职友好";
  if (minutes <= 360) return "集中训练";
  if (minutes <= 600) return "全职高强度";
  return "卷王模式";
}

/** 0–720 分钟、每半小时一档的快捷选项 */
const TIME_OPTIONS: CapacityTimeOption[] = Array.from({ length: 25 }, (_, i) => i * 30).map(
  (minutes) => ({
    minutes,
    value: String(minutes),
    label: formatCapacityDuration(minutes),
    caption: durationCaption(minutes),
  }),
);

function scaleLabel(metric: ScaleMetric, value: number): string {
  return SCALE_LABELS[metric][value - 1] ?? "未选择";
}

/** 当日调整：GET /api/v2/plans/today 载入容量，PUT /api/v2/plans/today/capacity 提交后重排今日 */
Page({
  data: {
    statusBarHeight: 20,
    viewState: "loading" as ViewState,
    errorDesc: "",
    requestId: "",
    capacityVersionId: "",
    minutes: "0",
    selectedDurationLabel: "今天不安排",
    timeOptions: TIME_OPTIONS,
    energy: 3,
    stress: 3,
    confidenceLevel: 3,
    energyLabel: "",
    stressLabel: "",
    confidenceLabel: "",
    scaleOptions: [1, 2, 3, 4, 5],
    submitting: false,
    formError: "",
    successMessage: "",
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    void this.loadToday();
  },

  async loadToday() {
    this.setData({ viewState: "loading", errorDesc: "", requestId: "" });
    try {
      const data = await authedV2<TodayDashboard>({ method: "GET", path: "/api/v2/plans/today" });
      this.setData({
        viewState: "ready",
        capacityVersionId: data.capacity.capacityVersionId,
        minutes: String(data.capacity.availableMinutes),
        selectedDurationLabel: formatCapacityDuration(data.capacity.availableMinutes),
        energy: data.capacity.energy,
        stress: data.capacity.stress,
        confidenceLevel: data.capacity.confidenceLevel,
        energyLabel: scaleLabel("energy", data.capacity.energy),
        stressLabel: scaleLabel("stress", data.capacity.stress),
        confidenceLabel: scaleLabel("confidence", data.capacity.confidenceLevel),
        formError: "",
        successMessage: "",
      });
    } catch (err) {
      const apiErr = err as ApiError;
      this.setData({
        viewState: "error",
        errorDesc: "今日容量加载失败，请重试。",
        requestId: apiErr.requestId ?? "",
      });
    }
  },

  inputMinutes(event: WechatMiniprogram.Input) {
    const value = event.detail.value;
    this.setData({
      minutes: value,
      selectedDurationLabel: formatCapacityDuration(Number(value)),
      formError: "",
      successMessage: "",
    });
  },

  chooseMinutes(event: WechatMiniprogram.BaseEvent) {
    const minutes = Number(event.currentTarget.dataset.minutes);
    if (!Number.isInteger(minutes) || minutes < 0 || minutes > 720 || this.data.submitting) return;
    this.setData({
      minutes: String(minutes),
      selectedDurationLabel: formatCapacityDuration(minutes),
      formError: "",
      successMessage: "",
    });
  },

  chooseScale(event: WechatMiniprogram.BaseEvent) {
    const field = String(event.currentTarget.dataset.field ?? "");
    const value = Number(event.currentTarget.dataset.value);
    if (!Number.isInteger(value) || value < 1 || value > 5 || this.data.submitting) return;
    if (field === "energy") {
      this.setData({ energy: value, energyLabel: scaleLabel("energy", value) });
    } else if (field === "stress") {
      this.setData({ stress: value, stressLabel: scaleLabel("stress", value) });
    } else if (field === "confidenceLevel") {
      this.setData({ confidenceLevel: value, confidenceLabel: scaleLabel("confidence", value) });
    }
  },

  /** 今天确实无法学习：时长归零、把握降到最低 */
  markUnavailable() {
    if (this.data.submitting) return;
    this.setData({
      minutes: "0",
      selectedDurationLabel: formatCapacityDuration(0),
      confidenceLevel: 1,
      confidenceLabel: scaleLabel("confidence", 1),
      formError: "",
      successMessage: "",
    });
  },

  async submit() {
    if (this.data.submitting || this.data.viewState !== "ready") return;
    const availableMinutes = Number(this.data.minutes);
    if (!Number.isInteger(availableMinutes) || availableMinutes < 0 || availableMinutes > 1440) {
      this.setData({ formError: "请输入 0–1440 之间的完整分钟数。" });
      return;
    }
    if (!this.data.capacityVersionId) {
      this.setData({ formError: "今日计划已更新，请重新载入后再保存。" });
      return;
    }
    const command: DailyCapacityCommand = {
      basedOnCapacityVersionId: this.data.capacityVersionId,
      availableMinutes,
      energy: this.data.energy,
      stress: this.data.stress,
      confidenceLevel: this.data.confidenceLevel,
    };
    this.setData({ submitting: true, formError: "", successMessage: "" });
    try {
      const data = await authedV2<DailyCapacityCommandResult>({
        method: "PUT",
        path: "/api/v2/plans/today/capacity",
        body: command,
        idempotencyKey: idemKey(),
      });
      this.setData({
        submitting: false,
        capacityVersionId: data.plan.capacity.capacityVersionId,
        successMessage: `已生成计划 v${String(data.plan.versionNo)}：${data.plan.headline}`,
      });
    } catch (err) {
      const apiErr = err as ApiError;
      this.setData({
        submitting: false,
        formError: "当日计划调整失败，请重新加载后再试。",
        requestId: apiErr.requestId ?? "",
      });
    }
  },

  onRetry() {
    void this.loadToday();
  },

  onBack() {
    wx.navigateBack({
      fail: () => wx.switchTab({ url: "/pages/today/index" }),
    });
  },
});
