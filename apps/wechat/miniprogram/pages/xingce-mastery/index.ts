import { ApiClient, ApiClientError } from "../../api/client";
import {
  XingceLearningApi,
  type XingceMasteryQueueBucket,
  type XingceMasteryQueueSummary,
} from "../../api/xingce-learning";
import { routes } from "../../app/navigation/routes";
import { currentAuthSession, type AuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import {
  assertMasteryQueuePage,
  emptyMasteryQueueSummary,
  isMasteryQueueBucket,
  masteryQueueActionTarget,
} from "../../features/xingce/mastery/contracts";
import {
  XingceMasteryPageRuntimeRegistry,
  type XingceMasteryPageRuntime,
} from "../../features/xingce/mastery/page-runtime";
import {
  generatedAtLabel,
  masteryQueueEmptyCopy,
  masteryQueueTabs,
  presentMasteryQueueItem,
  type MasteryQueueItemView,
  type MasteryQueueTabView,
} from "../../features/xingce/mastery/presentation";

type MasteryPageStatus = "loading" | "ready" | "error" | "unavailable";

interface XingceMasteryPageData {
  status: MasteryPageStatus;
  activeBucket: XingceMasteryQueueBucket;
  summary: XingceMasteryQueueSummary;
  tabs: MasteryQueueTabView[];
  items: MasteryQueueItemView[];
  emptyTitle: string;
  emptyDescription: string;
  generatedAtLabel: string;
  loadingMore: boolean;
  hasMore: boolean;
  errorTitle: string;
  errorMessage: string;
  requestId: string;
  partialErrorMessage: string;
  partialRequestId: string;
  showLoginAction: boolean;
}

interface XingceMasteryPageCustom {
  retry(): void;
  retryLoadMore(): void;
  goLogin(): void;
  selectBucket(event: WechatMiniprogram.BaseEvent): void;
  loadMore(): void;
  openItem(event: WechatMiniprogram.BaseEvent): Promise<void>;
}

type XingceMasteryPage = WechatMiniprogram.Page.Instance<
  XingceMasteryPageData,
  XingceMasteryPageCustom
>;

const defaultBucket: XingceMasteryQueueBucket = "pending_reflection";
const pageSize = 30;
const runtimes = new XingceMasteryPageRuntimeRegistry<XingceMasteryPage>();

Page<XingceMasteryPageData, XingceMasteryPageCustom>({
  data: initialData(defaultBucket),

  onLoad() {
    const session = currentAuthSession();
    if (!session) {
      showLoggedOut(this, "登录后才能查看错题与掌握记录。");
      return;
    }
    const runtime = attachRuntime(this, session, defaultBucket);
    void loadPage(this, runtime, false);
  },

  onShow() {
    const session = currentAuthSession();
    const runtime = runtimes.get(this);
    if (!session) {
      if (runtime) runtimes.detach(this);
      showLoggedOut(this, "登录状态已失效，旧账号的掌握记录已从当前页面清除。");
      return;
    }
    if (runtime && runtime.ownerUserId === session.userId) return;
    const bucket = runtime?.bucket ?? this.data.activeBucket;
    const nextRuntime = attachRuntime(this, session, bucket);
    resetForAccount(this, bucket);
    void loadPage(this, nextRuntime, false);
  },

  onUnload() {
    runtimes.detach(this);
  },

  async onPullDownRefresh() {
    const runtime = runtimes.get(this);
    if (runtime) await loadPage(this, runtime, false);
    await wx.stopPullDownRefresh();
  },

  retry() {
    const runtime = runtimes.get(this);
    if (runtime) void loadPage(this, runtime, false);
  },

  retryLoadMore() {
    const runtime = runtimes.get(this);
    if (runtime?.nextCursor) void loadPage(this, runtime, true);
  },

  goLogin() {
    void wx.redirectTo({ url: routes.login });
  },

  selectBucket(event) {
    const bucketValue = String(event.currentTarget.dataset.bucket ?? "");
    const runtime = runtimes.get(this);
    if (!runtime || !isMasteryQueueBucket(bucketValue) || bucketValue === runtime.bucket) return;
    runtime.requestEpoch += 1;
    runtime.requestInFlight = false;
    runtime.loadingMore = false;
    runtime.bucket = bucketValue;
    runtime.nextCursor = null;
    this.setData({
      ...initialData(bucketValue),
      summary: this.data.summary,
      tabs: masteryQueueTabs(this.data.summary, bucketValue),
    });
    void loadPage(this, runtime, false);
  },

  loadMore() {
    const runtime = runtimes.get(this);
    if (!runtime?.nextCursor || runtime.loadingMore || runtime.requestInFlight) return;
    void loadPage(this, runtime, true);
  },

  async openItem(event) {
    const itemId = String(event.currentTarget.dataset.itemid ?? "");
    const item = this.data.items.find((candidate) => candidate.itemId === itemId);
    if (!item || !item.actionAvailable) return;
    if (item.actionState === "awaiting_plan") {
      const confirmation = await wx.showModal({
        title: "复测计划尚未发布",
        content:
          "这条学习记录已经到期，但今天还没有安排对应复测。岸岸不会临时拼题，请回到今日计划查看最新安排。",
        confirmText: "返回今日",
        showCancel: false,
      });
      if (confirmation.confirm) await wx.switchTab({ url: routes.today });
      return;
    }
    if (item.actionState !== "ready") return;
    const target = masteryQueueActionTarget(item.source);
    if (!target) {
      this.setData({
        partialErrorMessage: "执行入口与当前掌握记录不一致，已停止跳转。请刷新队列。",
        partialRequestId: "",
      });
      return;
    }
    if (target.mode === "switchTab") await wx.switchTab({ url: target.url });
    else await wx.navigateTo({ url: target.url });
  },
});

function attachRuntime(
  page: XingceMasteryPage,
  session: AuthSession,
  bucket: XingceMasteryQueueBucket,
): XingceMasteryPageRuntime {
  return runtimes.attach(page, {
    api: new XingceLearningApi(new ApiClient(environment.apiBaseUrl, session.accessToken)),
    ownerUserId: session.userId,
    bucket,
    nextCursor: null,
    requestEpoch: 0,
    requestInFlight: false,
    loadingMore: false,
    disposed: false,
  });
}

async function loadPage(
  page: XingceMasteryPage,
  runtime: XingceMasteryPageRuntime,
  append: boolean,
): Promise<void> {
  if (runtime.requestInFlight || (append && !runtime.nextCursor)) return;
  const bucket = runtime.bucket;
  const cursor = append ? runtime.nextCursor : null;
  if (append && !cursor) return;
  const epoch = ++runtime.requestEpoch;
  runtime.requestInFlight = true;
  runtime.loadingMore = append;
  if (append) {
    page.setData({ loadingMore: true, partialErrorMessage: "", partialRequestId: "" });
  } else {
    runtime.nextCursor = null;
    page.setData({
      status: "loading",
      activeBucket: bucket,
      tabs: masteryQueueTabs(page.data.summary, bucket),
      items: [],
      loadingMore: false,
      hasMore: false,
      errorTitle: "",
      errorMessage: "",
      requestId: "",
      partialErrorMessage: "",
      partialRequestId: "",
      showLoginAction: false,
    });
  }

  try {
    const response = await runtime.api.getMasteryQueue({
      bucket,
      pageSize,
      ...(cursor ? { cursor } : {}),
    });
    if (!runtimes.owns(page, runtime, epoch) || !currentAccountOwns(runtime)) return;
    const queue = assertMasteryQueuePage(response.data, bucket);
    const sourceItems = append
      ? deduplicateItems(page.data.items, queue.items.map(presentMasteryQueueItem))
      : queue.items.map(presentMasteryQueueItem);
    runtime.nextCursor = queue.nextCursor;
    const empty = masteryQueueEmptyCopy(bucket);
    page.setData({
      status: "ready",
      activeBucket: bucket,
      summary: queue.summary,
      tabs: masteryQueueTabs(queue.summary, bucket),
      items: sourceItems,
      emptyTitle: empty.title,
      emptyDescription: empty.description,
      generatedAtLabel: generatedAtLabel(queue.generatedAt),
      loadingMore: false,
      hasMore: queue.nextCursor !== null,
      errorTitle: "",
      errorMessage: "",
      requestId: "",
      partialErrorMessage: "",
      partialRequestId: "",
      showLoginAction: false,
    });
  } catch (error) {
    if (!runtimes.owns(page, runtime, epoch)) return;
    if (!currentAccountOwns(runtime)) {
      runtimes.detach(page);
      showLoggedOut(page, "账号已经变更，旧账号的掌握记录已从当前页面清除。");
      return;
    }
    if (error instanceof ApiClientError && error.statusCode === 401) {
      runtimes.detach(page);
      showLoggedOut(page, "登录状态已过期，请重新登录后加载掌握队列。");
      return;
    }
    const message = error instanceof Error ? error.message : "掌握队列加载失败。";
    const requestId = error instanceof ApiClientError ? error.requestId : "";
    if (append && page.data.items.length > 0) {
      page.setData({
        status: "ready",
        loadingMore: false,
        hasMore: runtime.nextCursor !== null,
        partialErrorMessage: `下一页暂未加载：${message}`,
        partialRequestId: requestId,
      });
    } else {
      page.setData({
        status: "error",
        errorTitle: "错题与掌握队列尚未载入",
        errorMessage: message,
        requestId,
        loadingMore: false,
        hasMore: false,
      });
    }
  } finally {
    if (runtimes.owns(page, runtime, epoch)) {
      runtime.requestInFlight = false;
      runtime.loadingMore = false;
    }
  }
}

function deduplicateItems(
  current: MasteryQueueItemView[],
  incoming: MasteryQueueItemView[],
): MasteryQueueItemView[] {
  const seen = new Set(current.map((item) => item.itemId));
  return [...current, ...incoming.filter((item) => !seen.has(item.itemId))];
}

function currentAccountOwns(runtime: XingceMasteryPageRuntime): boolean {
  return currentAuthSession()?.userId === runtime.ownerUserId;
}

function showLoggedOut(page: XingceMasteryPage, message: string): void {
  page.setData({
    ...initialData(page.data.activeBucket),
    status: "unavailable",
    errorTitle: "需要重新登录",
    errorMessage: message,
    showLoginAction: true,
  });
}

function resetForAccount(page: XingceMasteryPage, bucket: XingceMasteryQueueBucket): void {
  page.setData(initialData(bucket));
}

function initialData(bucket: XingceMasteryQueueBucket): XingceMasteryPageData {
  const empty = masteryQueueEmptyCopy(bucket);
  return {
    status: "loading",
    activeBucket: bucket,
    summary: { ...emptyMasteryQueueSummary },
    tabs: masteryQueueTabs(emptyMasteryQueueSummary, bucket),
    items: [],
    emptyTitle: empty.title,
    emptyDescription: empty.description,
    generatedAtLabel: "",
    loadingMore: false,
    hasMore: false,
    errorTitle: "",
    errorMessage: "",
    requestId: "",
    partialErrorMessage: "",
    partialRequestId: "",
    showLoginAction: false,
  };
}
