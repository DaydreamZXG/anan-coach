import { fetchMasteryQueue, type MasteryBucket } from "../../api/endpoints";
import type { MasteryQueueItem, MasteryQueueSummary } from "../../api/generated/types.gen";

type ViewState = "loading" | "ready" | "error";

interface Tab {
  bucket: MasteryBucket;
  label: string;
  countKey: keyof MasteryQueueSummary;
}

interface QueueItemView {
  itemId: string;
  bucket: MasteryQueueItem["bucket"];
  module: string;
  questionCount: number;
  timeLabel: string;
  actionable: boolean;
  actionLabel: string;
  planTaskId: string | null;
  sessionId: string | null;
  reviewId: string | null;
}

const TABS: Tab[] = [
  { bucket: "pending_reflection", label: "待复盘", countKey: "pendingReflectionCount" },
  { bucket: "due_verification", label: "待验证", countKey: "dueVerificationCount" },
  { bucket: "stable_mastery", label: "已稳定", countKey: "stableMasteryCount" },
];

function formatDay(iso: string | null | undefined, prefix: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${prefix}${d.getMonth() + 1}月${d.getDate()}日`;
}

Page({
  data: {
    statusBarHeight: 20,
    viewState: "loading" as ViewState,
    tabs: TABS.map((t) => ({ ...t, count: 0 })),
    activeBucket: "pending_reflection" as MasteryBucket,
    items: [] as QueueItemView[],
    summary: null as MasteryQueueSummary | null,
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    this.loadBucket("pending_reflection");
  },

  async loadBucket(bucket: MasteryBucket) {
    this.setData({ viewState: "loading", activeBucket: bucket });
    try {
      const page = await fetchMasteryQueue(bucket);
      const tabs = TABS.map((t) => ({ ...t, count: page.summary[t.countKey] ?? 0 }));
      const items: QueueItemView[] = page.items.map((it: MasteryQueueItem) => {
        const isReflection = it.bucket === "pending_reflection";
        // 待复盘桶要求会话+复盘记录齐全才可行动；待验证桶要求已生成计划任务
        const actionable =
          it.action.state === "ready" &&
          (isReflection ? Boolean(it.sessionId && it.reviewId) : Boolean(it.action.planTaskId));
        return {
          itemId: it.itemId,
          bucket: it.bucket,
          module: it.moduleName ?? "行测",
          questionCount: it.questionCount ?? 0,
          timeLabel:
            it.bucket === "due_verification"
              ? formatDay(it.dueAt, "到期 ")
              : formatDay(it.createdAt, "完成于 "),
          actionable,
          actionLabel: isReflection
            ? "去复盘"
            : it.bucket === "due_verification"
              ? "开始复测"
              : "已稳定",
          planTaskId: it.action.planTaskId ?? null,
          sessionId: it.sessionId ?? null,
          reviewId: it.reviewId ?? null,
        };
      });
      this.setData({ viewState: "ready", items, summary: page.summary, tabs });
    } catch (err) {
      console.warn("[anan] 掌握队列加载失败：", err);
      this.setData({ viewState: "error" });
    }
  },

  onTab(e: WechatMiniprogram.TouchEvent) {
    const bucket = e.currentTarget.dataset.bucket as MasteryBucket;
    if (bucket === this.data.activeBucket) return;
    this.loadBucket(bucket);
  },

  onItem(e: WechatMiniprogram.TouchEvent) {
    const { bucket, planTaskId, sessionId, reviewId, actionable } = e.currentTarget.dataset as {
      bucket: MasteryQueueItem["bucket"];
      planTaskId: string | null;
      sessionId: string | null;
      reviewId: string | null;
      actionable: boolean;
    };
    if (!actionable) {
      wx.showToast({
        title: bucket === "stable_mastery" ? "这条已经稳定掌握" : "题组还在生成中，稍后再来",
        icon: "none",
      });
      return;
    }
    // 待复盘：去复盘归因页（需要作答会话与复盘记录两个标识）
    if (bucket === "pending_reflection") {
      if (!sessionId || !reviewId) {
        wx.showToast({ title: "复盘记录还不完整，稍后再来", icon: "none" });
        return;
      }
      wx.navigateTo({
        url: `/pages/reflection/index?sessionId=${encodeURIComponent(sessionId)}&reviewId=${encodeURIComponent(reviewId)}`,
      });
      return;
    }
    // 待验证：直接进题组
    if (planTaskId) {
      wx.navigateTo({ url: `/pages/practice/index?planTaskId=${encodeURIComponent(planTaskId)}` });
      return;
    }
    wx.showToast({ title: "题组还在生成中，稍后再来", icon: "none" });
  },

  onRetry() {
    this.loadBucket(this.data.activeBucket);
  },

  onBack() {
    wx.navigateBack({
      fail: () => wx.switchTab({ url: "/pages/training/index" }),
    });
  },
});
