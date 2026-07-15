import { ApiClient, ApiClientError } from "../../api/client";
import { XingceLearningApi, type XingceTaskSession } from "../../api/xingce-learning";
import { routes } from "../../app/navigation/routes";
import { currentAuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import {
  isXingceReflectionTag,
  xingceReflectionTagLabels,
  xingceReflectionTags,
  type XingceReflectionTag,
} from "../../features/xingce/real_attempt/contracts";
import {
  presentXingceResult,
  type XingceResultView,
} from "../../features/xingce/real_attempt/presentation";
import {
  clearPendingXingceCauseDecision,
  clearPendingXingceReflection,
  loadPendingXingceCauseDecision,
  loadPendingXingceReflection,
  storePendingXingceCauseDecision,
  storePendingXingceReflection,
} from "../../features/xingce/real_attempt/pending-store";
import { publishLatestTodayDashboard } from "../../features/planning/runtime-state";
import { createIdempotencyKey } from "../../platform/command-id";

type ReflectionStatus = "loading" | "ready" | "error" | "unavailable";
type ReflectionPhase = "reflection" | "cause_decision" | "completed";

interface ReflectionPageData {
  status: ReflectionStatus;
  phase: ReflectionPhase;
  errorTitle: string;
  errorMessage: string;
  showLoginAction: boolean;
  result: XingceResultView | null;
  rawText: string;
  strategyNote: string;
  doubtNote: string;
  tagOptions: Array<{ code: XingceReflectionTag; label: string; selected: boolean }>;
  replacementCause: string;
  interactionLocked: boolean;
  pendingMessage: string;
  hasPendingWrite: boolean;
}

interface ReflectionPageCustom {
  loadReview(): Promise<void>;
  retry(): void;
  goLogin(): void;
  exit(): Promise<void>;
  inputRawText(event: WechatMiniprogram.Input): void;
  inputStrategyNote(event: WechatMiniprogram.Input): void;
  inputDoubtNote(event: WechatMiniprogram.Input): void;
  toggleTag(event: WechatMiniprogram.BaseEvent): void;
  submitReflection(): Promise<void>;
  inputReplacementCause(event: WechatMiniprogram.Input): void;
  replaceCause(): Promise<void>;
  skipCause(): Promise<void>;
  returnToday(): void;
}

interface ReflectionRuntime {
  api: XingceLearningApi;
  ownerUserId: string;
  sessionId: string;
  reviewId: string;
  session: XingceTaskSession | null;
  selectedTags: Set<XingceReflectionTag>;
  disposed: boolean;
  requestEpoch: number;
  submitting: boolean;
}

type ReflectionPageInstance = WechatMiniprogram.Page.Instance<
  ReflectionPageData,
  ReflectionPageCustom
>;

const runtimes = new WeakMap<ReflectionPageInstance, ReflectionRuntime>();

Page<ReflectionPageData, ReflectionPageCustom>({
  data: {
    status: "loading",
    phase: "reflection",
    errorTitle: "",
    errorMessage: "",
    showLoginAction: false,
    result: null,
    rawText: "",
    strategyNote: "",
    doubtNote: "",
    tagOptions: tagOptions(new Set()),
    replacementCause: "",
    interactionLocked: false,
    pendingMessage: "",
    hasPendingWrite: false,
  },

  onLoad(options: Record<string, string | undefined>) {
    const sessionId = options.sessionId?.trim() ?? "";
    const reviewId = options.reviewId?.trim() ?? "";
    if (!sessionId || !reviewId) {
      this.setData({
        status: "error",
        errorTitle: "复盘地址不完整",
        errorMessage: "缺少服务端作答会话或复盘版本，不能写入孤立复盘。",
      });
      return;
    }
    if (environment.dataMode === "prototype") {
      this.setData({
        status: "unavailable",
        errorTitle: "演示数据不能写入复盘",
        errorMessage: "用户原话必须绑定真实作答结果与账号。",
      });
      return;
    }
    const auth = currentAuthSession();
    if (!auth) {
      this.setData({
        status: "unavailable",
        errorTitle: "登录后才能继续复盘",
        errorMessage: "复盘是个人学习档案，当前没有可用登录会话。",
        showLoginAction: true,
      });
      return;
    }
    const runtime: ReflectionRuntime = {
      api: new XingceLearningApi(new ApiClient(environment.apiBaseUrl, auth.accessToken)),
      ownerUserId: auth.userId,
      sessionId,
      reviewId,
      session: null,
      selectedTags: new Set(),
      disposed: false,
      requestEpoch: 0,
      submitting: false,
    };
    runtimes.set(this, runtime);
    void this.loadReview();
  },

  onShow() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    if (!currentAccountOwns(runtime)) {
      invalidateForAccountChange(this, runtime);
      return;
    }
    if (!runtime.session) return;
    void this.loadReview();
  },

  onUnload() {
    const runtime = runtimes.get(this);
    if (!runtime) return;
    runtime.disposed = true;
    runtime.requestEpoch += 1;
    runtimes.delete(this);
  },

  async loadReview() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    const epoch = ++runtime.requestEpoch;
    this.setData({ status: "loading", errorTitle: "", errorMessage: "" });
    try {
      const response = await runtime.api.getSession(runtime.sessionId);
      if (!isCurrent(this, runtime, epoch)) return;
      if (!currentAccountOwns(runtime)) {
        invalidateForAccountChange(this, runtime);
        return;
      }
      applySession(this, runtime, response.data);
    } catch (error) {
      if (!isCurrent(this, runtime, epoch)) return;
      handleError(this, error, "真实复盘状态尚未载入");
    }
  },

  retry() {
    const runtime = runtimes.get(this);
    if (!runtime) return;
    if (loadPendingXingceReflection(runtime.ownerUserId, runtime.reviewId)) {
      void this.submitReflection();
      return;
    }
    if (loadPendingXingceCauseDecision(runtime.ownerUserId, runtime.reviewId)) {
      void decideCause(this, runtime);
      return;
    }
    void this.loadReview();
  },

  goLogin() {
    void wx.redirectTo({ url: routes.login });
  },

  async exit() {
    if (this.data.phase !== "completed") {
      const result = await wx.showModal({
        title: "本次复盘还没完成",
        content: "退出不会伪造完成记录，今日任务会保持待复盘状态。",
        confirmText: "仍要退出",
      });
      if (!result.confirm) return;
    }
    void wx.navigateBack();
  },

  inputRawText(event) {
    if (!this.data.interactionLocked) this.setData({ rawText: event.detail.value });
  },

  inputStrategyNote(event) {
    if (!this.data.interactionLocked) this.setData({ strategyNote: event.detail.value });
  },

  inputDoubtNote(event) {
    if (!this.data.interactionLocked) this.setData({ doubtNote: event.detail.value });
  },

  toggleTag(event) {
    const runtime = runtimes.get(this);
    const code = String(event.currentTarget.dataset.code ?? "");
    if (!runtime || this.data.interactionLocked || !isXingceReflectionTag(code)) return;
    if (runtime.selectedTags.has(code)) runtime.selectedTags.delete(code);
    else runtime.selectedTags.add(code);
    this.setData({ tagOptions: tagOptions(runtime.selectedTags) });
  },

  async submitReflection() {
    const runtime = runtimes.get(this);
    const review = runtime?.session?.review;
    if (!runtime || !review || runtime.submitting) return;
    let pending = loadPendingXingceReflection(runtime.ownerUserId, runtime.reviewId);
    const rawText = this.data.rawText.trim();
    if (!pending && (!rawText || runtime.selectedTags.size === 0)) {
      void wx.showToast({ title: "请写下真实想法并至少选择一个标签", icon: "none" });
      return;
    }
    runtime.submitting = true;
    this.setData({ interactionLocked: true, pendingMessage: "正在写入你的原话复盘…" });
    try {
      if (!pending) {
        pending = {
          kind: "reflection",
          sessionId: runtime.sessionId,
          reviewId: runtime.reviewId,
          idempotencyKey: await createIdempotencyKey("xingce-reflection"),
          command: {
            expectedRevision: review.revision,
            rawText,
            tags: [...runtime.selectedTags],
            ...(this.data.strategyNote.trim()
              ? { strategyNote: this.data.strategyNote.trim() }
              : {}),
            ...(this.data.doubtNote.trim() ? { doubtNote: this.data.doubtNote.trim() } : {}),
          },
        };
        storePendingXingceReflection(runtime.ownerUserId, pending);
      }
      const response = await runtime.api.submitReflection(
        pending.reviewId,
        pending.command,
        pending.idempotencyKey,
      );
      if (!currentAccountOwns(runtime)) {
        invalidateForAccountChange(this, runtime);
        return;
      }
      clearPendingXingceReflection(runtime.ownerUserId, runtime.reviewId);
      applySession(this, runtime, response.data);
    } catch (error) {
      if (error instanceof ApiClientError && error.statusCode === 409) {
        clearPendingXingceReflection(runtime.ownerUserId, runtime.reviewId);
        await reloadAfterConflict(this, runtime);
        return;
      }
      handleWriteError(this, error, "复盘提交结果尚未确认，请重试同一命令。");
    } finally {
      runtime.submitting = false;
    }
  },

  inputReplacementCause(event) {
    if (!this.data.interactionLocked) this.setData({ replacementCause: event.detail.value });
  },

  async replaceCause() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.submitting) return;
    const replacementCause = this.data.replacementCause.trim();
    if (!replacementCause) {
      void wx.showToast({ title: "请写下你认可的错因", icon: "none" });
      return;
    }
    await prepareCauseDecision(this, runtime, "replace", replacementCause);
  },

  async skipCause() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.submitting) return;
    await prepareCauseDecision(this, runtime, "skip");
  },

  returnToday() {
    const plan = runtimes.get(this)?.session?.completion?.plan;
    if (plan) publishLatestTodayDashboard(plan);
    void wx.switchTab({ url: routes.today });
  },
});

