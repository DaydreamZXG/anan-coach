import { ApiClient, ApiClientError } from "../../../api/client";
import { QuestionFavoritesApi } from "../../shared/api/question-favorites";
import { routes } from "../../../app/navigation/routes";
import {
  currentAuthSession,
  sameSessionOwner,
  type ActiveAuthSession,
} from "../../../auth/session";
import { environment } from "../../../config/environment";
import { executeQuestionFavoriteCommand } from "../../shared/features/xingce/favorites/command";
import {
  QuestionFavoritesPageRuntimeRegistry,
  type QuestionFavoritesPageRuntime,
} from "../../shared/features/xingce/favorites/page-runtime";
import {
  presentQuestionFavoriteItem,
  type QuestionFavoriteItemView,
} from "../../shared/features/xingce/favorites/presentation";

type FavoritesPageStatus = "loading" | "ready" | "error" | "unavailable";

interface QuestionFavoritesPageData {
  status: FavoritesPageStatus;
  items: QuestionFavoriteItemView[];
  loadingMore: boolean;
  hasMore: boolean;
  errorTitle: string;
  errorMessage: string;
  requestId: string;
  partialErrorMessage: string;
  partialRequestId: string;
  refreshMessage: string;
  showLoginAction: boolean;
}

interface QuestionFavoritesPageCustom {
  retry(): void;
  retryLoadMore(): void;
  goLogin(): void;
  loadMore(): void;
  cancelFavorite(event: WechatMiniprogram.BaseEvent): Promise<void>;
}

type QuestionFavoritesPage = WechatMiniprogram.Page.Instance<
  QuestionFavoritesPageData,
  QuestionFavoritesPageCustom
>;

const pageSize = 30;
const runtimes = new QuestionFavoritesPageRuntimeRegistry<QuestionFavoritesPage>();

