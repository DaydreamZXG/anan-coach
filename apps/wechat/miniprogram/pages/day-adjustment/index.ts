import { PlanningApi } from "../../api/planning";
import type {
  DailyCapacityCommand,
  DailyCapacityPreview,
  TodayDashboard,
} from "../../api/generated/types.gen";
import { ApiClientError } from "../../api/client";
import { routes } from "../../app/navigation/routes";
import { navigateBackOr } from "../../app/navigation/safe-back";
import { currentAuthSession, sameSessionOwner, type ActiveAuthSession } from "../../auth/session";
import {
  plannedDuration,
  presentCapacityChanges,
  type CapacityChangeView,
} from "../../features/planning/capacity-preview";
import { errorView, requireAuthenticatedClient } from "../../features/learning-brain/presentation";
import { stableFingerprint } from "../../platform/command-id";
import {
  OperationAttemptStore,
  type OperationAttemptRecord,
} from "../../platform/operation-attempt-store";
import { formatCapacityDuration, prepareCapacityCommand } from "../today/capacity";

interface EnergyOption {
  label: string;
  value: number;
}

type XingceModule = NonNullable<DailyCapacityCommand["preferredXingceModule"]>;

interface FocusOption {
  label: string;
  value: XingceModule | "";
}

const ENERGY_OPTIONS: readonly EnergyOption[] = [
  { label: "有点累", value: 2 },
  { label: "正常", value: 3 },
  { label: "状态不错", value: 4 },
];

const TIME_PRESETS = [
  { label: "1 小时", minutes: 60 },
  { label: "2 小时", minutes: 120 },
  { label: "4 小时", minutes: 240 },
  { label: "今天不学", minutes: 0 },
] as const;

const FOCUS_OPTIONS: readonly FocusOption[] = [
  { label: "综合安排", value: "" },
  { label: "言语", value: "言语理解与表达" },
  { label: "数量", value: "数量关系" },
  { label: "判断", value: "判断推理" },
  { label: "资料分析", value: "资料分析" },
  { label: "常识", value: "常识判断" },
  { label: "政治理论", value: "政治理论" },
];

const MINUTES_STEP = 30;
const MAX_VISIBLE_MINUTES = 720;
const OPERATION_ATTEMPT_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;
const CAPACITY_ATTEMPT_SCOPE = "today-capacity:update@v1";
const UNKNOWN_OUTCOME_MESSAGE =
  "保存请求已经发出，但结果还在确认中。请再次确认，系统会复用同一次请求，不会重复调整。";
const OLD_DAY_ATTEMPT_MESSAGE =
  "上一个学习日的调整结果已无法继续确认，请按今天的状态重新生成预览。";
const INVALID_ATTEMPT_MESSAGE =
  "上一次调整的恢复信息无法校验，已停止自动重试。请按当前状态重新生成预览。";

interface CapacityCanonicalInput {
  localDate: string;
  command: DailyCapacityCommand;
}

interface CapacityAttemptRuntime {
  editBoundary: boolean;
  loadGeneration: number;
  lifecycleGeneration: number;
  visible: boolean;
  disposed: boolean;
}

interface CapacityWriteGroup {
  pending: Set<Promise<void>>;
  successSubscribers: Set<() => void>;
}

