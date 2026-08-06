import { EssayIntelligenceApi, type EssayReviewResponse } from "../shared/api/essay-intelligence";
import { routes } from "../../app/navigation/routes";
import { navigateBackOr } from "../shared/app/navigation/safe-back";
import { currentAuthSession } from "../../auth/session";
import { storeCompanionLaunchContext } from "../../features/companion/context-store";
import { errorView, requireAuthenticatedClient } from "../../features/learning-brain/presentation";
import { reviewView } from "../shared/features/essay-workbench/presentation";

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
        errorMessage: "这个地址好像不对，回申论作答页再进一次就行。",
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
      if (!reviewVersionId) throw new Error("这道题还没评完呢。");
      const review = (await api.getGroundedReview(reviewVersionId)).data;
      this.setData({
        status: "ready",
        review,
        view: reviewView(review),
        retryAllowed: false,
      });
    } catch (error) {
      const view = errorView(error, "评阅没加载出来，别急，再试一次。");
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
    navigateBackOr(routes.today);
  },

  rewriteAgain() {
    void wx.showToast({
      title: "改写要从今日计划进，马上带你过去。",
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
      routeId: "essay/essay-review/index",
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
