import { ApiClient, ApiClientError } from "../../api/client";
import type { RecognitionRevision } from "../../api/external-learning-contracts";
import {
  PrivateKnowledgeApi,
  type PrivateCitation,
  type PrivateSourceSummary,
} from "./features/api/private-knowledge";
import { currentAuthSession } from "../../auth/session";
import { routes } from "../../app/navigation/routes";
import { navigateBackOr } from "./shared/app/navigation/safe-back";
import type { LocalSourceArtifactFile } from "../../features/external-learning/contracts";
import {
  chooseQuestionImage,
  chooseQuestionPdf,
} from "./shared/features/external-learning/platform";
import { createIdempotencyKey } from "../../platform/command-id";
import {
  PrivateKnowledgeRequestOwner,
  type PrivateKnowledgeRequestLease,
} from "./features/page-runtime/request-owner";

const noReviewableTextMessage = "没有识别到可逐段核对的文字。";

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
  searchStatus: "idle" | "loading" | "ready" | "error";
  searchedQuery: string;
  searchErrorMessage: string;
  searchRequestId: string;
  citations: PrivateCitationView[];
  importPanelOpen: boolean;
  importErrorMessage: string;
  importRequestId: string;
  importRetryAvailable: boolean;
  errorMessage: string;
  requestId: string;
  backgroundProcessing: boolean;
  citationOpen: boolean;
  activeCitation: PrivateCitationView | null;
  initialSection: string;
}

interface PageCustom {
  loadSources(): Promise<boolean>;
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
  waitForDeletion(
    gateway: PrivateKnowledgeApi,
    deletionId: string,
    request: PrivateKnowledgeRequest,
  ): Promise<boolean>;
  inputQuery(event: WechatMiniprogram.Input): void;
  search(): Promise<void>;
  retry(): void;
  openImportPanel(): void;
  closeImportPanel(): void;
  currentDocumentKind(): "lecture" | "note" | "summary";
  sourceFromEvent(event: WechatMiniprogram.BaseEvent): PrivateSourceView | null;
  failImport(error: unknown): void;
  retryImport(): Promise<void>;
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
  applyInitialSection(): void;
  goBack(): void;
  noop(): void;
}

type PrivateKnowledgePageInstance = WechatMiniprogram.Page.Instance<PageData, PageCustom>;

const retryFiles = new WeakMap<PrivateKnowledgePageInstance, LocalSourceArtifactFile>();

interface PrivateKnowledgeRuntime {
  requests: PrivateKnowledgeRequestOwner;
  gateway: PrivateKnowledgeApi;
}

interface PrivateKnowledgeRequest {
  runtime: PrivateKnowledgeRuntime;
  lease: PrivateKnowledgeRequestLease;
}