const attemptRuntimes = new WeakMap<object, CapacityAttemptRuntime>();
const inFlightCapacityWrites = new Map<string, Promise<void>>();
const inFlightCapacityWriteGroups = new Map<string, CapacityWriteGroup>();

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    dashboard: null as TodayDashboard | null,
    step: "edit",
    minutes: 0,
    selectedDurationLabel: "今天不安排",
    currentDurationLabel: "",
    currentTaskCount: 0,
    currentFocus: "",
    energy: 3,
    stress: 3,
    confidenceLevel: 3,
    energyOptions: ENERGY_OPTIONS,
    timePresets: TIME_PRESETS,
    focusOptions: FOCUS_OPTIONS,
    preferredXingceModule: "",
    xingceOnly: false,
    minutesStep: MINUTES_STEP,
    preview: null as DailyCapacityPreview | null,
    previewChanges: [] as CapacityChangeView[],
    previewHeadline: "",
    previewRationale: "",
    previewImpact: "",
    previewing: false,
    submitting: false,
    agentPrefillMinutes: null as number | null,
    agentPrefillModule: null as XingceModule | null,
    agentPrefillXingceOnly: false,
  },

  onLoad(options: Record<string, string | undefined> = {}) {
    const runtime = attemptRuntime(this);
    runtime.visible = true;
    runtime.disposed = false;
    runtime.lifecycleGeneration += 1;
    const focus = parseAgentPrefillFocus(options);
    this.setData({
      agentPrefillMinutes: parseAgentPrefillMinutes(options),
      agentPrefillModule: focus?.module ?? null,
      agentPrefillXingceOnly: focus?.xingceOnly ?? false,
    });
    void this.loadToday();
  },

  onShow() {
    const runtime = attemptRuntime(this);
    if (runtime.disposed || runtime.visible) return;
    runtime.visible = true;
    runtime.lifecycleGeneration += 1;
    void this.loadToday();
  },

  onHide() {
    invalidatePageLifecycle(this, false);
  },

  onUnload() {
    invalidatePageLifecycle(this, true);
  },

  async loadToday() {
    const originSession = currentAuthSession();
    const client = requireAuthenticatedClient();
    if (!originSession || !client) return;
    const runtime = attemptRuntime(this);
    if (!runtime.visible || runtime.disposed) return;
    const lifecycleGeneration = runtime.lifecycleGeneration;
    const loadGeneration = runtime.loadGeneration + 1;
    runtime.loadGeneration = loadGeneration;
    this.setData({
      status: "loading",
      errorMessage: "",
      requestId: "",
      step: "edit",
      preview: null,
      submitting: false,
    });
    try {
      const api = new PlanningApi(client);
      const { data } = await api.getToday();
      if (!ownsLoadResponse(runtime, lifecycleGeneration, loadGeneration, originSession)) {
        return;
      }

      const presentation = dashboardPresentation(data);
      const store = new OperationAttemptStore(originSession.userId);
      const unfinished = store.load(CAPACITY_ATTEMPT_SCOPE, Date.now());
      if (!unfinished || !isUnfinishedAttempt(unfinished)) {
        const agentPrefillMinutes = this.data.agentPrefillMinutes;
        const agentPrefillModule = this.data.agentPrefillModule;
        const hasAgentPrefill = agentPrefillMinutes !== null || agentPrefillModule !== null;
        this.setData(
          {
            status: "ready",
            submitting: false,
            ...presentation,
            ...(agentPrefillMinutes === null
              ? {}
              : {
                  minutes: agentPrefillMinutes,
                  selectedDurationLabel: formatCapacityDuration(agentPrefillMinutes),
                  agentPrefillMinutes: null,
                }),
            ...(agentPrefillModule === null
              ? {}
              : {
                  preferredXingceModule: agentPrefillModule,
                  xingceOnly: this.data.agentPrefillXingceOnly,
                  agentPrefillModule: null,
                  agentPrefillXingceOnly: false,
                }),
          },
          () => {
            if (hasAgentPrefill) void this.generatePreview();
          },
        );
        return;
      }

      if (!isCapacityCanonicalInput(unfinished.canonicalInput)) {
        const authoritative = settleCapacityAttempt(store, unfinished, "terminal", Date.now());
        if (
          authoritative?.state === "succeeded" &&
          ownsPageLifecycle(runtime, lifecycleGeneration, originSession)
        ) {
          this.setData({ status: "ready", submitting: false });
          void wx.switchTab({ url: routes.today });
          return;
        }
        if (!ownsLoadResponse(runtime, lifecycleGeneration, loadGeneration, originSession)) {
          return;
        }
        this.setData({
          status: "ready",
          submitting: false,
          ...presentation,
          errorMessage: INVALID_ATTEMPT_MESSAGE,
          requestId: "",
        });
        return;
      }

      if (unfinished.canonicalInput.localDate !== data.localDate) {
        const authoritative = settleCapacityAttempt(store, unfinished, "terminal", Date.now());
        if (
          authoritative?.state === "succeeded" &&
          ownsPageLifecycle(runtime, lifecycleGeneration, originSession)
        ) {
          this.setData({ status: "ready", submitting: false });
          void wx.switchTab({ url: routes.today });
          return;
        }
        if (!ownsLoadResponse(runtime, lifecycleGeneration, loadGeneration, originSession)) {
          return;
        }
        this.setData({
          status: "ready",
          submitting: false,
          ...presentation,
          errorMessage: OLD_DAY_ATTEMPT_MESSAGE,
          requestId: "",
        });
        return;
      }

      this.setData({ status: "loading", submitting: true, ...presentation });
      let successPublished = false;
      const publishAuthoritativeSuccess = () => {
        if (
          successPublished ||
          !ownsLoadResponse(runtime, lifecycleGeneration, loadGeneration, originSession)
        ) {
          return;
        }
        successPublished = true;
        this.setData({
          status: "ready",
          submitting: false,
          errorMessage: "",
          requestId: "",
        });
        void wx.switchTab({ url: routes.today });
      };
      let requestError: unknown;
      let desiredOutcome: CapacityAttemptOutcome = "succeeded";
      try {
        await sendCapacityAttempt(
          api,
          unfinished.canonicalInput.command,
          unfinished,
          originSession,
          publishAuthoritativeSuccess,
        );
      } catch (error) {
        requestError = error;
        desiredOutcome = capacityFailureOutcome(error);
      }
      const authoritative = settleCapacityAttempt(store, unfinished, desiredOutcome, Date.now());
      if (authoritative?.state === "succeeded") {
        publishAuthoritativeSuccess();
        return;
      }
      if (!ownsLoadResponse(runtime, lifecycleGeneration, loadGeneration, originSession)) {
        return;
      }
      if (authoritative?.state === "unknown") {
        this.setData({
          status: "error",
          submitting: false,
          ...presentation,
          errorMessage: UNKNOWN_OUTCOME_MESSAGE,
          requestId: requestError instanceof ApiClientError ? requestError.requestId : "",
        });
        return;
      }
      const view = errorView(
        requestError,
        authoritative?.state === "terminal"
          ? "今天的安排没有保存成功，请重新载入后再试。"
          : "这次调整已由另一页面处理，请重新载入确认。",
      );
      this.setData({
        status: authoritative?.state === "terminal" ? "ready" : "error",
        submitting: false,
        ...presentation,
        errorMessage: view.message,
        requestId: view.requestId,
      });
    } catch (error) {
      if (!ownsLoadResponse(runtime, lifecycleGeneration, loadGeneration, originSession)) {
        return;
      }
      const view = errorView(error, "今天的安排没有加载成功，请重试。");
      this.setData({
        status: "error",
        submitting: false,
        errorMessage: view.message,
        requestId: view.requestId,
      });
    }
  },

  exit() {
    invalidatePageLifecycle(this, true);
    navigateBackOr(routes.today);
  },

  adjustMinutes(event: WechatMiniprogram.BaseEvent) {
    if (this.data.previewing || this.data.submitting) return;
    const delta = Number(event.currentTarget.dataset.delta);
    const current = this.data.minutes;
    if (!Number.isInteger(delta) || !Number.isInteger(current)) return;
    const next = Math.max(0, Math.min(MAX_VISIBLE_MINUTES, current + delta));
    this.updateMinutes(next);
  },

  chooseMinutes(event: WechatMiniprogram.BaseEvent) {
    if (this.data.previewing || this.data.submitting) return;
    const minutes = Number(event.currentTarget.dataset.minutes);
    if (!Number.isInteger(minutes) || minutes < 0 || minutes > MAX_VISIBLE_MINUTES) return;
    this.updateMinutes(minutes);
  },

  updateMinutes(minutes: number) {
    attemptRuntime(this).editBoundary = true;
    this.setData({
      minutes,
      selectedDurationLabel: formatCapacityDuration(minutes),
      preview: null,
      errorMessage: "",
      requestId: "",
    });
  },

  chooseEnergy(event: WechatMiniprogram.BaseEvent) {
    if (this.data.previewing || this.data.submitting) return;
    const energy = Number(event.currentTarget.dataset.value);
    if (![2, 3, 4].includes(energy)) return;
    attemptRuntime(this).editBoundary = true;
    this.setData({
      energy,
      preview: null,
      errorMessage: "",
      requestId: "",
    });
  },

  chooseFocus(event: WechatMiniprogram.BaseEvent) {
    if (this.data.previewing || this.data.submitting) return;
    const preferredXingceModule = String(event.currentTarget.dataset.value ?? "") as
      XingceModule | "";
    if (!FOCUS_OPTIONS.some((item) => item.value === preferredXingceModule)) return;
    attemptRuntime(this).editBoundary = true;
    this.setData({
      preferredXingceModule,
      xingceOnly: preferredXingceModule ? this.data.xingceOnly : false,
      preview: null,
      errorMessage: "",
      requestId: "",
    });
  },

  toggleXingceOnly() {
    if (!this.data.preferredXingceModule || this.data.previewing || this.data.submitting) {
      return;
    }
    attemptRuntime(this).editBoundary = true;
    this.setData({
      xingceOnly: !this.data.xingceOnly,
      preview: null,
      errorMessage: "",
      requestId: "",
    });
  },

  commandFromDraft(): DailyCapacityCommand | null {
    const dashboard = this.data.dashboard;
    if (!dashboard) return null;
    const prepared = prepareCapacityCommand(
      {
        availableMinutes: String(this.data.minutes),
        energy: this.data.energy,
        stress: this.data.stress,
        confidenceLevel: this.data.confidenceLevel,
        preferredXingceModule: selectedFocusModule(this.data.preferredXingceModule),
        xingceOnly: this.data.xingceOnly,
      },
      dashboard.capacity.capacityVersionId,
    );
    if (!prepared.command) {
      this.setData({ errorMessage: prepared.error });
      return null;
    }
    return prepared.command;
  },

  async generatePreview() {
    const client = requireAuthenticatedClient();
    const command = this.commandFromDraft();
    if (!client || !command || this.data.previewing || this.data.submitting) return;
    this.setData({ previewing: true, errorMessage: "", requestId: "" });
    try {
      const { data } = await new PlanningApi(client).previewTodayCapacity(command);
      const proposed = data.proposedPlan;
      const previewChanges = presentCapacityChanges(data.currentPlan.tasks, proposed.tasks);
      const taskDuration = plannedDuration(proposed.tasks);
      const capacityDuration = formatCapacityDuration(proposed.capacity.availableMinutes);
      const capacityImpact =
        proposed.capacity.availableMinutes === 0
          ? "今天已设为休息，不安排学习任务。"
          : `今天可用 ${capacityDuration}。`;
      const goalEvidence = proposed.goalGap.evidenceSummary.trim();
      const focusImpact = proposed.capacity.preferredXingceModule
        ? proposed.capacity.xingceOnly
          ? `今天只安排${proposed.capacity.preferredXingceModule}。`
          : `今天优先安排${proposed.capacity.preferredXingceModule}。`
        : "今天按双轨综合节奏安排。";
      this.setData({
        previewing: false,
        step: "preview",
        preview: data,
        previewChanges,
        previewHeadline: proposed.headline,
        previewRationale: proposed.rationale,
        previewImpact: [
          `调整后安排 ${String(proposed.tasks.length)} 项任务，共 ${taskDuration}；${capacityImpact}`,
          focusImpact,
          goalEvidence ? `目标依据：${goalEvidence}` : "",
        ]
          .filter(Boolean)
          .join(" "),
      });
    } catch (error) {
      const view = errorView(error, "调整预览没有生成成功，请重新载入后再试。");
      this.setData({
        previewing: false,
        errorMessage: view.message,
        requestId: view.requestId,
      });
    }
  },

  backToEdit() {
    if (this.data.submitting) return;
    attemptRuntime(this).editBoundary = true;
    this.setData({
      step: "edit",
      errorMessage: "",
      requestId: "",
    });
  },

  async confirmAdjustment() {
    const session = currentAuthSession();
    const client = requireAuthenticatedClient();
    const command = this.commandFromDraft();
    const dashboard = this.data.dashboard;
    if (
      !session ||
      !client ||
      !command ||
      !dashboard ||
      !this.data.preview ||
      this.data.submitting ||
      this.data.previewing
    ) {
      return;
    }
    this.setData({ submitting: true, errorMessage: "", requestId: "" });
    const runtime = attemptRuntime(this);
    const lifecycleGeneration = runtime.lifecycleGeneration;
    let successPublished = false;
    const publishAuthoritativeSuccess = () => {
      if (successPublished || !ownsPageLifecycle(runtime, lifecycleGeneration, session)) {
        return;
      }
      successPublished = true;
      this.setData({ submitting: false, errorMessage: "", requestId: "" });
      void wx.switchTab({ url: routes.today });
    };
    const nowMs = Date.now();
    const canonicalInput: CapacityCanonicalInput = {
      localDate: dashboard.localDate,
      command,
    };
    const canonicalFingerprint = stableFingerprint(canonicalInput);
    const store = new OperationAttemptStore(session.userId);
    let attempt: OperationAttemptRecord | undefined;
    try {
      cancelChangedAttempt(store, runtime, canonicalFingerprint, nowMs);
      attempt = await store.begin({
        scope: CAPACITY_ATTEMPT_SCOPE,
        canonicalInput,
        idempotencyKeyPrefix: "capacity",
        expiresAtMs: nowMs + OPERATION_ATTEMPT_WINDOW_MS,
        nowMs,
      });
      runtime.editBoundary = false;
    } catch (error) {
      if (!ownsPageLifecycle(runtime, lifecycleGeneration, session)) return;
      const view = errorView(error, "今天的安排没有保存成功，请重新载入后再试。");
      this.setData({
        submitting: false,
        errorMessage: view.message,
        requestId: view.requestId,
      });
      return;
    }

    if (!sameSessionOwner(currentAuthSession(), session)) {
      cancelCapacityAttemptIfActive(store, attempt, Date.now());
      return;
    }

    try {
      await sendCapacityAttempt(
        new PlanningApi(client),
        command,
        attempt,
        session,
        publishAuthoritativeSuccess,
      );
    } catch (error) {
      const authoritative = settleCapacityAttempt(
        store,
        attempt,
        capacityFailureOutcome(error),
        Date.now(),
      );
      if (!ownsPageLifecycle(runtime, lifecycleGeneration, session)) return;
      if (authoritative?.state === "succeeded") {
        publishAuthoritativeSuccess();
        return;
      }
      if (authoritative?.state === "unknown") {
        this.setData({
          submitting: false,
          errorMessage: UNKNOWN_OUTCOME_MESSAGE,
          requestId: error instanceof ApiClientError ? error.requestId : "",
        });
        return;
      }
      const view = errorView(
        error,
        authoritative?.state === "terminal"
          ? "今天的安排没有保存成功，请重新载入后再试。"
          : "这次调整已由另一页面处理，请重新载入确认。",
      );
      this.setData({
        submitting: false,
        errorMessage: view.message,
        requestId: view.requestId,
      });
      return;
    }

    const authoritative = settleCapacityAttempt(store, attempt, "succeeded", Date.now());
    if (!ownsPageLifecycle(runtime, lifecycleGeneration, session)) return;
    if (authoritative?.state !== "succeeded") {
      this.setData({
        submitting: false,
        errorMessage: "这次调整已由另一页面处理，请重新载入确认。",
        requestId: "",
      });
      return;
    }
    publishAuthoritativeSuccess();
  },

  retry() {
    void this.loadToday();
  },
});

