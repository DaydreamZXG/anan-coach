import type {
  XingceLearningApi,
  XingceTaskMode,
  XingceTaskSession,
} from "../../../shared/api/xingce-learning";
import type { TaskRuntimeState } from "../../../../../task-runtime/contracts";
import type { SwipeNavSample } from "../../../shared/task-runtime/swipe-nav";
import type { TaskEditingApi } from "../../../shared/api/task-editing";
import type { QuestionFavoritesApi } from "../../../shared/api/question-favorites";
import type { TaskEditOwnership } from "../../../shared/features/task-editing/coordinator";

export interface XingcePageRuntime {
  api: XingceLearningApi | null;
  favoriteApi: QuestionFavoritesApi | null;
  editingApi: TaskEditingApi | null;
  editOwnership: TaskEditOwnership | null;
  ownerUserId: string;
  ownerSessionGeneration: string;
  planTaskId: string;
  requestedMode: XingceTaskMode | null;
  session: XingceTaskSession | null;
  task: TaskRuntimeState | null;
  timerHandle: ReturnType<typeof setInterval> | undefined;
  /** 契约增量 v1：上一次 TICK 的 Date.now()，用于累计真实活跃毫秒。 */
  lastTickAtMs: number;
  saveInFlight: boolean;
  /** 输入/笔画先防抖落本机，网络仍沿用 15 秒自动保存节奏。 */
  localCheckpointTimer: ReturnType<typeof setTimeout> | undefined;
  localCheckpointDirty: boolean;
  localCheckpointWarningShown: boolean;
  submitInFlight: boolean;
  /** 快滑切题手势的起点采样；多指或松手后置回 null。 */
  swipeStart: SwipeNavSample | null;
  /** scroll-view 以 touchcancel 收尾时使用的最后一个有效移动点。 */
  swipeLatest: SwipeNavSample | null;
  lastAutosaveElapsedSeconds: number;
  visible: boolean;
  disposed: boolean;
  requestEpoch: number;
  favoriteRequestEpoch: number;
  favoriteMutationInFlight: boolean;
  reflectionNavigationStarted: boolean;
  /** 契约增量 v2：批注锚定的题干句段矩形表对应的题号；切题后重建。 */
  annotationSegmentsQuestionId: string;
  /** 契约增量 v2：批注锚定矩形表最近一次查询的 Date.now()，用于滚动节流。 */
  lastAnnotationSegmentsAtMs: number;
  /** 画词工具条跟随句子的重定位最近一次 Date.now()，用于滚动节流。 */
  lastSelectionPositionAtMs: number;
}

function createRuntime(): XingcePageRuntime {
  return {
    api: null,
    favoriteApi: null,
    editingApi: null,
    editOwnership: null,
    ownerUserId: "",
    ownerSessionGeneration: "",
    planTaskId: "",
    requestedMode: null,
    session: null,
    task: null,
    timerHandle: undefined,
    lastTickAtMs: 0,
    saveInFlight: false,
    localCheckpointTimer: undefined,
    localCheckpointDirty: false,
    localCheckpointWarningShown: false,
    submitInFlight: false,
    swipeStart: null,
    swipeLatest: null,
    lastAutosaveElapsedSeconds: 0,
    visible: false,
    disposed: false,
    requestEpoch: 0,
    favoriteRequestEpoch: 0,
    favoriteMutationInFlight: false,
    reflectionNavigationStarted: false,
    annotationSegmentsQuestionId: "",
    lastAnnotationSegmentsAtMs: 0,
    lastSelectionPositionAtMs: 0,
  };
}

export class XingcePageRuntimeRegistry<TPage extends object> {
  private readonly values = new WeakMap<TPage, XingcePageRuntime>();

  attach(page: TPage): XingcePageRuntime {
    this.detach(page);
    const runtime = createRuntime();
    this.values.set(page, runtime);
    return runtime;
  }

  get(page: TPage): XingcePageRuntime | undefined {
    return this.values.get(page);
  }

  owns(page: TPage, runtime: XingcePageRuntime): boolean {
    return this.values.get(page) === runtime && !runtime.disposed;
  }

  beginRequest(runtime: XingcePageRuntime): number {
    runtime.requestEpoch += 1;
    runtime.favoriteRequestEpoch += 1;
    return runtime.requestEpoch;
  }

  isCurrent(page: TPage, runtime: XingcePageRuntime, epoch: number): boolean {
    return this.owns(page, runtime) && runtime.requestEpoch === epoch;
  }

  detach(page: TPage): void {
    const runtime = this.values.get(page);
    if (!runtime) return;
    runtime.disposed = true;
    runtime.visible = false;
    runtime.requestEpoch += 1;
    if (runtime.timerHandle !== undefined) clearInterval(runtime.timerHandle);
    runtime.timerHandle = undefined;
    if (runtime.localCheckpointTimer !== undefined) clearTimeout(runtime.localCheckpointTimer);
    runtime.localCheckpointTimer = undefined;
    runtime.api = null;
    runtime.favoriteApi = null;
    runtime.editingApi = null;
    runtime.editOwnership = null;
    runtime.session = null;
    runtime.task = null;
    this.values.delete(page);
  }
}
