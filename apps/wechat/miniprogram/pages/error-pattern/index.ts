import { authedV2 } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import type { BrainEvidenceRef, ErrorPatternDetail } from "../../api/generated/types.gen";

type ViewState = "loading" | "ready" | "error";

/** 错误模式详情视图 */
interface PatternView {
  displayName: string;
  causeCode: string;
  moduleKey: string;
  trackLabel: string;
  statusLabel: string;
  statusClass: string;
  confidenceLabel: string;
  occurrenceCount: number;
  counterevidenceCount: number;
  latestLabel: string;
  interpretation: string;
  supportingEvidence: EvidenceView[];
  counterevidence: EvidenceView[];
  limitations: string[];
}

interface EvidenceView {
  key: string;
  entityType: string;
  meta: string;
  occurredLabel: string;
}

Page({
  patternId: "",

  data: {
    statusBarHeight: 20,
    viewState: "loading" as ViewState,
    errorDesc: "",
    requestId: "",
    detail: null as PatternView | null,
  },

  onLoad(options: Record<string, string | undefined>) {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    const patternId = options.patternId?.trim() ?? "";
    if (!patternId) {
      this.setData({
        viewState: "error",
        errorDesc: "错误模式地址缺少标识。",
      });
      return;
    }
    this.patternId = patternId;
    void this.loadPattern();
  },

  async loadPattern() {
    if (!this.patternId) return;
    this.setData({ viewState: "loading" });
    try {
      const detail = await authedV2<ErrorPatternDetail>({
        method: "GET",
        path: `/api/v2/learning-brain/error-patterns/${encodeURIComponent(this.patternId)}`,
      });
      this.setData({ viewState: "ready", detail: presentPattern(detail) });
    } catch (err) {
      const apiErr = err as ApiError;
      this.setData({
        viewState: "error",
        errorDesc: "错误模式没有加载成功，请检查网络后重试。",
        requestId: apiErr.requestId ?? "",
      });
    }
  },

  onRetry() {
    void this.loadPattern();
  },

  onBack() {
    wx.navigateBack({
      fail: () => wx.switchTab({ url: "/pages/training/index" }),
    });
  },
});

/** 错误模式 → 视图 */
function presentPattern(detail: ErrorPatternDetail): PatternView {
  const p = detail.pattern;
  return {
    displayName: p.displayName,
    causeCode: p.causeCode,
    moduleKey: p.moduleKey,
    trackLabel: p.track === "xingce" ? "行测" : "申论",
    statusLabel:
      p.status === "confirmed" ? "已确认" : p.status === "counterevidence" ? "有反证" : "已退役",
    statusClass:
      p.status === "confirmed"
        ? "confirmed"
        : p.status === "counterevidence"
          ? "counter"
          : "retired",
    confidenceLabel:
      p.confidence === "high"
        ? "高置信"
        : p.confidence === "medium"
          ? "中置信"
          : p.confidence === "low"
            ? "低置信"
            : "置信未知",
    occurrenceCount: p.occurrenceCount,
    counterevidenceCount: p.counterevidenceCount,
    latestLabel: dateTimeLabel(p.latestObservedAt),
    interpretation: detail.interpretation,
    supportingEvidence: detail.supportingEvidence.map(presentEvidence),
    counterevidence: detail.counterevidence.map(presentEvidence),
    limitations: [...detail.limitations],
  };
}

function presentEvidence(ref: BrainEvidenceRef): EvidenceView {
  return {
    key: `${ref.entityType}:${ref.entityId}:${ref.versionId ?? ""}`,
    entityType: ref.entityType,
    meta: [ref.owner, ref.versionId].filter(Boolean).join(" · "),
    occurredLabel: ref.occurredAt ? dateTimeLabel(ref.occurredAt) : "",
  };
}

function dateTimeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const two = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}.${two(date.getMonth() + 1)}.${two(date.getDate())}`;
}