Page<QuestionFavoritesPageData, QuestionFavoritesPageCustom>({
  data: initialData(),

  onLoad() {
    const session = currentAuthSession();
    if (!session) {
      showLoggedOut(this, "登录后才能查看跨设备同步的收藏题目。");
      return;
    }
    const runtime = attachRuntime(this, session);
    void loadFavorites(this, runtime, false);
  },

  onShow() {
    const session = currentAuthSession();
    const runtime = runtimes.get(this);
    if (!session) {
      if (runtime) runtimes.detach(this);
      showLoggedOut(this, "登录状态已失效，旧账号的收藏题目已从当前页面清除。");
      return;
    }
    if (runtime && sameSessionOwner(session, runtime.ownerSession)) {
      runtime.visible = true;
      if (!runtime.requestInFlight) void loadFavorites(this, runtime, false);
      return;
    }
    const nextRuntime = attachRuntime(this, session);
    this.setData(initialData());
    void loadFavorites(this, nextRuntime, false);
  },

  onHide() {
    const runtime = runtimes.get(this);
    if (!runtime) return;
    runtime.visible = false;
    runtime.requestEpoch += 1;
    runtime.requestInFlight = false;
    runtime.mutationEpoch += 1;
  },

  onUnload() {
    runtimes.detach(this);
  },

  async onPullDownRefresh() {
    const runtime = runtimes.get(this);
    if (runtime) {
      runtime.visible = true;
      await loadFavorites(this, runtime, false);
    }
    await wx.stopPullDownRefresh();
  },

  retry() {
    const runtime = runtimes.get(this);
    if (runtime) void loadFavorites(this, runtime, false);
  },

  retryLoadMore() {
    const runtime = runtimes.get(this);
    if (runtime?.nextCursor) void loadFavorites(this, runtime, true);
  },

  goLogin() {
    void wx.redirectTo({ url: routes.login });
  },

  loadMore() {
    const runtime = runtimes.get(this);
    if (!runtime?.nextCursor || runtime.requestInFlight) return;
    void loadFavorites(this, runtime, true);
  },

  async cancelFavorite(event) {
    const runtime = runtimes.get(this);
    const questionId = String(event.currentTarget.dataset.questionid ?? "").trim();
    const item = this.data.items.find((candidate) => candidate.questionId === questionId);
    if (
      !runtime ||
      !item ||
      runtime.mutationInFlight.size > 0 ||
      item.mutationStatus === "saving"
    ) {
      return;
    }
    if (!sameSessionOwner(currentAuthSession(), runtime.ownerSession)) {
      runtimes.detach(this);
      showLoggedOut(this, "账号已经变更，请重新登录后查看当前账号的收藏题目。");
      return;
    }
    const mutationEpoch = ++runtime.mutationEpoch;
    if (runtime.requestInFlight) {
      runtime.requestEpoch += 1;
      runtime.requestInFlight = false;
    }
    runtime.refreshAfterRequest = true;
    runtime.mutationInFlight.add(questionId);
    patchItem(this, questionId, {
      mutationStatus: "saving",
      mutationMessage: "",
      mutationRequestId: "",
    });
    try {
      const outcome = await executeQuestionFavoriteCommand({
        api: runtime.api,
        ownerUserId: runtime.ownerSession.userId,
        questionId,
        desiredState: "not_favorited",
      });
      if (!ownsMutation(this, runtime, mutationEpoch, questionId)) {
        invalidateFavoriteOwnerChange(this, runtime);
        return;
      }
      if (outcome.kind === "succeeded" && outcome.favoriteState === "not_favorited") {
        this.setData({
          items: this.data.items.filter((candidate) => candidate.questionId !== questionId),
          refreshMessage: "已取消收藏。下拉刷新可同步另一台设备的最新变化。",
        });
        void wx.showToast({ title: "已取消收藏", icon: "none", duration: 1600 });
        return;
      }
      if (outcome.kind === "unknown") {
        patchItem(this, questionId, {
          mutationStatus: "unknown",
          mutationMessage: outcome.message,
          mutationRequestId: outcome.requestId,
        });
        return;
      }
      patchItem(this, questionId, {
        mutationStatus: "error",
        mutationMessage:
          outcome.kind === "terminal"
            ? `取消没有生效：${outcome.message}`
            : "取消结果与服务端状态不一致，请刷新后再试。",
        mutationRequestId: outcome.requestId,
      });
    } catch (error) {
      if (!ownsMutation(this, runtime, mutationEpoch, questionId)) {
        invalidateFavoriteOwnerChange(this, runtime);
        return;
      }
      patchItem(this, questionId, {
        mutationStatus: "error",
        mutationMessage: error instanceof Error ? error.message : "取消没有生效，请刷新后再试。",
        mutationRequestId: error instanceof ApiClientError ? error.requestId : "",
      });
    } finally {
      runtime.mutationInFlight.delete(questionId);
      if (
        runtimes.attached(this, runtime) &&
        runtime.visible &&
        runtime.mutationInFlight.size === 0 &&
        sameSessionOwner(currentAuthSession(), runtime.ownerSession)
      ) {
        runtime.refreshAfterRequest = false;
        scheduleRefresh(this, runtime);
      }
    }
  },
});

function attachRuntime(
  page: QuestionFavoritesPage,
  session: ActiveAuthSession,
): QuestionFavoritesPageRuntime {
  return runtimes.attach(page, {
    api: new QuestionFavoritesApi(new ApiClient(environment.apiBaseUrl, session.accessToken)),
    ownerSession: { ...session },
  });
}

