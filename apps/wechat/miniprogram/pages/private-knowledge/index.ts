import { ApiClient, ApiClientError } from "../../api/client";
import type { RecognitionRevision } from "../../api/external-learning-contracts";
import {
  PrivateKnowledgeApi,
  type PrivateCitation,
  type PrivateSourceSummary,
} from "../../api/private-knowledge";
import { currentAuthSession } from "../../auth/session";
import { routes } from "../../app/navigation/routes";
import type { LocalSourceArtifactFile } from "../../features/external-learning/contracts";
import { chooseQuestionImage, chooseQuestionPdf } from "../../features/external-learning/platform";
import { createIdempotencyKey } from "../../platform/command-id";

interface ReviewSpan {
  spanId: string;
  text: string;
  confidenceLabel: string;
  originalText: string;
}

interface PrivateSourceView extends PrivateSourceSummary {
  documentKindLabel: string;
  revisionLabel: string;
  createdAtLabel: string;
}

interface PrivateCitationView extends PrivateCitation {
  matchLabel: string;
}

interface PageData {
  status: "loading" | "ready" | "empty" | "error";
  sources: PrivateSourceView[];
  title: string;
  documentKindIndex: number;
  documentKinds: Array<{ value: "lecture" | "note" | "summary"; label: string }>;
  busy: boolean;
  uploadPercent: number;
  reviewRevision: RecognitionRevision | null;
  reviewSpans: ReviewSpan[];
  reviewArtifactVersionId: string;
  reviewFilename: string;
  importTarget: PrivateSourceView | null;
  query: string;
  searching: boolean;
  citations: PrivateCitationView[];
  importPanelOpen: boolean;
  errorMessage: string;
  requestId: string;
  backgroundProcessing: boolean;
  citationOpen: boolean;
  activeCitation: PrivateCitationView | null;
}

interface PageCustom {
  loadSources(): Promise<void>;
  inputTitle(event: WechatMiniprogram.Input): void;
  selectDocumentKind(event: WechatMiniprogram.PickerChange): void;
  chooseImage(): Promise<void>;
  chooseCameraNote(): Promise<void>;
  choosePdf(): Promise<void>;
  chooseMarkdown(): Promise<void>;
  pickMarkdown(importTarget: PrivateSourceView | null): Promise<void>;
  cancelRoundTrip(): void;
  uploadAndProcess(file: LocalSourceArtifactFile): Promise<void>;
  inputReviewSpan(event: WechatMiniprogram.Input): void;
  confirmReview(): Promise<void>;
  cancelReview(): void;
  prepareRoundTrip(event: WechatMiniprogram.BaseEvent): void;
  exportMarkdown(event: WechatMiniprogram.BaseEvent): Promise<void>;
  deleteSource(event: WechatMiniprogram.BaseEvent): void;
  confirmDelete(source: PrivateSourceSummary): Promise<void>;
  waitForDeletion(gateway: PrivateKnowledgeApi, deletionId: string): Promise<void>;
  inputQuery(event: WechatMiniprogram.Input): void;
  search(): Promise<void>;
  retry(): void;
  openImportPanel(): void;
  closeImportPanel(): void;
  currentDocumentKind(): "lecture" | "note" | "summary";
  sourceFromEvent(event: WechatMiniprogram.BaseEvent): PrivateSourceView | null;
  fail(error: unknown, fallback: string): void;
  runInBackground(): void;
  showProgress(): void;
  navigateToDetail(event: WechatMiniprogram.BaseEvent): void;
  prepareReplacement(sourceId: string): void;
  chooseReplacementImage(): Promise<void>;
  chooseReplacementPdf(): Promise<void>;
  chooseReplacementMarkdown(): Promise<void>;
  openCitation(event: WechatMiniprogram.BaseEvent): void;
  closeCitation(): void;
  goBack(): void;
  noop(): void;
}

function api(): PrivateKnowledgeApi | null {
  const auth = currentAuthSession();
  return auth ? new PrivateKnowledgeApi(new ApiClient(undefined, auth.accessToken)) : null;
}

