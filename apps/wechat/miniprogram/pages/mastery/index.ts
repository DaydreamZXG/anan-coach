import { authedV2 } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import type { MasteryQueueItem, MasteryQueuePage } from "../../api/generated/types.gen";

type ViewState = "loading" | "ready" | "error";

type Bucket = MasteryQueueItem["bucket"];

/** 模块掌握分布视图（题数按桶聚合） */
interface ModuleView {
  name: string;
  totalQuestions: number;
  pendingCount: number;
  dueCount: number;
  stableCount: number;
  invalidatedCount: number;
  segPending: number;
  segDue: number;
  segStable: number;
  segInvalidated: number;
}

/** 失效记录视图 */
interface InvalidatedView {
  itemId: string;
  module: string;
  questionCount: number;
  dateLabel: string;
}

const BUCKETS: Bucket[] = [
  "pending_reflection",
  "due_verification",
  "stable_mastery",
  "invalidated",
];

Page({
  data: {
    statusBarHeight: 20,
    viewState: "loading" as ViewState,
    errorDesc: "",
    requestId: "",
    pendingCount: 0,
    dueCount: 0,
    stableCount: 0,
    invalidatedCount: 0,
    modules: [] as ModuleView[],
    invalidated: [] as InvalidatedView[],
    generatedAtLabel: "",
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    void this.loadAll();
  },

  /** 四个桶并发拉取，summary 全局一致，任取一份即可 */
  async loadAll() {
    this.setData({ viewState: "loading" });
    try {
      const pages = await Promise.all(
        BUCKETS.map((bucket) =>
          authedV2<MasteryQueuePage>({
            method: "GET",
            path: "/api/v2/xingce/mastery-queue",
            query: { bucket, pageSize: 100 },
          }),
        ),
      );
      const [pending, due, stable, invalidated] = pages as [
        MasteryQueuePage,
        MasteryQueuePage,
        MasteryQueuePage,
        MasteryQueuePage,
      ];
      const summary = pending.summary;
      this.setData({
        viewState: "ready",
        pendingCount: summary.pendingReflectionCount,
        dueCount: summary.dueVerificationCount,
        stableCount: summary.stableMasteryCount,
        invalidatedCount: summary.invalidatedCount,
        modules: aggregateModules([pending.items, due.items, stable.items, invalidated.items]),
        invalidated: invalidated.items.map(presentInvalidated),
        generatedAtLabel: generatedAtLabel(pending.generatedAt),
      });
    } catch (err) {
      const apiErr = err as ApiError;
      this.setData({
        viewState: "error",
        errorDesc: "掌握度数据没有加载成功，请检查网络后重试。",
        requestId: apiErr.requestId ?? "",
      });
    }
  },

  onRetry() {
    void this.loadAll();
  },

  onBack() {
    wx.navigateBack({
      fail: () => wx.switchTab({ url: "/pages/training/index" }),
    });
  },
});

/** 按模块聚合各桶题数，并换算堆叠条百分比 */
function aggregateModules(groups: MasteryQueueItem[][]): ModuleView[] {
  const byModule = new Map<
    string,
    { pending: number; due: number; stable: number; invalid: number }
  >();
  const add = (items: MasteryQueueItem[], key: "pending" | "due" | "stable" | "invalid") => {
    for (const item of items) {
      const name = item.moduleName?.trim() || "行测";
      const slot = byModule.get(name) ?? { pending: 0, due: 0, stable: 0, invalid: 0 };
      slot[key] += item.questionCount;
      byModule.set(name, slot);
    }
  };
  groups.forEach((items, index) => {
    add(items, (["pending", "due", "stable", "invalid"] as const)[index] ?? "pending");
  });

  const modules: ModuleView[] = [];
  for (const [name, slot] of byModule) {
    const total = slot.pending + slot.due + slot.stable + slot.invalid;
    if (total <= 0) continue;
    modules.push({
      name,
      totalQuestions: total,
      pendingCount: slot.pending,
      dueCount: slot.due,
      stableCount: slot.stable,
      invalidatedCount: slot.invalid,
      segPending: Math.round((slot.pending / total) * 100),
      segDue: Math.round((slot.due / total) * 100),
      segStable: Math.round((slot.stable / total) * 100),
      segInvalidated: Math.round((slot.invalid / total) * 100),
    });
  }
  // 稳定掌握题数多的模块排前面
  return modules.sort(
    (a, b) => b.stableCount - a.stableCount || b.totalQuestions - a.totalQuestions,
  );
}

function presentInvalidated(item: MasteryQueueItem): InvalidatedView {
  return {
    itemId: item.itemId,
    module: item.moduleName?.trim() || "行测",
    questionCount: item.questionCount,
    dateLabel: dateLabel(item.createdAt),
  };
}

function dateLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

function generatedAtLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const two = (n: number) => String(n).padStart(2, "0");
  return `数据更新于 ${two(d.getMonth() + 1)}.${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`;
}