async function loadFavorites(
  page: QuestionFavoritesPage,
  runtime: QuestionFavoritesPageRuntime,
  append: boolean,
): Promise<void> {
  if (runtime.mutationInFlight.size > 0) {
    runtime.refreshAfterRequest = true;
    return;
  }
  if (runtime.requestInFlight || (append && !runtime.nextCursor)) return;
  if (!sameSessionOwner(currentAuthSession(), runtime.ownerSession)) {
    runtimes.detach(page);
    showLoggedOut(page, "账号已经变更，请重新登录后查看当前账号的收藏题目。");
    return;
  }
  const cursor = append ? runtime.nextCursor : null;
  const requestEpoch = ++runtime.requestEpoch;
  runtime.requestInFlight = true;
  if (append) {
    page.setData({
      loadingMore: true,
      partialErrorMessage: "",
      partialRequestId: "",
    });
  } else {
    runtime.nextCursor = null;
    page.setData({
      status: "loading",
      items: [],
      loadingMore: false,
      hasMore: false,
      errorTitle: "",
      errorMessage: "",
      requestId: "",
      partialErrorMessage: "",
      partialRequestId: "",
      refreshMessage: "",
      showLoginAction: false,
    });
  }

  try {
    const response = await runtime.api.list({
      pageSize,
      ...(cursor ? { cursor } : {}),
    });
    if (!ownsRequest(page, runtime, requestEpoch)) {
      invalidateFavoriteOwnerChange(page, runtime);
      return;
    }
    const incoming = response.data.items
      .filter((item) => item.favoriteState === "favorited")
      .map(presentQuestionFavoriteItem);
    runtime.nextCursor = response.data.nextCursor ?? null;
    page.setData({
      status: "ready",
      items: append ? deduplicateItems(page.data.items, incoming) : incoming,
      loadingMore: false,
      hasMore: runtime.nextCursor !== null,
      errorTitle: "",
      errorMessage: "",
      requestId: "",
      partialErrorMessage: "",
      partialRequestId: "",
      showLoginAction: false,
    });
  } catch (error) {
    if (!ownsRequest(page, runtime, requestEpoch)) {
      invalidateFavoriteOwnerChange(page, runtime);
      return;
    }
    if (error instanceof ApiClientError && error.statusCode === 401) {
      runtimes.detach(page);
      showLoggedOut(page, "登录状态已过期，请重新登录后加载收藏题目。");
      return;
    }
    const message = error instanceof Error ? error.message : "收藏题目没有加载成功。";
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
        errorTitle: "收藏题目尚未载入",
        errorMessage: message,
        requestId,
        loadingMore: false,
        hasMore: false,
      });
    }
  } finally {
    if (runtimes.attached(page, runtime) && runtime.requestEpoch === requestEpoch) {
      runtime.requestInFlight = false;
      if (runtime.refreshAfterRequest && runtime.visible) {
        runtime.refreshAfterRequest = false;
        void loadFavorites(page, runtime, false);
      }
    }
  }
}

function ownsRequest(
  page: QuestionFavoritesPage,
  runtime: QuestionFavoritesPageRuntime,
  requestEpoch: number,
): boolean {
  return (
    runtimes.owns(page, runtime, requestEpoch) &&
    sameSessionOwner(currentAuthSession(), runtime.ownerSession)
  );
}

function ownsMutation(
  page: QuestionFavoritesPage,
  runtime: QuestionFavoritesPageRuntime,
  mutationEpoch: number,
  questionId: string,
): boolean {
  return (
    runtimes.owns(page, runtime) &&
    runtime.mutationEpoch === mutationEpoch &&
    runtime.mutationInFlight.has(questionId) &&
    sameSessionOwner(currentAuthSession(), runtime.ownerSession)
  );
}

function scheduleRefresh(page: QuestionFavoritesPage, runtime: QuestionFavoritesPageRuntime): void {
  if (runtime.mutationInFlight.size > 0) {
    runtime.refreshAfterRequest = true;
    return;
  }
  if (runtime.requestInFlight) {
    runtime.refreshAfterRequest = true;
    return;
  }
  void loadFavorites(page, runtime, false);
}

function invalidateFavoriteOwnerChange(
  page: QuestionFavoritesPage,
  runtime: QuestionFavoritesPageRuntime,
): void {
  if (
    runtimes.attached(page, runtime) &&
    !sameSessionOwner(currentAuthSession(), runtime.ownerSession)
  ) {
    runtimes.detach(page);
    showLoggedOut(page, "账号已经变更，旧账号的收藏题目已从当前页面清除。");
  }
}

function patchItem(
  page: QuestionFavoritesPage,
  questionId: string,
  patch: Pick<QuestionFavoriteItemView, "mutationStatus" | "mutationMessage" | "mutationRequestId">,
): void {
  page.setData({
    items: page.data.items.map((item) =>
      item.questionId === questionId ? { ...item, ...patch } : item,
    ),
  });
}

function deduplicateItems(
  current: QuestionFavoriteItemView[],
  incoming: QuestionFavoriteItemView[],
): QuestionFavoriteItemView[] {
  const seen = new Set(current.map((item) => item.questionId));
  return [...current, ...incoming.filter((item) => !seen.has(item.questionId))];
}

function showLoggedOut(page: QuestionFavoritesPage, message: string): void {
  page.setData({
    ...initialData(),
    status: "unavailable",
    errorTitle: "需要重新登录",
    errorMessage: message,
    showLoginAction: true,
  });
}

function initialData(): QuestionFavoritesPageData {
  return {
    status: "loading",
    items: [],
    loadingMore: false,
    hasMore: false,
    errorTitle: "",
    errorMessage: "",
    requestId: "",
    partialErrorMessage: "",
    partialRequestId: "",
    refreshMessage: "",
    showLoginAction: false,
  };
}
