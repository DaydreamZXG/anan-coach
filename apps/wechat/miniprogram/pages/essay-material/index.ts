import { EssayIntelligenceApi, type EssayTaskWorkspace } from "../../api/essay-intelligence";
import { routes } from "../../app/navigation/routes";
import { navigateBackOr } from "../../app/navigation/safe-back";
import { errorView, requireAuthenticatedClient } from "../../features/learning-brain/presentation";
import { materialDirectory } from "../../features/essay-workbench/presentation";
import { materialTextParagraphs } from "../../features/essay-task/material-paragraphs";

type MaterialParagraph = EssayTaskWorkspace["material"]["segments"][number] & {
  anchorId: string;
  paragraphNumber: number;
  displayParagraphId: string;
  showAnnotation: boolean;
};

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    workspace: null as EssayTaskWorkspace | null,
    directory: [] as ReturnType<typeof materialDirectory>,
    segments: [] as MaterialParagraph[],
    annotations: [] as Array<{ segmentId: string; summary: string; evidenceQuote: string }>,
    taskVersionId: "",
    targetAnchor: "",
    activeMaterialLabel: "材料",
    selectedSegmentId: "",
    selectedParagraphId: "",
    selectedText: "",
    insertMode: false,
    retryAllowed: false,
  },

  onLoad(options: Record<string, string | undefined>) {
    this.setData({
      taskVersionId: options.taskVersionId?.trim() ?? "",
      targetAnchor: options.anchor?.trim() ?? "",
      insertMode: options.insertMode === "drafting",
    });
    void this.loadWorkspace();
  },

  async loadWorkspace() {
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
      const response = await new EssayIntelligenceApi(client).getWorkspace(taskVersionId);
      const workspace = response.data;
      const directory = materialDirectory(workspace);
      const artifact = workspace.currentArtifacts.material_annotation;
      const annotations =
        artifact?.payload.schemaType === "material_annotation"
          ? artifact.payload.annotations.map((item) => ({
              segmentId: item.segmentId,
              summary: item.paragraphSummary,
              evidenceQuote: item.evidenceQuote ?? "",
            }))
          : [];
      let paragraphNumber = 0;
      const materialAnchors = new Set<number>();
      const segments = workspace.material.segments.flatMap((segment) => {
        const paragraphs = materialTextParagraphs(segment.text, segment.segmentId);
        return paragraphs.map((paragraph, index) => {
          paragraphNumber += 1;
          const firstForMaterial = !materialAnchors.has(segment.materialOrder);
          if (firstForMaterial) materialAnchors.add(segment.materialOrder);
          return {
            ...segment,
            text: paragraph.text,
            paragraphNumber,
            displayParagraphId: paragraph.paragraphId,
            showAnnotation: index === paragraphs.length - 1,
            anchorId: firstForMaterial
              ? `material-${String(segment.materialOrder)}`
              : paragraph.paragraphId,
          };
        });
      });
      const targetDirectory = directory.find(
        (item) => item.firstAnchorId === this.data.targetAnchor,
      );
      const targetMaterialOrder =
        targetDirectory?.materialOrder ??
        parseMaterialOrder(this.data.targetAnchor) ??
        directory[0]?.materialOrder;
      this.setData({
        status: "ready",
        workspace,
        directory,
        segments,
        annotations,
        activeMaterialLabel:
          targetMaterialOrder === undefined
            ? "材料"
            : `材料 ${String(targetMaterialOrder)} / ${String(directory.length)}`,
        retryAllowed: false,
      });
      if (this.data.targetAnchor) {
        wx.nextTick(() => {
          void wx.pageScrollTo({ selector: `#${this.data.targetAnchor}`, duration: 240 });
        });
      }
    } catch (error) {
      const view = errorView(error, "材料没有加载成功，请重试。");
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
    const materialOrder = Number(event.currentTarget.dataset.materialOrder ?? 0);
    if (!anchor) return;
    this.setData({
      activeMaterialLabel: `材料 ${String(materialOrder)} / ${String(this.data.directory.length)}`,
      selectedSegmentId: "",
      selectedParagraphId: "",
      selectedText: "",
    });
    void wx.pageScrollTo({ selector: `#${anchor}`, duration: 240 });
  },

  selectSegment(event: WechatMiniprogram.BaseEvent) {
    const segmentId = String(event.currentTarget.dataset.segmentId ?? "");
    const displayParagraphId = String(event.currentTarget.dataset.paragraphId ?? "");
    const text = String(event.currentTarget.dataset.text ?? "").trim();
    const materialOrder = Number(event.currentTarget.dataset.materialOrder ?? 0);
    if (!segmentId || !displayParagraphId || !text) return;
    this.setData({
      selectedSegmentId: segmentId,
      selectedParagraphId: displayParagraphId,
      selectedText: text,
      activeMaterialLabel: `材料 ${String(materialOrder)} / ${String(this.data.directory.length)}`,
    });
  },

  clearSelection() {
    this.setData({ selectedSegmentId: "", selectedParagraphId: "", selectedText: "" });
  },

  copySelection() {
    const text = this.data.selectedText;
    if (!text) return;
    void wx.setClipboardData({ data: text });
  },

  addSelectionToOutline() {
    this.emitSelection("addMaterialToOutline");
  },

  quoteSelection() {
    this.emitSelection("quoteMaterial");
  },

  emitSelection(eventName: "addMaterialToOutline" | "quoteMaterial") {
    const text = this.data.selectedText;
    if (!text || !this.data.insertMode) return;
    const eventChannel = this.getOpenerEventChannel();
    if (!eventChannel.emit) {
      void wx.showToast({
        title: "请返回作答页后重新打开材料",
        icon: "none",
      });
      return;
    }
    eventChannel.emit(eventName, { text });
    navigateBackOr(routes.today);
  },

  returnToWriter() {
    navigateBackOr(routes.today);
  },
});

function parseMaterialOrder(anchor: string): number | null {
  const match = /^material-(\d+)$/u.exec(anchor);
  if (!match?.[1]) return null;
  const value = Number(match[1]);
  return Number.isInteger(value) && value > 0 ? value : null;
}
