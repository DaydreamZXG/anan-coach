import { ApiClient, ApiClientError } from "../../api/client";
import { DataRightsApi } from "../../api/data-rights";
import type {
  DataCategoryView,
  DataExportView,
  PrivacyOverview,
} from "../../api/generated/types.gen";
import { clearAuthSession, currentAccessToken } from "../../auth/session";

type CategoryDisplay = DataCategoryView & { countLabel: string };
const confirmationPhrase = "删除我的岸岸账户";

function commandKey(prefix: string): string {
  return `${prefix}-${String(Date.now())}-${Math.random().toString(36).slice(2, 10)}`;
}

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    overview: null as PrivacyOverview | null,
    categories: [] as CategoryDisplay[],
    exportRequest: null as DataExportView | null,
    exporting: false,
    openingExport: false,
    deletionPhrase: "",
    deleting: false,
    confirmationPhrase,
  },

  onShow() {
    void this.loadOverview();
  },

  async loadOverview() {
    const token = currentAccessToken();
    if (!token) return;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const response = await new DataRightsApi(new ApiClient(undefined, token)).overview();
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
        errorMessage: apiError?.message ?? "数据清单加载失败，请重试。",
        requestId: apiError?.requestId ?? "",
      });
    }
  },

  retry() {
    void this.loadOverview();
  },

  async createExport() {
    const token = currentAccessToken();
    if (!token || this.data.exporting) return;
    this.setData({ exporting: true, errorMessage: "" });
    try {
      const response = await new DataRightsApi(new ApiClient(undefined, token)).createExport(
        commandKey("personal-export"),
      );
      this.setData({ exportRequest: response.data });
      void wx.showToast({ title: "已开始生成", icon: "success" });
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({ errorMessage: apiError?.message ?? "数据导出创建失败。" });
    } finally {
      this.setData({ exporting: false });
    }
  },

  async refreshExport() {
    const token = currentAccessToken();
    const exportRequest = this.data.exportRequest;
    if (!token || !exportRequest) return;
    try {
      const response = await new DataRightsApi(new ApiClient(undefined, token)).exportStatus(
        exportRequest.exportRequestId,
      );
      this.setData({ exportRequest: response.data, errorMessage: "" });
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({ errorMessage: apiError?.message ?? "导出状态刷新失败。" });
    }
  },

  async openExport() {
    const token = currentAccessToken();
    const exportRequest = this.data.exportRequest;
    if (!token || !exportRequest || exportRequest.state !== "completed") return;
    this.setData({ openingExport: true, errorMessage: "" });
    try {
      const response = await new DataRightsApi(new ApiClient(undefined, token)).exportPayload(
        exportRequest.exportRequestId,
      );
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
      this.setData({ errorMessage: apiError?.message ?? "导出文件保存失败。" });
    } finally {
      this.setData({ openingExport: false });
    }
  },

  onDeletionPhraseInput(event: WechatMiniprogram.Input) {
    this.setData({ deletionPhrase: event.detail.value });
  },

  requestDeletion() {
    if (this.data.deletionPhrase !== confirmationPhrase || this.data.deleting) return;
    wx.showModal({
      title: "账户删除后无法恢复",
      content: "会立即退出全部设备，删除身份映射和私有上传原件。建议先导出数据。",
      confirmText: "确认删除",
      confirmColor: "#C83F56",
      success: (result) => {
        if (result.confirm) void this.deleteAccount();
      },
    });
  },

  async deleteAccount() {
    const token = currentAccessToken();
    if (!token) return;
    this.setData({ deleting: true, errorMessage: "" });
    try {
      await new DataRightsApi(new ApiClient(undefined, token)).requestDeletion(
        commandKey("account-delete"),
        this.data.deletionPhrase,
      );
      clearAuthSession();
      void wx.reLaunch({ url: "/pages/login/index?reason=account_deletion_pending" });
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        deleting: false,
        errorMessage: apiError?.message ?? "账户删除请求未能创建。",
      });
    }
  },
});