export function parseAgentPrefillMinutes(
  options: Record<string, string | undefined>,
): number | null {
  if (options.source !== "anan") return null;
  const raw = options.availableMinutes ?? "";
  if (!/^\d{1,4}$/.test(raw)) return null;
  const minutes = Number(raw);
  return Number.isSafeInteger(minutes) && minutes >= 0 && minutes <= 1440 ? minutes : null;
}

export function parseAgentPrefillFocus(
  options: Record<string, string | undefined>,
): { module: XingceModule; xingceOnly: boolean } | null {
  if (options.source !== "anan") return null;
  const module = options.preferredXingceModule ?? "";
  if (!FOCUS_OPTIONS.some((item) => item.value === module && item.value !== "")) return null;
  return {
    module: module as XingceModule,
    xingceOnly: options.xingceOnly === "1",
  };
}

function selectedFocusModule(value: string): XingceModule | null {
  const matched = FOCUS_OPTIONS.find((item) => item.value === value && item.value !== "");
  return matched?.value || null;
}

function normalizedEnergy(value: number): number {
  if (value <= 2) return 2;
  if (value >= 4) return 4;
  return 3;
}

function dashboardPresentation(dashboard: TodayDashboard) {
  const activeTasks = dashboard.tasks.filter(
    (task) => task.status === "ready" || task.status === "in_progress",
  );
  return {
    dashboard,
    minutes: dashboard.capacity.availableMinutes,
    selectedDurationLabel: formatCapacityDuration(dashboard.capacity.availableMinutes),
    currentDurationLabel: formatCapacityDuration(
      activeTasks.reduce((total, task) => total + task.estimatedMinutes, 0),
    ),
    currentTaskCount: activeTasks.length,
    currentFocus: activeTasks[0]?.title ?? dashboard.headline,
    energy: normalizedEnergy(dashboard.capacity.energy),
    stress: dashboard.capacity.stress,
    confidenceLevel: dashboard.capacity.confidenceLevel,
    preferredXingceModule: dashboard.capacity.preferredXingceModule ?? "",
    xingceOnly: dashboard.capacity.xingceOnly ?? false,
    errorMessage: "",
    requestId: "",
  };
}

