import { authedV2 } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import type { XingceTaskBriefing } from "../../api/generated/types.gen";

type ViewState = "loading" | "ready" | "error" | "expired";

/** 任务说明视图 */
interface BriefingView {
  title: string;
  reason: string;
  modeLabel: string;
  scopeLabel: string;
  questionCountLabel: string;
  targetTimeLabel: string;
  perQuestionTimeLabel: string;
  evidenceItems: Array<{ code: string; label: string }>;
  verificationStageLabel: string;
  validUntilLabel: string;
}

/** 验收证据口径（与旧版 presentation 一致） */
const METRIC_LABELS: Record<string, string> = {
  answer_verdict: "客观对错",
  confidence_evidence: "作答信心",
  timing_evidence: "单题用时",
  reflection: "用户原话复盘",
  cause_decision: "用户确认的错因决定",
};

Page({
  planTaskId: "",
  validUntilMs: 0,

  data: {
    statusBarHeight: 20,
    viewState: "loading" as ViewState,
    errorTitle: "",
    errorDesc: "",
    requestId: "",
    view: null as BriefingView | null,
    starting: false,
  },

  onLoad(options: Record<string, string | undefined>) {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    const planTaskId = options.planTaskId?.trim() ?? "";
    if (!planTaskId) {
      this.setData({
        viewState: "error",
        errorTitle: "任务说明地址不完整",
        errorDesc: "缺少明确的行测计划任务，请从今日页进入。",
      });
      return;
    }
    this.planTaskId = planTaskId;
    void this.loadBriefing();
  },

  async loadBriefing() {
    if (!this.planTaskId) return;
    this.setData({ viewState: "loading" });
    try {
      const briefing = await authedV2<XingceTaskBriefing>({
        method: "GET",
        path: `/api/v2/xingce/plan-tasks/${encodeURIComponent(this.planTaskId)}/briefing`,
      });
      this.validUntilMs = new Date(briefing.validUntil).getTime();
      if (this.isExpired()) {
        this.showExpired();
        return;
      }
      this.setData({ viewState: "ready", view: presentBriefing(briefing) });
    } catch (err) {
      const apiErr = err as ApiError;
      if (apiErr.status === 409) {
        // 任务已过期或不属于当前计划
        this.setData({
          viewState: "expired",
          errorTitle: "任务说明已经失效",
          errorDesc: "该任务已过期、内容已变化或已不属于当前计划，请返回今日获取新任务。",
          requestId: apiErr.requestId ?? "",
        });
        return;
      }
      this.setData({
        viewState: "error",
        errorTitle: "任务说明尚未载入",
        errorDesc: "请检查网络后重试。",
        requestId: apiErr.requestId ?? "",
      });
    }
  },

  isExpired(): boolean {
    return Number.isFinite(this.validUntilMs) && this.validUntilMs <= Date.now();
  },

  showExpired() {
    this.setData({
      viewState: "expired",
      errorTitle: "任务说明已到期",
      errorDesc: "请返回今日重新读取最新任务说明。",
    });
  },

  onRetry() {
    void this.loadBriefing();
  },

  onStart() {
    if (this.data.starting) return;
    if (this.isExpired()) {
      this.showExpired();
      return;
    }
    this.setData({ starting: true });
    wx.navigateTo({
      url: `/pages/practice/index?planTaskId=${encodeURIComponent(this.planTaskId)}`,
      complete: () => {
        this.setData({ starting: false });
      },
    });
  },

  onBack() {
    wx.navigateBack({
      fail: () => wx.switchTab({ url: "/pages/today/index" }),
    });
  },

  onReturnToday() {
    wx.switchTab({ url: "/pages/today/index" });
  },
});

/** 任务说明 → 视图（文案沿用旧版 presentation） */
function presentBriefing(briefing: XingceTaskBriefing): BriefingView {
  return {
    title: briefing.title,
    reason: briefing.reason,
    modeLabel:
      briefing.mode === "baseline"
        ? "受控基线"
        : briefing.mode === "verification"
          ? "独立复测"
          : "针对训练",
    scopeLabel: briefing.moduleName?.trim() || (briefing.mode === "baseline" ? "行测基线" : "行测"),
    questionCountLabel: `${String(briefing.questionCount ?? 3)} 题`,
    targetTimeLabel: formatDuration(briefing.timing.targetSeconds),
    perQuestionTimeLabel: `${formatDuration(briefing.timing.perQuestionTargetSeconds)}/题`,
    evidenceItems: briefing.completion.requiredMetrics.map((code) => ({
      code,
      label: METRIC_LABELS[code] ?? code,
    })),
    verificationStageLabel:
      briefing.verificationStage === "immediate"
        ? "即时变式验证"
        : briefing.verificationStage === "first_review"
          ? "1–3 天首次复测"
          : briefing.verificationStage === "delayed_review"
            ? "7–14 天延时验证"
            : "",
    validUntilLabel: `说明有效至 ${dateTimeLabel(briefing.validUntil)}`,
  };
}

function formatDuration(seconds: number): string {
  if (seconds >= 60 && seconds % 60 === 0) return `${String(seconds / 60)} 分钟`;
  if (seconds >= 60) {
    return `${String(Math.floor(seconds / 60))} 分 ${String(seconds % 60)} 秒`;
  }
  return `${String(seconds)} 秒`;
}

function dateTimeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const two = (n: number) => String(n).padStart(2, "0");
  return `${two(date.getMonth() + 1)}.${two(date.getDate())} ${two(date.getHours())}:${two(date.getMinutes())}`;
}
