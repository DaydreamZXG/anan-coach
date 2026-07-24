import { LearningBrainApi } from "../../api/learning-brain";
import type { BrainEvidenceRef, ErrorPatternDetail } from "../../api/generated/types.gen";
import {
  dateTimeLabel,
  errorView,
  requireAuthenticatedClient,
} from "../../features/learning-brain/presentation";

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    detail: null as ErrorPatternDetail | null,
    latestLabel: "",
    patternStatusLabel: "",
    supportingEvidence: [] as Array<{ label: string; meta: string }>,
    patternId: "",
    retryAllowed: false,
  },
  onLoad(options: Record<string, string | undefined>) {
    this.setData({ patternId: options.patternId?.trim() ?? "" });
    void this.loadPattern();
  },
  async loadPattern() {
    const patternId = this.data.patternId;
    if (!patternId) {
      this.setData({
        status: "error",
        errorMessage: "错误模式地址缺少标识。",
        retryAllowed: false,
      });
      return;
    }
    const client = requireAuthenticatedClient();
    if (!client) return;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const { data } = await new LearningBrainApi(client).getErrorPattern(patternId);
      this.setData({
        status: "ready",
        detail: data,
        latestLabel: dateTimeLabel(data.pattern.latestObservedAt),
        patternStatusLabel: {
          confirmed: "已确认",
          counterevidence: "出现反向证据",
          retired: "已不再生效",
        }[data.pattern.status],
        supportingEvidence: data.supportingEvidence.map(evidenceView),
        retryAllowed: false,
      });
    } catch (error) {
      const view = errorView(error, "错误模式加载失败。");
      this.setData({
        status: "error",
        errorMessage: view.message,
        requestId: view.requestId,
        retryAllowed: true,
      });
    }
  },
  retryLoad() {
    if (this.data.retryAllowed) void this.loadPattern();
  },
});

function evidenceView(item: BrainEvidenceRef): { label: string; meta: string } {
  const label =
    item.role?.trim() ||
    {
      question_attempt: "真实作答记录",
      task_execution: "任务完成记录",
      reflection: "错题复盘",
      essay_review: "申论评阅",
      verification: "复测记录",
    }[item.entityType] ||
    "相关学习记录";
  return {
    label,
    meta: item.occurredAt ? dateTimeLabel(item.occurredAt) : "已纳入当前判断",
  };
}