function attemptRuntime(page: object): CapacityAttemptRuntime {
  const existing = attemptRuntimes.get(page);
  if (existing) return existing;
  const created: CapacityAttemptRuntime = {
    editBoundary: false,
    loadGeneration: 0,
    lifecycleGeneration: 0,
    visible: true,
    disposed: false,
  };
  attemptRuntimes.set(page, created);
  return created;
}

function invalidatePageLifecycle(page: object, disposed: boolean): void {
  const runtime = attemptRuntime(page);
  runtime.visible = false;
  runtime.disposed = runtime.disposed || disposed;
  runtime.lifecycleGeneration += 1;
  runtime.loadGeneration += 1;
}

function cancelChangedAttempt(
  store: OperationAttemptStore,
  runtime: CapacityAttemptRuntime,
  nextFingerprint: string,
  nowMs: number,
): void {
  if (!runtime.editBoundary) return;
  const current = store.load(CAPACITY_ATTEMPT_SCOPE, nowMs);
  if (current && isUnfinishedAttempt(current) && current.canonicalFingerprint !== nextFingerprint) {
    store.cancel(CAPACITY_ATTEMPT_SCOPE, current.idempotencyKey, nowMs);
  }
}

type CapacityAttemptOutcome = "succeeded" | "terminal" | "unknown";

