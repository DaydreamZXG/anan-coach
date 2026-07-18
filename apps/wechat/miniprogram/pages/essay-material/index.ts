import { EssayIntelligenceApi, type EssayTaskWorkspace } from "../../api/essay-intelligence";
import { errorView, requireAuthenticatedClient } from "../../features/learning-brain/presentation";
import { essayWriterUrl, materialDirectory } from "../../features/essay-workbench/presentation";

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    workspace: null as EssayTaskWorkspace | null,
    directory: [] as ReturnType<typeof materialDirectory>,
    annotations: [] as Array<{ segmentId: string; summary: string; evidenceQuote: string }>,
    writerUrl: "",
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
        errorMessage: "材料页地址缺少申论题目版本。",
        retryAllowed: false,
      });
      return;
    }
    const client = requireAuthenticatedClient();
    if (!client) return;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const response = await new EssayIntelligenceApi(client).getWorkspace(taskVersionId);
      const workspace = response.data;
      const artifact = workspace.currentArtifacts.material_annotation;
      const annotations =
        artifact?.payload.schemaType === "material_annotation"
          ? artifact.payload.annotations.map((item) => ({
              segmentId: item.segmentId,
              summary: item.paragraphSummary,
              evidenceQuote: item.evidenceQuote ?? "",
            }))
          : [];
      this.setData({
        status: "ready",
        workspace,
        directory: materialDirectory(workspace),
        annotations,
        writerUrl:
          essayWriterUrl("essay_material_reading", taskVersionId, this.data.planTaskId) ?? "",
        retryAllowed: false,
      });
    } catch (error) {
      const view = errorView(error, "材料工作台加载失败，请重试。");
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

  scrollToMaterial(event: WechatMiniprogram.BaseEvent) {
    const anchor = String(event.currentTarget.dataset.anchor ?? "");
    if (anchor) void wx.pageScrollTo({ selector: `#${anchor}`, duration: 240 });
  },

  openWriter() {
    if (this.data.writerUrl) void wx.navigateTo({ url: this.data.writerUrl });
  },
});
