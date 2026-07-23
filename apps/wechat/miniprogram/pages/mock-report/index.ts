import { authedRequest, idemKey } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import type { MockExamReview, MockExamSession } from "../../api/generated/types.gen";

type ViewState = "loading" | "ready" | "error" | "unsubmitted";

interface ModuleView {
  moduleName: string;
  correct: number;
  total: number;
  accuracyPercent: number;
  changeLabel: string;
  changeClass: "up" | "down" | "flat";
}

interface ClusterView {
  nodeId: string;
  displayPath: string;
  wrongCount: number;
  fixAction: string;
}

interface MockReportPageData {
  statusBarHeight: number;
  viewState: ViewState;
  errorTitle: string;
  errorDesc: string;
  requestId: string;
  sessionId: string;
  performanceIndex: number;
  rangeLower: number;
  rangeUpper: number;
  estimatedScore: number;
  estimateSource: string;
  metricDefinition: string;
  intervalDefinition: string;
  submittedAtLabel: string;
  modules: ModuleView[];
  clusters: ClusterView[];
  nextWeekReason: string;
}

interface MockReportPageCustom {
  loadReport(): Promise<void>;
  retry(): void;
  goExam(): void;
  openReviewCenter(): void;
  returnToday(): void;
  goBack(): void;
}

/** 模考报告：GET /api/v1/mock-exams/{id}/review；未读时 POST .../review/view 标记已读 */
Page<MockReportPageData, MockReportPageCustom>({
  data: {
    statusBarHeight: 20,
    viewState: "loading",
    errorTitle: "",
    errorDesc: "",
    requestId: "",
    sessionId: "",
    performanceIndex: 0,
    rangeLower: 0,
    rangeUpper: 0,
    estimatedScore: 0,
    estimateSource: "",
    metricDefinition: "",
    intervalDefinition: "",
    submittedAtLabel: "",
    modules: [],
    clusters: [],
    nextWeekReason: "",
  },

  onLoad(options: Record<string, string | undefined>) {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({
      statusBarHeight: app.globalData.statusBarHeight,
      sessionId: options.sessionId?.trim() ?? "",
    });
    void this.loadReport();
  },

  async loadReport() {
    this.setData({ viewState: "loading", errorTitle: "", errorDesc: "", requestId: "" });
    try {
      // 未带 sessionId 时回落到本周模考（POST weekly 幂等返回同一场）
      let sessionId = this.data.sessionId;
      if (!sessionId) {
        const session = await apiCreateWeekly();
        sessionId = session.sessionId;
        if (session.status === "assigned") {
          this.setData({ viewState: "unsubmitted", sessionId });
          return;
        }
        this.setData({ sessionId });
      }
      const review = await apiGetReview(sessionId);
      this.setData({
        viewState: "ready",
        performanceIndex: review.performanceIndex,
        rangeLower: review.performanceRange.lower,
        rangeUpper: review.performanceRange.upper,
        estimatedScore: review.estimatedScore,
        estimateSource: review.estimateSource,
        metricDefinition: review.metricDefinition,
        intervalDefinition: review.intervalDefinition,
        submittedAtLabel: formatSubmittedAt(review.submittedAt),
        modules: review.modules.map((item) => ({
          moduleName: item.moduleName,
          correct: item.correct,
          total: item.total,
          accuracyPercent: Math.round(item.accuracy * 100),
          changeLabel: changeLabel(item.changeFromPrevious),
          changeClass: changeClass(item.changeFromPrevious),
        })),
        clusters: review.wrongClusters.map((item) => ({
          nodeId: item.nodeId,
          displayPath: item.displayPath,
          wrongCount: item.wrongCount,
          fixAction: item.fixAction,
        })),
        nextWeekReason: review.nextWeekReason,
      });
      // 已读标记失败不影响阅读，静默重试一次即可
      if (!review.viewed) void markViewed(sessionId);
    } catch (error) {
      const apiError = error instanceof ApiError ? error : null;
      // 未交卷时服务端没有 review，引导回考场
      if (apiError && (apiError.status === 404 || apiError.status === 409)) {
        this.setData({ viewState: "unsubmitted" });
        return;
      }
      this.setData({
        viewState: "error",
        errorTitle: "报告加载失败",
        errorDesc: error instanceof Error && error.message.trim() ? error.message : "请稍后重试。",
        requestId: apiError?.requestId ?? "",
      });
    }
  },

  retry() {
    void this.loadReport();
  },

  goExam() {
    wx.redirectTo({
      url: `/pages/mock-exam/index?sessionId=${encodeURIComponent(this.data.sessionId)}`,
      fail: () => wx.navigateTo({ url: "/pages/mock-exam/index" }),
    });
  },

  openReviewCenter() {
    wx.navigateTo({ url: "/pages/review/index" });
  },

  returnToday() {
    wx.switchTab({ url: "/pages/today/index" });
  },

  goBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/today/index" }) });
  },
});

function changeLabel(change: number | null | undefined): string {
  if (change === null || change === undefined || change === 0) return "持平";
  const percent = Math.round(Math.abs(change) * 100);
  return change > 0 ? `较上次 +${percent}%` : `较上次 -${percent}%`;
}

function changeClass(change: number | null | undefined): "up" | "down" | "flat" {
  if (change === null || change === undefined || change === 0) return "flat";
  return change > 0 ? "up" : "down";
}

function formatSubmittedAt(iso: string): string {
  const time = Date.parse(iso);
  if (!Number.isFinite(time)) return "";
  const date = new Date(time);
  return `${date.getMonth() + 1}月${date.getDate()}日 ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")} 交卷`;
}

// ═══════ API（v1 裸响应） ═══════

function apiCreateWeekly(): Promise<MockExamSession> {
  return authedRequest<MockExamSession>({
    method: "POST",
    path: "/api/v1/mock-exams/weekly",
    body: {},
    idempotencyKey: idemKey(),
  });
}

function apiGetReview(sessionId: string): Promise<MockExamReview> {
  return authedRequest<MockExamReview>({
    method: "GET",
    path: `/api/v1/mock-exams/${encodeURIComponent(sessionId)}/review`,
  });
}

async function markViewed(sessionId: string): Promise<void> {
  try {
    await authedRequest<MockExamReview>({
      method: "POST",
      path: `/api/v1/mock-exams/${encodeURIComponent(sessionId)}/review/view`,
      body: {},
      idempotencyKey: idemKey(),
    });
  } catch {
    // 已读标记失败不打断阅读
  }
}
