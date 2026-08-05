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
        errorMessage: "这个地址好像不对，回申论评阅再进一次就行。",
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
      if (!reviewVersionId) throw new Error("这道题还没有评阅依据呢。");
      const review = (await api.getGroundedReview(reviewVersionId)).data;
      this.setData({ status: "ready", review, view: reviewView(review), retryAllowed: false });
    } catch (error) {
      const view = errorView(error, "评阅依据没加载出来，别急，再试一次。");
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
