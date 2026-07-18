import { EssayIntelligenceApi, type EssayTaskWorkspace } from "../../api/essay-intelligence";
import { EssayHandwritingApi } from "../../api/essay-handwriting";
import { errorView, requireAuthenticatedClient } from "../../features/learning-brain/presentation";
import {
  answerSummary,
  essayWriterUrl,
  outlineViews,
  pointViews,
} from "../../features/essay-workbench/presentation";

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    workspace: null as EssayTaskWorkspace | null,
    points: [] as ReturnType<typeof pointViews>,
    outline: [] as ReturnType<typeof outlineViews>,
    answer: null as ReturnType<typeof answerSummary>,
    outlineUrl: "",
    draftingUrl: "",
    originalArtifactVersionId: "",
    openingOriginal: false,
    taskVersionId: "",
    planTaskId: "",
    retryAllowed: false,
  },
  onLoad(options: Record<string, string | undefined>) {
    this.setData({
      taskVersionId: options.taskVersionId?.trim() ?? "",
      planTaskId: options.planTaskId?.trim() ?? "",
    });
    void this.loadWorkspace();
  },
  async loadWorkspace() {
    const taskVersionId = this.data.taskVersionId;
    if (!taskVersionId) {
      this.setData({
        status: "error",
        errorMessage: "作答工作台地址缺少申论题目版本。",
        retryAllowed: false,
      });
      return;
    }
    const client = requireAuthenticatedClient();
    if (!client) return;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const workspace = (await new EssayIntelligenceApi(client).getWorkspace(taskVersionId)).data;
      const planTaskId = this.data.planTaskId;
      this.setData({
        status: "ready",
        workspace,
        points: pointViews(workspace),
        outline: outlineViews(workspace),
        answer: answerSummary(workspace),
        outlineUrl: essayWriterUrl("essay_outlining", taskVersionId, planTaskId) ?? "",
        draftingUrl: essayWriterUrl("essay_drafting", taskVersionId, planTaskId) ?? "",
        originalArtifactVersionId:
          workspace.progression.currentAnswer?.sourceArtifactVersionId ?? "",
        retryAllowed: false,
      });
    } catch (error) {
      const view = errorView(error, "申论作答工作台加载失败，请重试。");
      this.setData({
        status: "error",
        errorMessage: view.message,
        requestId: view.requestId,
        retryAllowed: true,
      });
    }
  },
  retryLoad() {
    if (this.data.retryAllowed) void this.loadWorkspace();
  },
  openOutline() {
    if (this.data.outlineUrl) void wx.navigateTo({ url: this.data.outlineUrl });
  },
  openDrafting() {
    if (this.data.draftingUrl) void wx.navigateTo({ url: this.data.draftingUrl });
  },
  async openOriginal() {
    const client = requireAuthenticatedClient();
    const artifactVersionId = this.data.originalArtifactVersionId;
    if (!client || !artifactVersionId || this.data.openingOriginal) return;
    this.setData({ openingOriginal: true });
    try {
      const access = await new EssayHandwritingApi(client).getArtifactAccess(artifactVersionId);
      await wx.previewImage({ current: access.imageUrl, urls: [access.imageUrl] });
    } catch (error) {
      const view = errorView(error, "申论手写原图暂时无法打开，请重试。");
      void wx.showToast({ title: view.message, icon: "none", duration: 3000 });
    } finally {
      this.setData({ openingOriginal: false });
    }
  },
});