Page<PageData, PageCustom>({
  data: {
    status: "loading",
    sources: [],
    title: "",
    documentKindIndex: 1,
    documentKinds: [
      { value: "lecture", label: "讲义" },
      { value: "note", label: "笔记" },
      { value: "summary", label: "知识总结" },
    ],
    busy: false,
    uploadPercent: 0,
    reviewRevision: null,
    reviewSpans: [],
    reviewArtifactVersionId: "",
    reviewFilename: "",
    importTarget: null,
    query: "",
    searching: false,
    citations: [],
    importPanelOpen: false,
    errorMessage: "",
    requestId: "",
    backgroundProcessing: false,
    citationOpen: false,
    activeCitation: null,
  },

  onShow() {
    void this.loadSources();
  },

  async loadSources() {
    const gateway = api();
    if (!gateway) {
      void wx.reLaunch({ url: "/pages/login/index" });
      return;
    }
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const result = await gateway.listSources();
      this.setData({
        status: result.sources.length ? "ready" : "empty",
        sources: result.sources.map(privateSourceView),
      });
    } catch (error) {
      this.fail(error, "私人资料库没有加载成功。");
    }
  },

  inputTitle(event: WechatMiniprogram.Input) {
    this.setData({ title: event.detail.value });
  },

  selectDocumentKind(event: WechatMiniprogram.PickerChange) {
    this.setData({ documentKindIndex: Number(event.detail.value) });
  },

  async chooseImage() {
    this.setData({ importTarget: null });
    const file = await chooseQuestionImage("album");
    if (file) await this.uploadAndProcess(file);
  },

  async chooseCameraNote() {
    this.setData({ importTarget: null });
    const file = await chooseQuestionImage("camera");
    if (file) await this.uploadAndProcess(file);
  },

  async choosePdf() {
    this.setData({ importTarget: null });
    const file = await chooseQuestionPdf();
    if (file) await this.uploadAndProcess(file);
  },

  async chooseMarkdown() {
    await this.pickMarkdown(null);
  },

  async chooseReplacementImage() {
    const file = await chooseQuestionImage("album");
    if (file) await this.uploadAndProcess(file);
  },

  async chooseReplacementPdf() {
    const file = await chooseQuestionPdf();
    if (file) await this.uploadAndProcess(file);
  },

  async chooseReplacementMarkdown() {
    await this.pickMarkdown(this.data.importTarget);
  },

  async pickMarkdown(importTarget: PrivateSourceView | null) {
    try {
      const result = await wx.chooseMessageFile({
        count: 1,
        type: "file",
        extension: ["md", "markdown"],
      });
      const selected = result.tempFiles[0];
      if (!selected) return;
      const file: LocalSourceArtifactFile = {
        localPath: selected.path,
        displayName: selected.name || "Obsidian 笔记.md",
        sourceKind: "notes_app_export",
        mimeType: "text/markdown",
        byteSize: selected.size,
        pageCount: null,
      };
      const documentKindIndex = importTarget
        ? this.data.documentKinds.findIndex((item) => item.value === importTarget.documentKind)
        : this.data.documentKindIndex;
      this.setData({
        importTarget,
        ...(importTarget
          ? {
              title: importTarget.title,
              documentKindIndex: documentKindIndex >= 0 ? documentKindIndex : 1,
            }
          : {}),
      });
      await this.uploadAndProcess(file);
    } catch (error) {
      const message = (error as { errMsg?: string }).errMsg ?? "";
      if (!message.includes("cancel")) {
        this.fail(error, "Markdown 文件选择失败。");
      }
    }
  },

  cancelRoundTrip() {
    if (this.data.busy) return;
    this.setData({ importTarget: null, title: "" });
  },

  async uploadAndProcess(file: LocalSourceArtifactFile) {
    const gateway = api();
    if (!gateway || this.data.busy) return;
    const title = this.data.title.trim() || file.displayName.replace(/\.[^.]+$/u, "");
    this.setData({ busy: true, uploadPercent: 0, backgroundProcessing: false, errorMessage: "", title });
    try {
      const uploaded = await gateway.upload(
        file,
        await createIdempotencyKey("private-source-upload"),
        (progress) => {
          this.setData({ uploadPercent: progress.percent });
        },
      );
      if (file.mimeType === "text/markdown") {
        await gateway.createSource(
          {
            artifactVersionId: uploaded.artifact.artifactVersionId,
            recognitionRevisionId: null,
            title,
            documentKind: this.currentDocumentKind(),
            ...(this.data.importTarget
              ? {
                  sourceId: this.data.importTarget.sourceId,
                  expectedVersionNo: this.data.importTarget.versionNo,
                }
              : {}),
          },
          await createIdempotencyKey("private-source-create"),
        );
        this.setData({ busy: false, title: "", importTarget: null, importPanelOpen: false });
        await this.loadSources();
        return;
      }
      const revision = await gateway.recognizePrinted(
        uploaded.artifact.artifactVersionId,
        await createIdempotencyKey("private-source-ocr"),
      );
      const reviewSpans = recognitionSpans(revision);
      if (!reviewSpans.length) throw new Error("没有识别到可逐段核对的文字。");
      this.setData({
        busy: false,
        reviewRevision: revision,
        reviewSpans,
        reviewArtifactVersionId: uploaded.artifact.artifactVersionId,
        reviewFilename: file.displayName,
      });
    } catch (error) {
      this.setData({ busy: false });
      this.fail(error, "资料导入没有完成。");
    }
  },

  inputReviewSpan(event: WechatMiniprogram.Input) {
    const index = Number(event.currentTarget.dataset.index ?? -1);
    if (index < 0 || index >= this.data.reviewSpans.length) return;
    const reviewSpans = this.data.reviewSpans.map((item, current) =>
      current === index ? { ...item, text: event.detail.value } : item,
    );
    this.setData({ reviewSpans });
  },

  async confirmReview() {
    const gateway = api();
    const revision = this.data.reviewRevision;
    if (!gateway || !revision || this.data.busy) return;
    if (this.data.reviewSpans.some((span) => !span.text.trim())) {
      this.setData({ errorMessage: "每一段识别文字都需要保留或修正，不能留空。" });
      return;
    }
    this.setData({ busy: true, errorMessage: "" });
    try {
      let current = revision;
      if (this.data.reviewSpans.some((span) => span.text !== span.originalText)) {
        current = await gateway.correctRecognition(
          revision,
          correctedPayload(revision, this.data.reviewSpans),
          await createIdempotencyKey("private-source-ocr-correct"),
        );
      }
      const confirmed = await gateway.confirmRecognition(
        current,
        await createIdempotencyKey("private-source-ocr-confirm"),
      );
      await gateway.createSource(
        {
          artifactVersionId: this.data.reviewArtifactVersionId,
          recognitionRevisionId: confirmed.recognitionRevisionId,
          title: this.data.title.trim() || this.data.reviewFilename,
          documentKind: this.currentDocumentKind(),
          ...(this.data.importTarget
            ? {
                sourceId: this.data.importTarget.sourceId,
                expectedVersionNo: this.data.importTarget.versionNo,
              }
            : {}),
        },
        await createIdempotencyKey("private-source-create"),
      );
      this.setData({
        busy: false,
        title: "",
        importTarget: null,
        importPanelOpen: false,
        reviewRevision: null,
        reviewSpans: [],
        reviewArtifactVersionId: "",
        reviewFilename: "",
      });
      await this.loadSources();
    } catch (error) {
      this.setData({ busy: false });
      this.fail(error, "文字核对结果没有保存成功。");
    }
  },

  cancelReview() {
    this.setData({
      reviewRevision: null,
      reviewSpans: [],
      reviewArtifactVersionId: "",
      reviewFilename: "",
      importTarget: null,
    });
  },

  prepareRoundTrip(event: WechatMiniprogram.BaseEvent) {
    const source = this.sourceFromEvent(event);
    if (!source) return;
    void this.pickMarkdown(source);
  },

  async exportMarkdown(event: WechatMiniprogram.BaseEvent) {
    const gateway = api();
    const source = this.sourceFromEvent(event);
    if (!gateway || !source || this.data.busy) return;
    this.setData({ busy: true, errorMessage: "" });
    try {
      const exported = await gateway.exportMarkdown(source.sourceId);
      const path = `${wx.env.USER_DATA_PATH}/${safeFilename(exported.filename)}`;
      wx.getFileSystemManager().writeFileSync(path, exported.content, "utf8");
      await wx.shareFileMessage({ filePath: path, fileName: exported.filename });
      this.setData({ busy: false });
    } catch (error) {
      this.setData({ busy: false });
      this.fail(error, "Markdown 导出失败。");
    }
  },

  deleteSource(event: WechatMiniprogram.BaseEvent) {
    const source = this.sourceFromEvent(event);
    if (!source || this.data.busy) return;
    wx.showModal({
      title: "彻底删除这份资料？",
      content: "原文件、识别文字和检索内容会一起删除，不能恢复。",
      confirmText: "彻底删除",
      confirmColor: "#C83F56",
      success: (result) => {
        if (result.confirm) void this.confirmDelete(source);
      },
    });
  },

  async confirmDelete(source: PrivateSourceSummary) {
    const gateway = api();
    if (!gateway) return;
    this.setData({ busy: true, errorMessage: "" });
    try {
      const deletion = await gateway.deleteSource(
        source.sourceId,
        await createIdempotencyKey("private-source-delete"),
      );
      await this.waitForDeletion(gateway, deletion.deletionRequestId);
      this.setData({ busy: false });
      await this.loadSources();
    } catch (error) {
      this.setData({ busy: false });
      this.fail(error, "资料删除没有完成。");
    }
  },

  async waitForDeletion(gateway: PrivateKnowledgeApi, deletionId: string) {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      const current = await gateway.deletionStatus(deletionId);
      if (current.state === "completed") return;
      if (current.state === "failed") throw new Error("删除任务失败，请稍后重试。");
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    throw new Error("删除任务仍在处理中，请稍后刷新查看。");
  },

  inputQuery(event: WechatMiniprogram.Input) {
    this.setData({ query: event.detail.value });
  },

  async search() {
    const gateway = api();
    const query = this.data.query.trim();
    if (!gateway || !query || this.data.searching) return;
    this.setData({ searching: true, errorMessage: "" });
    try {
      const result = await gateway.search(query);
      this.setData({
        searching: false,
        citations: result.citations.map((citation) => ({
          ...citation,
          matchLabel: `匹配 ${String(Math.round(citation.finalScore * 100))}%`,
        })),
      });
    } catch (error) {
      this.setData({ searching: false });
      this.fail(error, "私人资料检索失败。");
    }
  },

  retry() {
    void this.loadSources();
  },

  openImportPanel() {
    this.setData({ importPanelOpen: true });
  },

  closeImportPanel() {
    if (this.data.busy) return;
    this.setData({ importPanelOpen: false, importTarget: null });
  },

  currentDocumentKind() {
    return this.data.documentKinds[this.data.documentKindIndex]?.value ?? "note";
  },

  sourceFromEvent(event: WechatMiniprogram.BaseEvent) {
    const sourceId = String(event.currentTarget.dataset.sourceId ?? "");
    return this.data.sources.find((item) => item.sourceId === sourceId) ?? null;
  },

  fail(error: unknown, fallback: string) {
    const apiError = error instanceof ApiClientError ? error : null;
    this.setData({
      status: this.data.sources.length ? this.data.status : "error",
      errorMessage: apiError?.message ?? (error instanceof Error ? error.message : fallback),
      requestId: apiError?.requestId ?? "",
    });
  },

  runInBackground() {
    this.setData({ backgroundProcessing: true });
    wx.showToast({ title: "已转入后台阅读", icon: "success" });
  },

  showProgress() {
    this.setData({ backgroundProcessing: false });
  },

  openCitation(event) {
    const index = Number(event.currentTarget.dataset.index ?? -1);
    const citation = this.data.citations[index];
    if (!citation) return;
    this.setData({ citationOpen: true, activeCitation: citation });
  },

  closeCitation() {
    this.setData({ citationOpen: false, activeCitation: null });
  },

  goBack() {
    if (this.data.citationOpen) {
      this.closeCitation();
      return;
    }
    if (this.data.busy && !this.data.backgroundProcessing) {
      this.runInBackground();
      return;
    }
    void wx.navigateBack();
  },

  noop() {},

  navigateToDetail(event: WechatMiniprogram.BaseEvent) {
    const sourceId = String(event.currentTarget.dataset.sourceId ?? "");
    if (!sourceId) return;
    wx.navigateTo({
      url: `${routes.privateKnowledgeDetail}?id=${encodeURIComponent(sourceId)}`,
      events: {
        replaceSource: (payload: { sourceId?: string }) => {
          this.prepareReplacement(String(payload.sourceId ?? ""));
        },
      },
    });
  },

  prepareReplacement(sourceId) {
    const source = this.data.sources.find((item) => item.sourceId === sourceId);
    if (!source || this.data.busy) return;
    const documentKindIndex = this.data.documentKinds.findIndex(
      (item) => item.value === source.documentKind,
    );
    this.setData({
      importTarget: source,
      title: source.title,
      documentKindIndex: documentKindIndex >= 0 ? documentKindIndex : 1,
      errorMessage: "",
    });
    wx.showActionSheet({
      itemList: ["图片", "PDF", "Markdown"],
      success: (result) => {
        if (result.tapIndex === 0) void this.chooseReplacementImage();
        else if (result.tapIndex === 1) void this.chooseReplacementPdf();
        else if (result.tapIndex === 2) void this.chooseReplacementMarkdown();
      },
    });
  },
});

