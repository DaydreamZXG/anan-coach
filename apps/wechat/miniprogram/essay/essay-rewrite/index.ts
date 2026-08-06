import { EssayIntelligenceApi, type EssayRewriteComparisonV1 } from "../shared/api/essay-intelligence";
import { routes } from "../../app/navigation/routes";
import { errorView, requireAuthenticatedClient } from "../../features/learning-brain/presentation";
import { rewriteComparisonView } from "../shared/features/essay-workbench/presentation";

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    comparison: null as EssayRewriteComparisonV1 | null,
    view: null as ReturnType<typeof rewriteComparisonView> | null,
    taskVersionId: "",
    rewriteVersionId: "",
    retryAllowed: false,
  },
  onLoad(options: Record<string, string | undefined>) {
    this.setData({
      taskVersionId: options.taskVersionId?.trim() ?? "",
      rewriteVersionId: options.rewriteVersionId?.trim() ?? "",
    });
    void this.loadComparison();
  },
  async loadComparison() {
    const taskVersionId = this.data.taskVersionId;
    if (!taskVersionId) {
      this.setData({
        status: "error",
        errorMessage: "这个地址好像不对，回申论工作台再进一次就行。",
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
      const rewriteVersionId = this.data.rewriteVersionId || workspace.progression.rewriteVersionId;
      if (!rewriteVersionId) throw new Error("这道题还没改写呢。");
      const comparison = (await api.getRewriteComparison(rewriteVersionId)).data;
      this.setData({
        status: "ready",
        comparison,
        view: rewriteComparisonView(comparison),
        retryAllowed: false,
      });
    } catch (error) {
      const view = errorView(error, "改写对照没加载出来，别急，再试一次。");
      this.setData({
        status: "error",
        errorMessage: view.message,
        requestId: view.requestId,
        retryAllowed: true,
      });
    }
  },
  retryLoad() {
    if (this.data.retryAllowed) void this.loadComparison();
  },
  goVerify() {
    void wx.switchTab({ url: routes.today });
  },
});