const pageRuntimes = new WeakMap<PrivateKnowledgePageInstance, PrivateKnowledgeRuntime>();

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
    searchStatus: "idle",
    searchedQuery: "",
    searchErrorMessage: "",
    searchRequestId: "",
    citations: [],
    importPanelOpen: false,
    importErrorMessage: "",
    importRequestId: "",
    importRetryAvailable: false,
    errorMessage: "",
    requestId: "",
    backgroundProcessing: false,
    citationOpen: false,
    activeCitation: null,
    initialSection: "",
  },

  onLoad(options: Record<string, string | undefined>) {
    const initialSection = options.section?.trim() ?? "";
    this.setData({ initialSection });
  },

  onShow() {
    const auth = currentAuthSession();
    if (!auth) {
      void wx.reLaunch({ url: "/pages/login/index" });
      return;
    }
    const existing = pageRuntimes.get(this);
    const sameOwner = existing?.requests.show(auth) ?? false;
    if (!sameOwner) {
      existing?.requests.unload();
      const requests = new PrivateKnowledgeRequestOwner(auth);
      const runtime = {
        requests,
        gateway: new PrivateKnowledgeApi(new ApiClient(undefined, auth.accessToken)),
      };
      pageRuntimes.set(this, runtime);
      requests.show(auth);
      retryFiles.delete(this);
      resetForSessionChange(this);
    }
    this.setData({ busy: false, searching: false, backgroundProcessing: false });
    void this.loadSources().then((current) => {
      if (current) this.applyInitialSection();
    });
  },

  onHide() {
    pageRuntimes.get(this)?.requests.hide();
  },

  onUnload() {
    pageRuntimes.get(this)?.requests.unload();
    pageRuntimes.delete(this);
    retryFiles.delete(this);
  },

  async loadSources() {
    const request = beginRequest(this, "sources");
    if (!request) return false;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const result = await request.runtime.gateway.listSources();
      if (!requestIsCurrent(this, request)) return false;
      this.setData({
        status: result.sources.length ? "ready" : "empty",
        sources: result.sources.map(privateSourceView),
      });
      return true;
    } catch (error) {
      if (!requestIsCurrent(this, request)) return false;
      this.fail(error, "私人资料库没有加载成功。");
      return true;
    }
  },

  inputTitle(event: WechatMiniprogram.Input) {
    this.setData({ title: event.detail.value });
  },

  selectDocumentKind(event: WechatMiniprogram.PickerChange) {
    this.setData({ documentKindIndex: Number(event.detail.value) });
  },

  async chooseImage() {
    const request = beginRequest(this, "file-picker");
    if (!request) return;
    this.setData({ importTarget: null });
    const file = await chooseQuestionImage("album");
    if (!requestIsCurrent(this, request)) return;
    if (file) await this.uploadAndProcess(file);
  },

  async chooseCameraNote() {
    const request = beginRequest(this, "file-picker");
    if (!request) return;
    this.setData({ importTarget: null });
    const file = await chooseQuestionImage("camera");
    if (!requestIsCurrent(this, request)) return;
    if (file) await this.uploadAndProcess(file);
  },

  async choosePdf() {
    const request = beginRequest(this, "file-picker");
    if (!request) return;
    this.setData({ importTarget: null });
    const file = await chooseQuestionPdf();
    if (!requestIsCurrent(this, request)) return;
    if (file) await this.uploadAndProcess(file);
  },

  async chooseMarkdown() {
    await this.pickMarkdown(null);
  },

  async chooseReplacementImage() {
    const request = beginRequest(this, "file-picker");
    if (!request) return;
    const file = await chooseQuestionImage("album");
    if (!requestIsCurrent(this, request)) return;
    if (file) await this.uploadAndProcess(file);
  },

  async chooseReplacementPdf() {
    const request = beginRequest(this, "file-picker");
    if (!request) return;
    const file = await chooseQuestionPdf();
    if (!requestIsCurrent(this, request)) return;
    if (file) await this.uploadAndProcess(file);
  },

  async chooseReplacementMarkdown() {
    await this.pickMarkdown(this.data.importTarget);
  },

  async pickMarkdown(importTarget: PrivateSourceView | null) {
    const request = beginRequest(this, "file-picker");
    if (!request) return;
    try {
      const result = await wx.chooseMessageFile({
        count: 1,
        type: "file",
        extension: ["md", "markdown"],
      });
      if (!requestIsCurrent(this, request)) return;
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
      if (!requestIsCurrent(this, request)) return;
      const message = (error as { errMsg?: string }).errMsg ?? "";
      if (!message.includes("cancel")) {
        this.failImport(error);
      }
    }
  },

  cancelRoundTrip() {
    if (this.data.busy) return;
    this.setData({ importTarget: null, title: "" });
  },

  async uploadAndProcess(file: LocalSourceArtifactFile) {
    if (this.data.busy) return;
    const request = beginRequest(this, "import");
    if (!request) return;
    const gateway = request.runtime.gateway;
    retryFiles.set(this, file);
    const title = this.data.title.trim() || file.displayName.replace(/\.[^.]+$/u, "");
    const importTarget = this.data.importTarget;
    const documentKind = this.currentDocumentKind();
    this.setData({
      busy: true,
      uploadPercent: 0,
      backgroundProcessing: false,
      importPanelOpen: false,
      importErrorMessage: "",
      importRequestId: "",
      errorMessage: "",
      requestId: "",
      title,
    });
    try {
      const uploadKey = await createIdempotencyKey("private-source-upload");
      if (!requestIsCurrent(this, request)) return;
      const uploaded = await gateway.upload(file, uploadKey, (progress) => {
        if (requestIsCurrent(this, request)) {
          this.setData({ uploadPercent: progress.percent });
        }
      });
      if (!requestIsCurrent(this, request)) return;
      if (file.mimeType === "text/markdown") {
        const createKey = await createIdempotencyKey("private-source-create");
        if (!requestIsCurrent(this, request)) return;
        await gateway.createSource(
          {
            artifactVersionId: uploaded.artifact.artifactVersionId,
            recognitionRevisionId: null,
            title,
            documentKind,
            ...(importTarget
              ? {
                  sourceId: importTarget.sourceId,
                  expectedVersionNo: importTarget.versionNo,
                }
              : {}),
          },
          createKey,
        );
        if (!requestIsCurrent(this, request)) return;
        this.setData({ busy: false, title: "", importTarget: null, importPanelOpen: false });
        retryFiles.delete(this);
        await this.loadSources();
        return;
      }
      const recognitionKey = await createIdempotencyKey("private-source-ocr");
      if (!requestIsCurrent(this, request)) return;
      const revision = await gateway.recognizePrinted(
        uploaded.artifact.artifactVersionId,
        recognitionKey,
      );
      if (!requestIsCurrent(this, request)) return;
      const reviewSpans = recognitionSpans(revision);
      if (!reviewSpans.length) throw new Error(noReviewableTextMessage);
      this.setData({
        busy: false,
        reviewRevision: revision,
        reviewSpans,
        reviewArtifactVersionId: uploaded.artifact.artifactVersionId,
        reviewFilename: file.displayName,
        importRetryAvailable: false,
      });
      retryFiles.delete(this);
    } catch (error) {
      if (!requestIsCurrent(this, request)) return;
      this.failImport(error);
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
    const revision = this.data.reviewRevision;
    if (!revision || this.data.busy) return;
    if (this.data.reviewSpans.some((span) => !span.text.trim())) {
      this.setData({ errorMessage: "每一段识别文字都需要保留或修正，不能留空。" });
      return;
    }
    const request = beginRequest(this, "review");
    if (!request) return;
    const gateway = request.runtime.gateway;
    const reviewSpans = this.data.reviewSpans;
    const artifactVersionId = this.data.reviewArtifactVersionId;
    const title = this.data.title.trim() || this.data.reviewFilename;
    const documentKind = this.currentDocumentKind();
    const importTarget = this.data.importTarget;
    this.setData({ busy: true, errorMessage: "" });
    try {
      let current = revision;
      if (reviewSpans.some((span) => span.text !== span.originalText)) {
        const correctionKey = await createIdempotencyKey("private-source-ocr-correct");
        if (!requestIsCurrent(this, request)) return;
        current = await gateway.correctRecognition(
          revision,
          correctedPayload(revision, reviewSpans),
          correctionKey,
        );
        if (!requestIsCurrent(this, request)) return;
      }
      const confirmKey = await createIdempotencyKey("private-source-ocr-confirm");
      if (!requestIsCurrent(this, request)) return;
      const confirmed = await gateway.confirmRecognition(current, confirmKey);
      if (!requestIsCurrent(this, request)) return;
      const createKey = await createIdempotencyKey("private-source-create");
      if (!requestIsCurrent(this, request)) return;
      await gateway.createSource(
        {
          artifactVersionId,
          recognitionRevisionId: confirmed.recognitionRevisionId,
          title,
          documentKind,
          ...(importTarget
            ? {
                sourceId: importTarget.sourceId,
                expectedVersionNo: importTarget.versionNo,
              }
            : {}),
        },
        createKey,
      );
      if (!requestIsCurrent(this, request)) return;
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
      retryFiles.delete(this);
      await this.loadSources();
    } catch (error) {
      if (!requestIsCurrent(this, request)) return;
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
    const source = this.sourceFromEvent(event);
    if (!source || this.data.busy) return;
    const request = beginRequest(this, "export");
    if (!request) return;
    const gateway = request.runtime.gateway;
    this.setData({ busy: true, errorMessage: "" });
    try {
      const exported = await gateway.exportMarkdown(source.sourceId);
      if (!requestIsCurrent(this, request)) return;
      const path = `${wx.env.USER_DATA_PATH}/${safeFilename(exported.filename)}`;
      wx.getFileSystemManager().writeFileSync(path, exported.content, "utf8");
      await wx.shareFileMessage({ filePath: path, fileName: exported.filename });
      if (!requestIsCurrent(this, request)) return;
      this.setData({ busy: false });
    } catch (error) {
      if (!requestIsCurrent(this, request)) return;
      this.setData({ busy: false });
      this.fail(error, "Markdown 导出失败。");
    }
  },

  deleteSource(event: WechatMiniprogram.BaseEvent) {
    const source = this.sourceFromEvent(event);
    if (!source || this.data.busy) return;
    const runtime = pageRuntimes.get(this);
    wx.showModal({
      title: "彻底删除这份资料？",
      content: "原文件、识别文字和检索内容会一起删除，不能恢复。",
      confirmText: "彻底删除",
      confirmColor: "#C83F56",
      success: (result) => {
        if (result.confirm && runtimeIsActive(this, runtime)) {
          void this.confirmDelete(source);
        }
      },
    });
  },

  async confirmDelete(source: PrivateSourceSummary) {
    const request = beginRequest(this, "delete");
    if (!request) return;
    const gateway = request.runtime.gateway;
    this.setData({ busy: true, errorMessage: "" });
    try {
      const deletionKey = await createIdempotencyKey("private-source-delete");
      if (!requestIsCurrent(this, request)) return;
      const deletion = await gateway.deleteSource(source.sourceId, deletionKey);
      if (!requestIsCurrent(this, request)) return;
      const completed = await this.waitForDeletion(gateway, deletion.deletionRequestId, request);
      if (!completed || !requestIsCurrent(this, request)) return;
      this.setData({ busy: false });
      await this.loadSources();
    } catch (error) {
      if (!requestIsCurrent(this, request)) return;
      this.setData({ busy: false });
      this.fail(error, "资料删除没有完成。");
    }
  },

  async waitForDeletion(
    gateway: PrivateKnowledgeApi,
    deletionId: string,
    request: PrivateKnowledgeRequest,
  ) {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      const current = await gateway.deletionStatus(deletionId);
      if (!requestIsCurrent(this, request)) return false;
      if (current.state === "completed") return true;
      if (current.state === "failed") throw new Error("删除任务失败，请稍后重试。");
      const currentAfterDelay = await request.runtime.requests.delay(
        500,
        request.lease,
        currentAuthSession(),
      );
      if (!currentAfterDelay || !requestIsCurrent(this, request)) return false;
    }
    throw new Error("删除任务仍在处理中，请稍后刷新查看。");
  },

  inputQuery(event: WechatMiniprogram.Input) {
    this.setData({
      query: event.detail.value,
      searchStatus: "idle",
      searchedQuery: "",
      searchErrorMessage: "",
      searchRequestId: "",
      citations: [],
    });
  },

  async search() {
    const query = this.data.query.trim();
    if (!query || this.data.searching) return;
    const request = beginRequest(this, "search");
    if (!request) return;
    const gateway = request.runtime.gateway;
    this.setData({
      searching: true,
      searchStatus: "loading",
      searchedQuery: query,
      searchErrorMessage: "",
      searchRequestId: "",
      errorMessage: "",
    });
    try {
      const result = await gateway.search(query);
      if (!requestIsCurrent(this, request)) return;
      this.setData({
        searching: false,
        searchStatus: "ready",
        citations: result.citations.map((citation) => ({
          ...citation,
          matchLabel: `匹配 ${String(Math.round(citation.finalScore * 100))}%`,
        })),
      });
    } catch (error) {
      if (!requestIsCurrent(this, request)) return;
      const apiError = error instanceof ApiClientError ? error : null;
      this.setData({
        searching: false,
        searchStatus: "error",
        citations: [],
        searchErrorMessage: apiError?.message ?? "私人资料检索没有完成，请重试。",
        searchRequestId: apiError?.requestId ?? "",
      });
    }
  },

  retry() {
    void this.loadSources();
  },

  openImportPanel() {
    this.setData({
      importPanelOpen: true,
      importErrorMessage: "",
      importRequestId: "",
      importRetryAvailable: false,
    });
  },

  closeImportPanel() {
    retryFiles.delete(this);
    this.setData({
      importPanelOpen: false,
      importTarget: null,
      importErrorMessage: "",
      importRequestId: "",
      importRetryAvailable: false,
    });
  },

  currentDocumentKind() {
    return this.data.documentKinds[this.data.documentKindIndex]?.value ?? "note";
  },

  sourceFromEvent(event: WechatMiniprogram.BaseEvent) {
    const sourceId = String(event.currentTarget.dataset.sourceId ?? "");
    return this.data.sources.find((item) => item.sourceId === sourceId) ?? null;
  },

  failImport(error: unknown) {
    const apiError = error instanceof ApiClientError ? error : null;
    const localMessage =
      error instanceof Error && error.message === noReviewableTextMessage
        ? noReviewableTextMessage
        : apiError?.statusCode === 503
          ? "资料已收到，但这次识别服务暂时不可用。"
          : (apiError?.message ?? "服务暂时没有完成这份资料的处理。");
    this.setData({
      busy: false,
      importPanelOpen: true,
      importErrorMessage: localMessage,
      importRequestId: apiError?.requestId ?? "",
      importRetryAvailable: retryFiles.has(this),
    });
  },

  async retryImport() {
    const file = retryFiles.get(this);
    if (!file || this.data.busy) return;
    await this.uploadAndProcess(file);
  },

  fail(error: unknown, fallback: string) {
    const apiError = error instanceof ApiClientError ? error : null;
    this.setData({
      status: this.data.sources.length ? this.data.status : "error",
      errorMessage: apiError?.message ?? fallback,
      requestId: apiError?.requestId ?? "",
    });
  },

  runInBackground() {
    this.setData({ backgroundProcessing: true });
    void wx.showToast({ title: "已转入后台阅读", icon: "success" });
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

  applyInitialSection() {
    const runtime = pageRuntimes.get(this);
    if (!runtimeIsActive(this, runtime)) return;
    const target = this.data.initialSection;
    if (!target) return;
    this.setData({ initialSection: "" });
    if (target === "import") {
      this.openImportPanel();
      return;
    }
    const selectors: Record<string, string> = {
      sources: "#library-sources",
      search: "#library-search",
    };
    const selector = selectors[target];
    if (selector) {
      wx.nextTick(() => {
        if (runtimeIsActive(this, runtime)) {
          void wx.pageScrollTo({ selector, duration: 0 });
        }
      });
    }
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
    navigateBackOr(routes.me);
  },

  noop() {},

  navigateToDetail(event: WechatMiniprogram.BaseEvent) {
    const sourceId = String(event.currentTarget.dataset.sourceId ?? "");
    if (!sourceId) return;
    const runtime = pageRuntimes.get(this);
    if (!runtimeIsActive(this, runtime)) return;
    void wx.navigateTo({
      url: `${routes.privateKnowledgeDetail}?id=${encodeURIComponent(sourceId)}`,
      events: {
        replaceSource: (payload: { sourceId?: string }) => {
          if (runtimeIsActive(this, runtime)) {
            this.prepareReplacement(payload.sourceId ?? "");
          }
        },
      },
    });
  },

  prepareReplacement(sourceId) {
    const source = this.data.sources.find((item) => item.sourceId === sourceId);
    if (!source || this.data.busy) return;
    const runtime = pageRuntimes.get(this);
    if (!runtimeIsActive(this, runtime)) return;
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
        if (!runtimeIsActive(this, runtime)) return;
        if (result.tapIndex === 0) void this.chooseReplacementImage();
        else if (result.tapIndex === 1) void this.chooseReplacementPdf();
        else if (result.tapIndex === 2) void this.chooseReplacementMarkdown();
      },
    });
  },
});