function recognitionSpans(revision: RecognitionRevision): ReviewSpan[] {
  const raw = revision.candidatePayload.textSpans;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const span = item as Record<string, unknown>;
    const text = typeof span.text === "string" ? span.text.trim() : "";
    const spanId = typeof span.spanId === "string" ? span.spanId : "";
    const confidence = typeof span.confidence === "number" ? span.confidence : 0;
    if (!text || !spanId) return [];
    return [
      {
        spanId,
        text,
        originalText: text,
        confidenceLabel: confidence < 0.8 ? "低置信，必须核对" : "待确认",
      },
    ];
  });
}

function privateSourceView(source: PrivateSourceSummary): PrivateSourceView {
  return {
    ...source,
    documentKindLabel: {
      lecture: "讲义",
      note: "笔记",
      summary: "知识总结",
    }[source.documentKind],
    revisionLabel: source.versionNo > 1 ? `第 ${String(source.versionNo)} 次更新` : "首次导入",
    createdAtLabel: formatDate(source.createdAt),
  };
}

function correctedPayload(
  revision: RecognitionRevision,
  spans: ReviewSpan[],
): Record<string, unknown> {
  const payload = JSON.parse(JSON.stringify(revision.candidatePayload)) as Record<string, unknown>;
  const values = new Map(spans.map((span) => [span.spanId, span.text.trim()]));
  if (!Array.isArray(payload.textSpans)) throw new Error("识别结果缺少可核对的文字段。");
  const textSpans = payload.textSpans as unknown[];
  payload.textSpans = textSpans.map((item): unknown => {
    if (!item || typeof item !== "object") return item;
    const span = item as Record<string, unknown>;
    const spanId = typeof span.spanId === "string" ? span.spanId : "";
    const originalText = typeof span.text === "string" ? span.text : "";
    return { ...span, text: values.get(spanId) ?? originalText };
  });
  return payload;
}

function safeFilename(value: string): string {
  return value.replace(/[\\/:*?"<>|]/gu, "-").slice(0, 120) || "岸岸私人资料.md";
}

function formatDate(value: string): string {
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})/u);
  return match ? `${match[1]}.${match[2]}.${match[3]}` : value;
}
