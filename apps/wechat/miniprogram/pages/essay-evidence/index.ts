import { EssayIntelligenceApi, type EssayReviewResponse } from "../../api/essay-intelligence";
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
    void this.loadEvidence();
  },
  async loadEvidence() {
    const taskVersionId = this.data.taskVersionId;
    if (!taskVersionId) {
      this.setData({
        status: "error",
        errorMessage: "页面地址不完整，请从申论评阅重新进入。",
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
      if (!reviewVersionId) throw new Error("当前题目还没有可审计评阅证据。");
      const review = (await api.getGroundedReview(reviewVersionId)).data;
      this.setData({ status: "ready", review, view: reviewView(review), retryAllowed: false });
    } catch (error) {
      const view = errorView(error, "评阅证据加载失败，请重试。");
      this.setData({
        status: "error",
        errorMessage: view.message,
        requestId: view.requestId,
        retryAllowed: true,
      });
    }
  },
  retryLoad() {
    if (this.data.retryAllowed) void this.loadEvidence();
  },
});