async function prepareCauseDecision(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
  decision: "replace" | "skip",
  replacementCause?: string,
): Promise<void> {
  const review = runtime.session?.review;
  if (!review) return;
  const pending = {
    kind: "cause_decision" as const,
    sessionId: runtime.sessionId,
    reviewId: runtime.reviewId,
    idempotencyKey: await createIdempotencyKey("xingce-cause"),
    command: {
      expectedRevision: review.revision,
      decision,
      ...(replacementCause ? { replacementCause } : {}),
    },
  };
  storePendingXingceCauseDecision(runtime.ownerUserId, pending);
  await decideCause(page, runtime);
}

async function decideCause(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
): Promise<void> {
  if (runtime.submitting) return;
  const pending = loadPendingXingceCauseDecision(runtime.ownerUserId, runtime.reviewId);
  if (!pending) return;
  runtime.submitting = true;
  page.setData({ interactionLocked: true, pendingMessage: "正在结算复盘与今日任务…" });
  try {
    const response = await runtime.api.decideCause(
      pending.reviewId,
      pending.command,
      pending.idempotencyKey,
    );
    if (!currentAccountOwns(runtime)) {
      invalidateForAccountChange(page, runtime);
      return;
    }
    clearPendingXingceCauseDecision(runtime.ownerUserId, runtime.reviewId);
    applySession(page, runtime, response.data);
  } catch (error) {
    if (error instanceof ApiClientError && error.statusCode === 409) {
      clearPendingXingceCauseDecision(runtime.ownerUserId, runtime.reviewId);
      await reloadAfterConflict(page, runtime);
      return;
    }
    handleWriteError(page, error, "错因决定尚未确认，请重试同一命令。");
  } finally {
    runtime.submitting = false;
  }
}

