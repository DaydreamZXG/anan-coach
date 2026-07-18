import type {
  InferenceCandidateVersion,
  LearningBrainProfileVersion,
  StrategyCandidateCommand,
} from "../../api/generated/types.gen";
import { LearningBrainApi } from "../../api/learning-brain";
import {
  errorView,
  newIdempotencyKey,
  percent,
  requireAuthenticatedClient,
} from "../../features/learning-brain/presentation";

type StrategyDecisionType = StrategyCandidateCommand["decisionType"];

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    profile: null as LearningBrainProfileVersion | null,
    module: null as LearningBrainProfileVersion["modules"][number] | null,
    correctRateLabel: "证据不足",
    decisionType: "phase_deprioritize",
    rationale: "",
    reviewDate: "",
    effectiveDate: "",
    candidate: null as InferenceCandidateVersion | null,
    submitting: false,
    decisionMessage: "",
    queryTrack: "",
    queryModuleKey: "",
    retryAllowed: false,
  },

  onLoad(options: Record<string, string | undefined>) {
    this.setData({
      queryTrack: options.track?.trim() ?? "",
      queryModuleKey: decodedRouteValue(options.moduleKey),
    });
    void this.loadModule();
  },

  async loadModule() {
    const track = this.data.queryTrack;
    const moduleKey = this.data.queryModuleKey;
    if ((track !== "xingce" && track !== "essay") || !moduleKey) {
      this.setData({
        status: "error",
        errorMessage: "模块地址缺少轨道或模块标识。",
        retryAllowed: false,
      });
      return;
    }
    const client = requireAuthenticatedClient();
    if (!client) return;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const { data } = await new LearningBrainApi(client).getProfile();
      const module = data.modules.find(
        (item) => item.track === track && item.moduleKey === moduleKey,
      );
      if (!module) throw new Error("模块画像不存在");
      this.setData({
        status: "ready",
        profile: data,
        module,
        correctRateLabel: percent(module.correctRate),
        retryAllowed: false,
      });
    } catch (error) {
      const view = errorView(error, "模块画像加载失败，请重试。");
      this.setData({
        status: "error",
        errorMessage: view.message,
        requestId: view.requestId,
        retryAllowed: true,
      });
    }
  },

  retryLoad() {
    if (this.data.retryAllowed) void this.loadModule();
  },

  chooseDecisionType(event: WechatMiniprogram.BaseEvent) {
    this.setData({
      decisionType: String(event.currentTarget.dataset.type),
    });
  },
  inputRationale(event: WechatMiniprogram.Input) {
    this.setData({ rationale: event.detail.value });
  },
  chooseReviewDate(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ reviewDate: event.detail.value });
  },
  chooseEffectiveDate(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ effectiveDate: event.detail.value });
  },

  async createCandidate() {
    const client = requireAuthenticatedClient();
    const profile = this.data.profile;
    const module = this.data.module;
    if (!client || !profile || !module || this.data.submitting) return;
    if (!this.data.rationale.trim() || !this.data.reviewDate || !this.data.effectiveDate) {
      this.setData({ errorMessage: "请填写取舍理由，并明确复评日与失效日。" });
      return;
    }
    this.setData({ submitting: true, errorMessage: "", decisionMessage: "" });
    try {
      const { data } = await new LearningBrainApi(client).createStrategyCandidate(
        {
          basedOnProfileVersionId: profile.profileVersionId,
          decisionType: this.data.decisionType as StrategyDecisionType,
          scopeType: "module",
          scopeKey: module.moduleKey,
          rationale: this.data.rationale.trim(),
          reviewAt: new Date(`${this.data.reviewDate}T12:00:00+08:00`).toISOString(),
          effectiveUntil: new Date(`${this.data.effectiveDate}T23:59:00+08:00`).toISOString(),
        },
        newIdempotencyKey("strategy-candidate"),
      );
      this.setData({
        submitting: false,
        candidate: data.candidate,
        decisionMessage: "候选已生成，尚未改变计划或能力。",
      });
    } catch (error) {
      const view = errorView(error, "战略候选生成失败。");
      this.setData({ submitting: false, errorMessage: view.message, requestId: view.requestId });
    }
  },

  async decideCandidate(event: WechatMiniprogram.BaseEvent) {
    const action = String(event.currentTarget.dataset.action);
    const candidate = this.data.candidate;
    const client = requireAuthenticatedClient();
    if (!client || !candidate || (action !== "confirm" && action !== "reject")) return;
    this.setData({ submitting: true, errorMessage: "" });
    try {
      const { data } = await new LearningBrainApi(client).decideStrategyCandidate(
        candidate.candidateId,
        { basedOnCandidateVersionId: candidate.candidateVersionId, action },
        newIdempotencyKey("strategy-decision"),
      );
      this.setData({
        submitting: false,
        candidate: data.candidate,
        decisionMessage:
          action === "confirm"
            ? "已确认有期限的战略决定，届时必须复评。"
            : "已拒绝候选，不影响计划。",
      });
    } catch (error) {
      const view = errorView(error, "战略决定提交失败。");
      this.setData({ submitting: false, errorMessage: view.message, requestId: view.requestId });
    }
  },
});

function decodedRouteValue(value: string | undefined): string {
  const normalized = value?.trim() ?? "";
  if (!normalized) return "";
  try {
    return decodeURIComponent(normalized).trim();
  } catch {
    return "";
  }
}