function sendCapacityAttempt(
  api: PlanningApi,
  command: DailyCapacityCommand,
  attempt: OperationAttemptRecord,
  session: ActiveAuthSession,
  publishAuthoritativeSuccess: () => void,
): Promise<void> {
  const inFlightKey = stableFingerprint({
    accessToken: session.accessToken,
    generation: session.generation,
    idempotencyKey: attempt.idempotencyKey,
    userId: session.userId,
  });
  const groupKey = stableFingerprint({
    idempotencyKey: attempt.idempotencyKey,
    ownerUserId: attempt.ownerUserId,
  });
  const group = capacityWriteGroup(groupKey);
  group.successSubscribers.add(publishAuthoritativeSuccess);
  const existing = inFlightCapacityWrites.get(inFlightKey);
  if (existing) return existing;

  const request = api.setTodayCapacity(command, attempt.idempotencyKey).then(() => {
    const store = new OperationAttemptStore(attempt.ownerUserId);
    const authoritative = settleCapacityAttempt(store, attempt, "succeeded", Date.now());
    if (authoritative?.state === "succeeded") {
      for (const publish of group.successSubscribers) publish();
    }
  });
  inFlightCapacityWrites.set(inFlightKey, request);
  group.pending.add(request);
  void request.then(
    () => {
      clearCapacityWrite(request, inFlightKey, groupKey, group);
    },
    () => {
      clearCapacityWrite(request, inFlightKey, groupKey, group);
    },
  );
  return request;
}