async function reloadAfterConflict(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
): Promise<void> {
  try {
    const current = await runtime.api.getSession(runtime.sessionId);
    if (!currentAccountOwns(runtime)) {
      invalidateForAccountChange(page, runtime);
      return;
    }
    applySession(page, runtime, current.data);
    page.setData({
      errorMessage: "服务端复盘版本已更新，已加载权威状态；未确认的本地文字仍保留在输入框中。",
      interactionLocked: false,
    });
  } catch (error) {
    handleWriteError(page, error, "无法重新加载服务端权威复盘状态。");
  }
}

function applySession(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
  session: XingceTaskSession,
): void {
  const review = session.review;
  if (!review || review.reviewId !== runtime.reviewId || session.sessionId !== runtime.sessionId) {
    throw new Error("服务端复盘与当前作答会话不匹配。");
  }
  runtime.session = session;
  const pendingReflection = loadPendingXingceReflection(runtime.ownerUserId, runtime.reviewId);
  const pendingCause = loadPendingXingceCauseDecision(runtime.ownerUserId, runtime.reviewId);
  const existingTags = review.reflection?.tags.filter(isXingceReflectionTag) ?? [];
  const pendingTags = pendingReflection?.command.tags.filter(isXingceReflectionTag) ?? [];
  if (pendingTags.length > 0) runtime.selectedTags = new Set(pendingTags);
  else if (existingTags.length > 0) runtime.selectedTags = new Set(existingTags);
  const phase: ReflectionPhase =
    review.state === "reflection_required"
      ? "reflection"
      : review.state === "cause_decision_required"
        ? "cause_decision"
        : "completed";
  page.setData({
    status: "ready",
    phase,
    errorTitle: "",
    errorMessage: "",
    result: presentXingceResult(session),
    rawText: pendingReflection?.command.rawText ?? review.reflection?.rawText ?? page.data.rawText,
    strategyNote:
      pendingReflection?.command.strategyNote ??
      review.reflection?.strategyNote ??
      page.data.strategyNote,
    doubtNote:
      pendingReflection?.command.doubtNote ?? review.reflection?.doubtNote ?? page.data.doubtNote,
    tagOptions: tagOptions(runtime.selectedTags),
    replacementCause:
      pendingCause?.command.replacementCause ??
      review.causeDecision?.replacementCause ??
      page.data.replacementCause,
    interactionLocked: pendingReflection !== null || pendingCause !== null,
    pendingMessage:
      pendingReflection !== null || pendingCause !== null
        ? "检测到一次结果未知的写入，必须按原命令重试。"
        : "",
    hasPendingWrite: pendingReflection !== null || pendingCause !== null,
  });
  if (session.completion?.plan) publishLatestTodayDashboard(session.completion.plan);
}

