import { CompanionApi } from "../../api/companion";
import { ApiClientError } from "../../api/client";
import type { CompanionAskResponse } from "../../api/generated/types.gen";
import { LearningBrainApi } from "../../api/learning-brain";
import { resolveCompanionAction } from "../../features/learning-brain/companion-action";
import {
  errorView,
  newIdempotencyKey,
  requireAuthenticatedClient,
} from "../../features/learning-brain/presentation";

type CompanionAnswerView = CompanionAskResponse & {
  quotaRemaining: number;
  stateLabel: string;
  confidenceLabel: string;
};

const stateLabels: Record<CompanionAskResponse["semanticState"], string> = {
  ready: "已根据当前证据回答",
  evidence_insufficient: "证据不足",
  permission_denied: "当前页面无权回答",
  security_blocked: "已阻断不安全请求",
  quota_exhausted: "今日额度已用完",
  provider_pending: "回答结果待安全确认",
  failed: "本次回答未完成",
};

Page({
  data: {
    contextStatus: "loading",
    learnerStateVersion: "",
    question: "",
    submitting: false,
    errorMessage: "",
    requestId: "",
    semanticState: "ready",
    stateLabel: "正在读取学习证据",
    companionIntent: "companion",
    answer: null as CompanionAnswerView | null,
  },
  onLoad(options: Record<string, string | undefined>) {
    const question = options.question?.trim() ?? "";
    this.setData({
      question: question.slice(0, 800),
    });
    void this.loadContext();
  },
  async loadContext() {
    const client = requireAuthenticatedClient();
    if (!client) return;
    this.setData({ contextStatus: "loading", errorMessage: "", requestId: "" });
    try {
      const { data } = await new LearningBrainApi(client).getProfile();
      this.setData({
        contextStatus: "ready",
        learnerStateVersion: data.profileVersionId,
        stateLabel: "可以问当前学习、计划或岗位问题",
      });
    } catch (error) {
      const view = errorView(error, "学习证据加载失败，暂时不能提交问题。");
      this.setData({
        contextStatus: "error",
        semanticState: "failed",
        stateLabel: stateLabels.failed,
        errorMessage: view.message,
        requestId: view.requestId,
        companionIntent: "reminder",
      });
    }
  },
  inputQuestion(event: WechatMiniprogram.Input) {
    this.setData({ question: event.detail.value });
  },
  async ask() {
    const client = requireAuthenticatedClient();
    if (!client || this.data.submitting || this.data.contextStatus !== "ready") return;
    const question = this.data.question.trim();
    if (question.length < 4) {
      this.setData({ errorMessage: "请至少写 4 个字，让岸岸知道你在问什么。" });
      return;
    }
    this.setData({
      submitting: true,
      errorMessage: "",
      requestId: "",
      answer: null,
      companionIntent: "thinking",
      stateLabel: "正在检查权限并检索当前证据",
    });
    try {
      const { data } = await new CompanionApi(client).ask(
        {
          questionText: question,
          context: {
            routeId: "pages/companion/index",
            workspace: "training",
            objectRefs: [],
            learnerStateVersion: this.data.learnerStateVersion,
          },
        },
        newIdempotencyKey("companion"),
      );
      this.setData({
        submitting: false,
        semanticState: data.semanticState,
        stateLabel: stateLabels[data.semanticState],
        companionIntent: data.companionIntent,
        answer: {
          ...data,
          quotaRemaining: Math.max(0, data.quota.limit - data.quota.used),
          stateLabel: stateLabels[data.semanticState],
          confidenceLabel: { low: "低置信", medium: "中置信", high: "高置信" }[data.confidence],
        },
      });
    } catch (error) {
      const semanticState = failureState(error);
      const view = errorView(error, "本次回答未完成，请根据状态稍后再试。");
      this.setData({
        submitting: false,
        semanticState,
        stateLabel: stateLabels[semanticState],
        companionIntent: "reminder",
        errorMessage: view.message,
        requestId: view.requestId,
      });
    }
  },
  retryContext() {
    void this.loadContext();
  },
  handleAction(event: WechatMiniprogram.BaseEvent) {
    const actionIndex = Number(event.currentTarget.dataset.index);
    const action =
      Number.isSafeInteger(actionIndex) && actionIndex >= 0
        ? this.data.answer?.allowedActions[actionIndex]
        : undefined;
    if (!action) return;
    const resolution = resolveCompanionAction(action);
    if (resolution.kind === "show_evidence") {
      void wx.showToast({ title: "依据已列在回答下方", icon: "none" });
      return;
    }
    if (resolution.kind === "refresh_context") {
      this.setData({ answer: null });
      void this.loadContext();
      return;
    }
    if (resolution.kind === "unavailable") {
      void wx.showToast({ title: "这个操作暂时不可用，请刷新后重试", icon: "none" });
      return;
    }
    const navigate = () => {
      if (resolution.target.mode === "switchTab") {
        void wx.switchTab({ url: resolution.target.url });
      } else {
        void wx.navigateTo({ url: resolution.target.url });
      }
    };
    if (resolution.requiresConfirmation) {
      wx.showModal({
        title: "打开计划调整",
        content: "岸岸的建议不会自动修改计划，需要你再确认。",
        success: ({ confirm }) => {
          if (confirm) navigate();
        },
      });
      return;
    }
    navigate();
  },
});

function failureState(error: unknown): CompanionAskResponse["semanticState"] {
  if (!(error instanceof ApiClientError)) return "failed";
  if (error.statusCode === 429) return "quota_exhausted";
  if (error.statusCode === 403) return "permission_denied";
  if (error.statusCode === 502 || error.statusCode === 503) return "provider_pending";
  return "failed";
}
