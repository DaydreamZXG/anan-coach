import { PlanningApi } from "../../api/planning";
import type { DailyCapacityCommand, TodayDashboard } from "../../api/generated/types.gen";
import {
  errorView,
  newIdempotencyKey,
  requireAuthenticatedClient,
} from "../../features/learning-brain/presentation";

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    dashboard: null as TodayDashboard | null,
    minutes: "0",
    energy: 3,
    stress: 3,
    confidenceLevel: 3,
    options: [1, 2, 3, 4, 5],
    submitting: false,
    successMessage: "",
  },

  onLoad() {
    void this.loadToday();
  },

  async loadToday() {
    const client = requireAuthenticatedClient();
    if (!client) return;
    try {
      const { data } = await new PlanningApi(client).getToday();
      this.setData({
        status: "ready",
        dashboard: data,
        minutes: String(data.capacity.availableMinutes),
        energy: data.capacity.energy,
        stress: data.capacity.stress,
        confidenceLevel: data.capacity.confidenceLevel,
        errorMessage: "",
      });
    } catch (error) {
      const view = errorView(error, "今日容量加载失败，请重试。");
      this.setData({ status: "error", errorMessage: view.message, requestId: view.requestId });
    }
  },

  inputMinutes(event: WechatMiniprogram.Input) {
    this.setData({ minutes: event.detail.value });
  },

  chooseScale(event: WechatMiniprogram.BaseEvent) {
    const field = String(event.currentTarget.dataset.field ?? "");
    const value = Number(event.currentTarget.dataset.value);
    if (!Number.isInteger(value) || value < 1 || value > 5) return;
    if (field === "energy") this.setData({ energy: value });
    if (field === "stress") this.setData({ stress: value });
    if (field === "confidenceLevel") this.setData({ confidenceLevel: value });
  },

  markUnavailable() {
    this.setData({ minutes: "0", confidenceLevel: 1, successMessage: "" });
  },

  async submit() {
    const client = requireAuthenticatedClient();
    const dashboard = this.data.dashboard;
    if (!client || !dashboard || this.data.submitting) return;
    const availableMinutes = Number(this.data.minutes);
    if (!Number.isInteger(availableMinutes) || availableMinutes < 0 || availableMinutes > 1440) {
      this.setData({ errorMessage: "请输入 0–1440 之间的完整分钟数。" });
      return;
    }
    const command: DailyCapacityCommand = {
      basedOnCapacityVersionId: dashboard.capacity.capacityVersionId,
      availableMinutes,
      energy: this.data.energy,
      stress: this.data.stress,
      confidenceLevel: this.data.confidenceLevel,
    };
    this.setData({ submitting: true, errorMessage: "", successMessage: "" });
    try {
      const { data } = await new PlanningApi(client).setTodayCapacity(
        command,
        newIdempotencyKey("capacity"),
      );
      this.setData({
        submitting: false,
        dashboard: data.plan,
        successMessage: `已生成计划 v${String(data.plan.versionNo)}：${data.plan.headline}`,
      });
    } catch (error) {
      const view = errorView(error, "当日计划调整失败，请重新加载后再试。");
      this.setData({ submitting: false, errorMessage: view.message, requestId: view.requestId });
    }
  },

  retry() {
    void this.loadToday();
  },
});