function tagOptions(selected: ReadonlySet<XingceReflectionTag>): ReflectionPageData["tagOptions"] {
  return xingceReflectionTags.map((code) => ({
    code,
    label: xingceReflectionTagLabels[code],
    selected: selected.has(code),
  }));
}

function handleWriteError(page: ReflectionPageInstance, error: unknown, fallback: string): void {
  if (error instanceof ApiClientError && error.statusCode === 401) {
    page.setData({
      status: "unavailable",
      errorTitle: "登录状态已失效",
      errorMessage: "待提交命令仍按原账号隔离保存，重新登录后可以继续。",
      showLoginAction: true,
      pendingMessage: "",
      hasPendingWrite: true,
    });
    return;
  }
  page.setData({
    status: "error",
    errorTitle: "服务端尚未确认本次写入",
    errorMessage: error instanceof Error ? error.message : fallback,
    interactionLocked: true,
    pendingMessage: fallback,
    hasPendingWrite: true,
  });
}

function handleError(page: ReflectionPageInstance, error: unknown, title: string): void {
  const expired = error instanceof ApiClientError && error.statusCode === 401;
  page.setData({
    status: expired ? "unavailable" : "error",
    errorTitle: expired ? "登录状态已失效" : title,
    errorMessage: error instanceof Error ? error.message : "服务端没有返回可确认状态。",
    showLoginAction: expired,
  });
}

function isCurrent(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
  epoch: number,
): boolean {
  return runtimes.get(page) === runtime && !runtime.disposed && runtime.requestEpoch === epoch;
}

function currentAccountOwns(runtime: ReflectionRuntime): boolean {
  return currentAuthSession()?.userId === runtime.ownerUserId;
}

function invalidateForAccountChange(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
): void {
  if (runtimes.get(page) !== runtime) return;
  runtime.disposed = true;
  runtime.requestEpoch += 1;
  runtimes.delete(page);
  page.setData({
    status: "unavailable",
    errorTitle: "账号已经变更",
    errorMessage: "旧账号的复盘内容已从当前页面清除，请以当前账号重新进入今日计划。",
    showLoginAction: true,
    result: null,
    rawText: "",
    strategyNote: "",
    doubtNote: "",
    replacementCause: "",
    tagOptions: tagOptions(new Set()),
    interactionLocked: true,
  });
}