function beginRequest(
  page: PrivateKnowledgePageInstance,
  lane: string,
): PrivateKnowledgeRequest | null {
  const runtime = pageRuntimes.get(page);
  const lease = runtime?.requests.begin(lane, currentAuthSession()) ?? null;
  return runtime && lease ? { runtime, lease } : null;
}

function requestIsCurrent(
  page: PrivateKnowledgePageInstance,
  request: PrivateKnowledgeRequest,
): boolean {
  return (
    pageRuntimes.get(page) === request.runtime &&
    request.runtime.requests.owns(request.lease, currentAuthSession())
  );
}

function runtimeIsActive(
  page: PrivateKnowledgePageInstance,
  runtime: PrivateKnowledgeRuntime | undefined,
): runtime is PrivateKnowledgeRuntime {
  return (
    runtime !== undefined &&
    pageRuntimes.get(page) === runtime &&
    runtime.requests.active(currentAuthSession())
  );
}

function resetForSessionChange(page: PrivateKnowledgePageInstance): void {
  page.setData({
    status: "loading",
    sources: [],
    title: "",
    busy: false,
    uploadPercent: 0,
    reviewRevision: null,
    reviewSpans: [],
    reviewArtifactVersionId: "",
    reviewFilename: "",
    importTarget: null,
    query: "",
    searching: false,
    searchStatus: "idle",
    searchedQuery: "",
    searchErrorMessage: "",
    searchRequestId: "",
    citations: [],
    importPanelOpen: false,
    importErrorMessage: "",
    importRequestId: "",
    importRetryAvailable: false,
    errorMessage: "",
    requestId: "",
    backgroundProcessing: false,
    citationOpen: false,
    activeCitation: null,
  });
}

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
  if (!match) return value;
  const [, year = "", month = "", day = ""] = match;
  return `${year}.${month}.${day}`;
}