function capacityWriteGroup(groupKey: string): CapacityWriteGroup {
  const existing = inFlightCapacityWriteGroups.get(groupKey);
  if (existing) return existing;
  const created: CapacityWriteGroup = {
    pending: new Set(),
    successSubscribers: new Set(),
  };
  inFlightCapacityWriteGroups.set(groupKey, created);
  return created;
}

function clearCapacityWrite(
  request: Promise<void>,
  inFlightKey: string,
  groupKey: string,
  group: CapacityWriteGroup,
): void {
  if (inFlightCapacityWrites.get(inFlightKey) === request) {
    inFlightCapacityWrites.delete(inFlightKey);
  }
  group.pending.delete(request);
  if (group.pending.size === 0 && inFlightCapacityWriteGroups.get(groupKey) === group) {
    inFlightCapacityWriteGroups.delete(groupKey);
  }
}

function settleCapacityAttempt(
  store: OperationAttemptStore,
  attempt: OperationAttemptRecord,
  outcome: CapacityAttemptOutcome,
  nowMs: number,
): OperationAttemptRecord | null {
  const current = store.load(CAPACITY_ATTEMPT_SCOPE, nowMs);
  if (!current || current.idempotencyKey !== attempt.idempotencyKey) return null;
  if (
    current.state === "succeeded" ||
    current.state === "terminal" ||
    current.state === "cancelled"
  ) {
    return current;
  }
  if (outcome === "succeeded") {
    return store.markSucceeded(CAPACITY_ATTEMPT_SCOPE, attempt.idempotencyKey, nowMs);
  }
  if (outcome === "terminal") {
    return store.markTerminal(CAPACITY_ATTEMPT_SCOPE, attempt.idempotencyKey, nowMs);
  }
  return store.markTransportUnknown(CAPACITY_ATTEMPT_SCOPE, attempt.idempotencyKey, nowMs);
}

