import { ApiClient, ApiClientError } from "../../api/client";
import { clearSession } from "../../api/auth";
import { DataRightsApi } from "../shared/api/data-rights";
import type {
  DataCategoryView,
  DataExportView,
  PrivacyOverview,
} from "../../api/generated/types.gen";
import { currentAuthSession } from "../../auth/session";
import {
  clearPendingAccountDeletion,
  clearPendingDataExport,
  loadPendingAccountDeletion,
  loadPendingDataExport,
  storePendingAccountDeletion,
  storePendingDataExport,
} from "../../features/data-rights/pending-store";
import { createIdempotencyKey } from "../../platform/command-id";

type CategoryDisplay = DataCategoryView & { countLabel: string };
type ExportPresentation = {
  stateLabel: string;
  canRefresh: boolean;
  canOpen: boolean;
  canRestart: boolean;
};
const confirmationPhrase = "删除我的岸岸账户";

function exportPresentation(value: DataExportView): ExportPresentation {
  switch (value.state) {
    case "queued":
      return {
        stateLabel: "已提交，正在等待生成",
        canRefresh: true,
        canOpen: false,
        canRestart: false,
      };
    case "running":
      return {
        stateLabel: "正在整理你的个人数据",
        canRefresh: true,
        canOpen: false,
        canRestart: false,
      };
    case "completed":
      return {
        stateLabel: "已经生成，可以保存或转发",
        canRefresh: false,
        canOpen: true,
        canRestart: false,
      };
    case "failed":
      return {
        stateLabel: "这次没生成成功，重新来一次吧",
        canRefresh: false,
        canOpen: false,
        canRestart: true,
      };
    case "expired":
      return {
        stateLabel: "这份文件已经过期，重新生成一份吧",
        canRefresh: false,
        canOpen: false,
        canRestart: true,
      };
  }
}

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    overview: null as PrivacyOverview | null,
    categories: [] as CategoryDisplay[],
    exportRequest: null as DataExportView | null,
    exportStateLabel: "",
    exportCanRefresh: false,
    exportCanOpen: false,
    exportCanRestart: false,
    exporting: false,
    restoringExport: false,
    openingExport: false,
    deletionPhrase: "",
    deleting: false,
    confirmationPhrase,
  },

  onShow() {
    void this.restorePage();
  },

  async restorePage() {
    const session = currentAuthSession();
    if (!session) return;
    if (loadPendingAccountDeletion(session.userId)) {
      await this.deleteAccount();
      return;
    }
    await Promise.all([this.loadOverview(), this.restoreExport()]);
  },

  async loadOverview() {
    const session = currentAuthSession();
    if (!session) return;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const response = await new DataRightsApi(
        new ApiClient(undefined, session.accessToken),
      ).overview();
      this.setData({
        status: "ready",
        overview: response.data,
        categories: response.data.categories.map((item) => ({
          ...item,
          countLabel: `${String(item.itemCount)} 项`,
        })),
      });
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        status: "error",
        errorMessage: apiError?.message ?? "数据清单没加载出来，再试一次吧。",
        requestId: apiError?.requestId ?? "",
      });
    }
  },

  retry() {
    void this.restorePage();
  },

  async restoreExport() {
    const session = currentAuthSession();
    if (!session || this.data.restoringExport) return;
    const pending = loadPendingDataExport(session.userId);
    if (!pending) return;
    this.setData({ restoringExport: true });
    const api = new DataRightsApi(new ApiClient(undefined, session.accessToken));
    try {
      const response = pending.exportRequestId
        ? await api.exportStatus(pending.exportRequestId)
        : await api.createExport(pending.idempotencyKey);
      storePendingDataExport(session.userId, {
        idempotencyKey: pending.idempotencyKey,
        exportRequestId: response.data.exportRequestId,
      });
      this.applyExport(response.data);
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      if (apiError && apiError.statusCode >= 400 && apiError.statusCode < 500) {
        clearPendingDataExport(session.userId);
      }
      this.setData({ errorMessage: apiError?.message ?? "数据导出状态没恢复，再试一次吧。" });
    } finally {
      this.setData({ restoringExport: false });
    }
  },

  async createExport() {
    const session = currentAuthSession();
    if (!session || this.data.exporting) return;
    this.setData({ exporting: true, errorMessage: "" });
    let pending = loadPendingDataExport(session.userId);
    try {
      if (!pending || pending.exportRequestId) {
        pending = {
          idempotencyKey: await createIdempotencyKey("personal-export"),
          exportRequestId: "",
        };
        storePendingDataExport(session.userId, pending);
      }
      const response = await new DataRightsApi(
        new ApiClient(undefined, session.accessToken),
      ).createExport(pending.idempotencyKey);
      storePendingDataExport(session.userId, {
        idempotencyKey: pending.idempotencyKey,
        exportRequestId: response.data.exportRequestId,
      });
      this.applyExport(response.data);
      void wx.showToast({ title: "已开始生成", icon: "success" });
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      const uncertain = !apiError || apiError.statusCode === 0 || apiError.statusCode >= 500;
      if (!uncertain) clearPendingDataExport(session.userId);
      this.setData({
        errorMessage: uncertain
          ? "刚才的结果还不确定，进度帮你留着，直接再试一次就好。"
          : apiError.message,
      });
    } finally {
      this.setData({ exporting: false });
    }
  },

  async refreshExport() {
    const session = currentAuthSession();
    const exportRequest = this.data.exportRequest;
    if (!session || !exportRequest) return;
    try {
      const response = await new DataRightsApi(
        new ApiClient(undefined, session.accessToken),
      ).exportStatus(exportRequest.exportRequestId);
      const pending = loadPendingDataExport(session.userId);
      if (pending) {
        storePendingDataExport(session.userId, {
          idempotencyKey: pending.idempotencyKey,
          exportRequestId: response.data.exportRequestId,
        });
      }
      this.applyExport(response.data);
      this.setData({ errorMessage: "" });
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({ errorMessage: apiError?.message ?? "生成状态还没更新，再试一次吧。" });
    }
  },

  async openExport() {
    const session = currentAuthSession();
    const exportRequest = this.data.exportRequest;
    if (!session || !exportRequest || exportRequest.state !== "completed") return;
    this.setData({ openingExport: true, errorMessage: "" });
    try {
      const response = await new DataRightsApi(
        new ApiClient(undefined, session.accessToken),
      ).exportPayload(exportRequest.exportRequestId);
      const path = `${wx.env.USER_DATA_PATH}/岸岸个人数据-${exportRequest.exportRequestId}.txt`;
      const content = JSON.stringify(response.data.payload, null, 2);
      await new Promise<void>((resolve, reject) => {
        wx.getFileSystemManager().writeFile({
          filePath: path,
          data: content,
          encoding: "utf8",
          success: () => {
            resolve();
          },
          fail: () => {
            reject(new Error("个人数据文件写入失败"));
          },
        });
      });
      await new Promise<void>((resolve, reject) => {
        wx.shareFileMessage({
          filePath: path,
          fileName: `岸岸个人数据-${exportRequest.exportRequestId}.txt`,
          success: () => {
            resolve();
          },
          fail: () => {
            reject(new Error("个人数据文件转发失败"));
          },
        });
      });
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({ errorMessage: apiError?.message ?? "文件没保存成功，再试一次吧。" });
    } finally {
      this.setData({ openingExport: false });
    }
  },

  restartExport() {
    const session = currentAuthSession();
    if (!session || this.data.exporting) return;
    clearPendingDataExport(session.userId);
    this.setData({
      exportRequest: null,
      exportStateLabel: "",
      exportCanRefresh: false,
      exportCanOpen: false,
      exportCanRestart: false,
    });
    void this.createExport();
  },

  applyExport(exportRequest: DataExportView) {
    const presentation = exportPresentation(exportRequest);
    this.setData({
      exportRequest,
      exportStateLabel: presentation.stateLabel,
      exportCanRefresh: presentation.canRefresh,
      exportCanOpen: presentation.canOpen,
      exportCanRestart: presentation.canRestart,
    });
  },

  onDeletionPhraseInput(event: WechatMiniprogram.Input) {
    this.setData({ deletionPhrase: event.detail.value });
  },

  requestDeletion() {
    if (this.data.deletionPhrase !== confirmationPhrase || this.data.deleting) return;
    wx.showModal({
      title: "账户删除后无法恢复",
      content: "会马上退出所有设备，删除你的身份信息和上传的原件。建议先把数据导出来。",
      confirmText: "确认删除",
      confirmColor: "#C83F56",
      success: (result) => {
        if (result.confirm) void this.deleteAccount();
      },
    });
  },

  async deleteAccount() {
    const session = currentAuthSession();
    if (!session) return;
    this.setData({ deleting: true, errorMessage: "" });
    let pending = loadPendingAccountDeletion(session.userId);
    try {
      if (!pending) {
        pending = {
          idempotencyKey: await createIdempotencyKey("account-delete"),
        };
        storePendingAccountDeletion(session.userId, pending);
      }
      await new DataRightsApi(new ApiClient(undefined, session.accessToken)).requestDeletion(
        pending.idempotencyKey,
        confirmationPhrase,
      );
      clearPendingAccountDeletion(session.userId);
      clearSession();
      void wx.reLaunch({ url: "/pages/login/index?reason=account_deletion_pending" });
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      const uncertain = !apiError || apiError.statusCode === 0 || apiError.statusCode >= 500;
      if (!uncertain) clearPendingAccountDeletion(session.userId);
      if (apiError?.statusCode === 401) {
        clearSession();
        void wx.reLaunch({ url: "/pages/login/index?reason=account_deletion_pending" });
        return;
      }
      this.setData({
        deleting: false,
        errorMessage: uncertain
          ? "刚才的删除结果还不确定，操作帮你留着，下次进来会接着确认。"
          : apiError.message,
      });
    }
  },
});
