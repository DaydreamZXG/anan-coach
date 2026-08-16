import { LearningBrainApi } from "../../api/learning-brain";
import type { BrainEvidenceRef, ErrorPatternDetail } from "../../api/generated/types.gen";
import { routes } from "../../app/navigation/routes";
import { currentAuthSession } from "../../auth/session";
import { createIdempotencyKey } from "../../platform/command-id";
import { storeCompanionLaunchContext } from "../../features/companion/context-store";
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
    recheckBusy: false,
    recheckPending: false,
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
  goPractice() {
    void wx.navigateTo({
      url: "/xingce/xingce-mastery/index?bucket=due_verification",
    });
  },
  async markInaccurate() {
    const detail = this.data.detail;
    const client = requireAuthenticatedClient();
    if (!detail || !client || this.data.recheckBusy) return;
    this.setData({ recheckBusy: true });
    try {
      const commandId = await createIdempotencyKey("coach-recheck");
      await new LearningBrainApi(client).requestCoachRecheck({
        commandId,
        profileVersionId: detail.profileVersionId,
        patternId: detail.pattern.patternId,
        note: "我认为这条判断不准确，请用新的作答证据重新验证。",
      });
      this.setData({ recheckBusy: false, recheckPending: true });
      void wx.showToast({ title: "岸岸会用复测重新判断", icon: "none" });
    } catch (error) {
      const view = errorView(error, "这次复核请求没有保存，请刷新后再试。");
      this.setData({
        recheckBusy: false,
        errorMessage: view.message,
        requestId: view.requestId,
      });
    }
  },
  askAnan() {
    const auth = currentAuthSession();
    const detail = this.data.detail;
    if (!auth || !detail) return;
    storeCompanionLaunchContext(auth.userId, {
      sourceLabel: "错误模式详情",
      title: `错误模式 · ${detail.pattern.displayName}`,
      summary: [
        `已出现 ${String(detail.pattern.occurrenceCount)} 次`,
        `当前状态 ${this.data.patternStatusLabel}`,
        `最近一次 ${this.data.latestLabel}`,
        detail.interpretation,
      ].join("；"),
      suggestedQuestion:
        "这个错误模式我到底卡在哪一步？帮我指出触发条件，再给我一个下次做题时用得上的检查动作。",
      routeId: "reports/error-pattern/index",
      workspace: "profile",
      objectRefs: [
        {
          type: "error_pattern",
          id: detail.pattern.patternId,
          version: detail.profileVersionId,
        },
      ],
    });
    void wx.switchTab({ url: routes.anan });
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