function cancelCapacityAttemptIfActive(
  store: OperationAttemptStore,
  attempt: OperationAttemptRecord,
  nowMs: number,
): OperationAttemptRecord | null {
  const current = store.load(CAPACITY_ATTEMPT_SCOPE, nowMs);
  if (!current || current.idempotencyKey !== attempt.idempotencyKey) return current;
  if (!isUnfinishedAttempt(current)) return current;
  return store.cancel(CAPACITY_ATTEMPT_SCOPE, attempt.idempotencyKey, nowMs);
}

function capacityFailureOutcome(error: unknown): "terminal" | "unknown" {
  return isDeterministicCapacityFailure(error) ? "terminal" : "unknown";
}

function isDeterministicCapacityFailure(error: unknown): boolean {
  return (
    error instanceof ApiClientError &&
    error.classification === "http_response" &&
    error.statusCode >= 400 &&
    error.statusCode < 500 &&
    error.statusCode !== 408
  );
}

function isUnfinishedAttempt(
  attempt: OperationAttemptRecord,
): attempt is OperationAttemptRecord & { state: "pending" | "unknown" } {
  return attempt.state === "pending" || attempt.state === "unknown";
}

function ownsLoadResponse(
  runtime: CapacityAttemptRuntime,
  lifecycleGeneration: number,
  loadGeneration: number,
  originSession: ActiveAuthSession,
): boolean {
  return (
    ownsPageLifecycle(runtime, lifecycleGeneration, originSession) &&
    runtime.loadGeneration === loadGeneration
  );
}

function ownsPageLifecycle(
  runtime: CapacityAttemptRuntime,
  lifecycleGeneration: number,
  originSession: ActiveAuthSession,
): boolean {
  return (
    runtime.visible &&
    !runtime.disposed &&
    runtime.lifecycleGeneration === lifecycleGeneration &&
    sameSessionOwner(currentAuthSession(), originSession)
  );
}

function isCapacityCanonicalInput(value: unknown): value is CapacityCanonicalInput {
  if (!hasExactKeys(value, ["command", "localDate"])) return false;
  if (!isLocalDate(value.localDate)) return false;
  const command = value.command;
  if (
    !hasExactKeys(command, [
      "availableMinutes",
      "basedOnCapacityVersionId",
      "confidenceLevel",
      "energy",
      "preferredXingceModule",
      "stress",
      "xingceOnly",
    ])
  ) {
    return false;
  }
  return (
    typeof command.basedOnCapacityVersionId === "string" &&
    command.basedOnCapacityVersionId.length > 0 &&
    command.basedOnCapacityVersionId.length <= 256 &&
    command.basedOnCapacityVersionId.trim() === command.basedOnCapacityVersionId &&
    isBoundedInteger(command.availableMinutes, 0, 1440) &&
    isBoundedInteger(command.energy, 1, 5) &&
    isBoundedInteger(command.stress, 1, 5) &&
    isBoundedInteger(command.confidenceLevel, 1, 5) &&
    (command.preferredXingceModule === null ||
      (typeof command.preferredXingceModule === "string" &&
        FOCUS_OPTIONS.some((item) => item.value === command.preferredXingceModule))) &&
    typeof command.xingceOnly === "boolean" &&
    (!command.xingceOnly || command.preferredXingceModule !== null)
  );
}

function hasExactKeys(
  value: unknown,
  expectedKeys: readonly string[],
): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const actualKeys = Object.keys(value);
  return (
    actualKeys.length === expectedKeys.length &&
    expectedKeys.every((key) => Object.prototype.hasOwnProperty.call(value, key))
  );
}

function isLocalDate(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/u.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (year < 1 || month < 1 || month > 12 || day < 1) return false;
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  const maximumDay = daysInMonth[month - 1];
  return maximumDay !== undefined && day <= maximumDay;
}

function isBoundedInteger(value: unknown, minimum: number, maximum: number): value is number {
  return Number.isInteger(value) && Number(value) >= minimum && Number(value) <= maximum;
}
