import { EssayIntelligenceApi, type EssayReviewResponse } from "../../api/essay-intelligence";
import { routes } from "../../app/navigation/routes";
import { currentAuthSession } from "../../auth/session";
import { storeCompanionLaunchContext } from "../../features/companion/context-store";
import { errorView, requireAuthenticatedClient } from "../../features/learning-brain/presentation";
import { reviewView } from "../../features/essay-workbench/presentation";

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    review: null as EssayReviewResponse | null,
    view: null as ReturnType<typeof reviewView> | null,
    taskVersionId: "",
    reviewVersionId: "",
    retryAllowed: false,
  },

  onLoad(options: Record<string, string | undefined>) {
    this.setData({
      taskVersionId: options.taskVersionId?.trim() ?? "",
      reviewVersionId: options.reviewVersionId?.trim() ?? "",
    });
    void this.loadReview();
  },

  async loadReview() {
    const taskVersionId = this.data.taskVersionId;
    if (!taskVersionId) {
      this.setData({
        status: "error",
        errorMessage: "页面地址不完整，请从申论作答页重新进入。",
        retryAllowed: false,
      });
      return;
    }
    const client = requireAuthenticatedClient();
    if (!client) return;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const api = new EssayIntelligenceApi(client);
      const workspace = (await api.getWorkspace(taskVersionId)).data;
      const reviewVersionId = this.data.reviewVersionId || workspace.progression.reviewVersionId;
      if (!reviewVersionId) throw new Error("当前题目还没有完成评阅。");
      const review = (await api.getGroundedReview(reviewVersionId)).data;
      this.setData({
        status: "ready",
        review,
        view: reviewView(review),
        retryAllowed: false,
      });
    } catch (error) {
      const view = errorView(error, "申论评阅没有加载成功，请重试。");
      this.setData({
        status: "error",
        errorMessage: view.message,
        requestId: view.requestId,
        retryAllowed: true,
      });
    }
  },

  retryLoad() {
    if (this.data.retryAllowed) void this.loadReview();
  },

  exit() {
    void wx.navigateBack();
  },

  rewriteAgain() {
    void wx.showToast({
      title: "请从今日计划进入改写任务",
      icon: "none",
    });
    void wx.switchTab({ url: routes.today });
  },

  askAnan() {
    const auth = currentAuthSession();
    if (!auth) {
      void wx.reLaunch({ url: routes.login });
      return;
    }
    const review = this.data.review;
    const view = this.data.view;
    if (!review || !view) return;
    storeCompanionLaunchContext(auth.userId, {
      sourceLabel: "申论 AI 批改复盘",
      title: "这道题最值得先改什么",
      summary: view.scoreAvailable
        ? `建议观察分 ${view.recommendedScore} / ${view.maximumScore}，覆盖 ${String(view.coveredPoints)} / ${String(view.totalPoints)} 个要点，${view.confidenceLabel}。`
        : `本次没有形成可信分数区间，覆盖 ${String(view.coveredPoints)} / ${String(view.totalPoints)} 个要点。`,
      suggestedQuestion:
        "结合本题材料、我的答案和评阅证据，告诉我最应该先改哪一处，并明确对应的答案原句和材料依据。",
      routeId: "pages/essay-review/index",
      workspace: "training",
      objectRefs: [
        {
          type: "attempt",
          id: review.attemptId,
          version: review.reviewVersionId,
        },
        {
          type: "question",
          id: review.taskVersionId,
          version: review.taskVersionId,
        },
      ],
      taskMode: "essay",
      taskStage: "reviewing",
    });
    void wx.switchTab({ url: routes.anan });
  },
});
