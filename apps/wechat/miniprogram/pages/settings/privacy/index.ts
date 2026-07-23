import { authedV2, idemKey } from "../../../api/endpoints";
import type {
  AccountDeletionCreateRequest,
  AccountDeletionView,
  DataExportCreateRequest,
  DataExportView,
  PrivacyOverview,
} from "../../../api/generated/types.gen";

interface CategoryView {
  title: string;
  countText: string;
  retention: string;
}

/** 导出任务状态文案 */
const EXPORT_STATE_TEXT: Record<string, string> = {
  queued: "排队中",
  running: "生成中",
  completed: "已生成",
  failed: "生成失败",
  expired: "已过期",
};

Page({
  data: {
    statusBarHeight: 20,
    viewState: "loading" as "loading" | "error" | "ready",
    categories: [] as CategoryView[],
    rawMaterialRetention: "",
    policyText: "",
    deletionEffects: [] as string[],
    exporting: false,
    exportStateText: "",
    deleting: false,
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    void this.load();
  },

  onBack() {
    wx.navigateBack({
      fail: () => {
        wx.switchTab({ url: "/pages/profile/index" });
      },
    });
  },

  onRetry() {
    void this.load();
  },

  async load() {
    this.setData({ viewState: "loading" });
    try {
      const overview = await authedV2<PrivacyOverview>({
        method: "GET",
        path: "/api/v2/data-rights/overview",
      });
      this.setData({
        viewState: "ready",
        categories: overview.categories.map((c) => ({
          title: c.title,
          countText: `${c.itemCount} 条`,
          retention: c.retention,
        })),
        rawMaterialRetention: overview.rawMaterialRetention,
        policyText: `${overview.policyTitle} · ${overview.policyVersion}`,
        deletionEffects: overview.accountDeletionEffect,
      });
    } catch {
      this.setData({ viewState: "error" });
    }
  },

  async onExport() {
    if (this.data.exporting) return;
    this.setData({ exporting: true });
    try {
      const body: DataExportCreateRequest = { commandKey: idemKey() };
      const task = await authedV2<DataExportView>({
        method: "POST",
        path: "/api/v2/data-rights/exports",
        body,
        idempotencyKey: idemKey(),
      });
      this.setData({ exportStateText: EXPORT_STATE_TEXT[task.state] ?? task.state });
      wx.showToast({ title: "导出任务已提交", icon: "none" });
    } catch {
      wx.showToast({ title: "网络不太稳定，稍后再试", icon: "none" });
    } finally {
      this.setData({ exporting: false });
    }
  },

  onDelete() {
    wx.showModal({
      title: "注销账号",
      content: "注销后账号与全部学习数据将被删除且不可恢复。确定继续吗？",
      confirmText: "确认注销",
      confirmColor: "#e05252",
      success: (res) => {
        if (res.confirm) void this.confirmDelete();
      },
    });
  },

  async confirmDelete() {
    if (this.data.deleting) return;
    this.setData({ deleting: true });
    try {
      const body: AccountDeletionCreateRequest = {
        commandKey: idemKey(),
        confirmationPhrase: "注销账号",
      };
      await authedV2<AccountDeletionView>({
        method: "POST",
        path: "/api/v2/data-rights/deletions",
        body,
        idempotencyKey: idemKey(),
      });
      wx.showToast({ title: "注销申请已提交", icon: "none" });
      wx.reLaunch({ url: "/pages/onboarding/index/index" });
    } catch {
      this.setData({ deleting: false });
      wx.showToast({ title: "网络不太稳定，稍后再试", icon: "none" });
    }
  },
});
