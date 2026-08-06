import { ApiClient, ApiClientError } from "../../../api/client";
import { PrivateKnowledgeApi, type PrivateSourceSummary } from "../features/api/private-knowledge";
import { routes } from "../../../app/navigation/routes";
import { navigateBackOr } from "../../../app/navigation/safe-back";
import { currentAuthSession } from "../../../auth/session";
import { createIdempotencyKey } from "../../../platform/command-id";

interface PrivateSourceDetailView extends PrivateSourceSummary {
  createdAtLabel: string;
}

interface PageData {
  status: "loading" | "ready" | "error";
  source: PrivateSourceDetailView | null;
  documentKindLabel: string;
  revisionLabel: string;
  busy: boolean;
  errorMessage: string;
  requestId: string;
  sourceId: string;
  deleteSheetOpen: boolean;
  deletionBackgrounded: boolean;
}

interface PageCustom {
  loadDetails(): Promise<void>;
  uploadNewVersion(): void;
  deleteSource(): void;
  closeDeleteSheet(): void;
  confirmDelete(): Promise<void>;
  runInBackground(): void;
  waitForDeletion(gateway: PrivateKnowledgeApi, deletionId: string): Promise<void>;
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
    source: null,
    documentKindLabel: "",
    revisionLabel: "",
    busy: false,
    errorMessage: "",
    requestId: "",
    sourceId: "",
    deleteSheetOpen: false,
    deletionBackgrounded: false,
  },

  onLoad(options) {
    const id = options.id ?? "";
    if (!id) {
      navigateBackOr(routes.privateKnowledge);
      return;
    }
    this.setData({ sourceId: id });
  },

  onShow() {
    void this.loadDetails();
  },

  async loadDetails() {
    const gateway = api();
    if (!gateway) {
      void wx.reLaunch({ url: "/pages/login/index" });
      return;
    }
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const result = await gateway.listSources();
      const current = result.sources.find((item) => item.sourceId === this.data.sourceId);
      if (!current) {
        throw new Error("找不到对应的私人资料。");
      }

      const documentKindLabel = {
        lecture: "讲义",
        note: "笔记",
        summary: "知识总结",
      }[current.documentKind];

      const revisionLabel =
        current.versionNo > 1 ? `第 ${String(current.versionNo)} 次更新` : "首次导入";

      this.setData({
        status: "ready",
        source: {
          ...current,
          createdAtLabel: formatDate(current.createdAt),
        },
        documentKindLabel,
        revisionLabel,
      });
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : null;
      this.setData({
        status: "error",
        errorMessage: apiError?.message ?? "资料详情没有加载成功，请重试。",
        requestId: apiError?.requestId ?? "",
      });
    }
  },

  uploadNewVersion() {
    const source = this.data.source;
    if (!source || this.data.busy) return;
    const eventChannel = this.getOpenerEventChannel();
    if (!eventChannel.emit) {
      void wx.showModal({
        title: "无法开始版本更新",
        content: "请返回私人资料库，从资料卡重新进入后再更新版本。",
        showCancel: false,
      });
      return;
    }
    eventChannel.emit("replaceSource", { sourceId: source.sourceId });
    navigateBackOr(routes.privateKnowledge);
  },

  deleteSource() {
    if (this.data.busy || !this.data.source) return;
    this.setData({ deleteSheetOpen: true });
  },

  closeDeleteSheet() {
    if (this.data.busy) return;
    this.setData({ deleteSheetOpen: false });
  },

  async confirmDelete() {
    if (this.data.busy) return;
    const gateway = api();
    const source = this.data.source;
    if (!gateway || !source) return;
    this.setData({ busy: true, deletionBackgrounded: false, errorMessage: "" });
    try {
      const deletion = await gateway.deleteSource(
        source.sourceId,
        await createIdempotencyKey("private-source-delete"),
      );
      await this.waitForDeletion(gateway, deletion.deletionRequestId);
      if (this.data.deletionBackgrounded) {
        void wx.showToast({ title: "资料已移除", icon: "success" });
        return;
      }
      this.setData({ deleteSheetOpen: false });
      void wx.showToast({ title: "已移除", icon: "success" });
      setTimeout(() => {
        navigateBackOr(routes.privateKnowledge);
      }, 1000);
    } catch (error) {
      if (this.data.deletionBackgrounded) {
        void wx.showToast({ title: "资料移除没有完成，请重试", icon: "none" });
        return;
      }
      this.setData({ busy: false });
      void wx.showModal({
        title: "移除失败",
        content: error instanceof Error ? error.message : "无法完成资料删除",
        showCancel: false,
      });
    }
  },

  runInBackground() {
    if (!this.data.busy || this.data.deletionBackgrounded) return;
    this.setData({ deletionBackgrounded: true, deleteSheetOpen: false });
    void wx.showToast({ title: "已转入后台移除", icon: "success" });
    navigateBackOr(routes.privateKnowledge);
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

  goBack() {
    if (this.data.deleteSheetOpen) {
      this.closeDeleteSheet();
      return;
    }
    navigateBackOr(routes.privateKnowledge);
  },

  noop() {},
});

function formatDate(value: string): string {
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})/u);
  if (!match) return value;
  const [, year = "", month = "", day = ""] = match;
  return `${year}.${month}.${day}`;
}
