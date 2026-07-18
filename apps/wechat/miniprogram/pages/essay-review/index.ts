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
    void this.loadReview();
  },
  async loadReview() {
    const taskVersionId = this.data.taskVersionId;
    if (!taskVersionId) {
      this.setData({
        status: "error",
        errorMessage: "评阅页地址缺少申论题目版本。",
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
      if (!reviewVersionId) throw new Error("当前题目还没有完整评阅版本。");
      const review = (await api.getGroundedReview(reviewVersionId)).data;
      this.setData({ status: "ready", review, view: reviewView(review), retryAllowed: false });
    } catch (error) {
      const view = errorView(error, "申论评阅加载失败，请重试。");
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
});
