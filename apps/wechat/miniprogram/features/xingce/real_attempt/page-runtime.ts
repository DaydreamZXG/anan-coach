import type {
  XingceLearningApi,
  XingceTaskMode,
  XingceTaskSession,
} from "../../../api/xingce-learning";
import type { TaskRuntimeState } from "../../../task-runtime/contracts";
import type { TaskEditingApi } from "../../../api/task-editing";
import type { TaskEditOwnership } from "../../task-editing/coordinator";

export interface XingcePageRuntime {
  api: XingceLearningApi | null;
  editingApi: TaskEditingApi | null;
  editOwnership: TaskEditOwnership | null;
  ownerUserId: string;
  planTaskId: string;
  requestedMode: XingceTaskMode | null;
  session: XingceTaskSession | null;
  task: TaskRuntimeState | null;
  timerHandle: ReturnType<typeof setInterval> | undefined;
  /** 契约增量 v1：上一次 TICK 的 Date.now()，用于累计真实活跃毫秒。 */
  lastTickAtMs: number;
  saveInFlight: boolean;
  submitInFlight: boolean;
  lastAutosaveElapsedSeconds: number;
  visible: boolean;
  disposed: boolean;
  requestEpoch: number;
  reflectionNavigationStarted: boolean;
}

function createRuntime(): XingcePageRuntime {
  return {
    api: null,
    editingApi: null,
    editOwnership: null,
    ownerUserId: "",
    planTaskId: "",
    requestedMode: null,
    session: null,
    task: null,
    timerHandle: undefined,
    lastTickAtMs: 0,
    saveInFlight: false,
    submitInFlight: false,
    lastAutosaveElapsedSeconds: 0,
    visible: false,
    disposed: false,
    requestEpoch: 0,
    reflectionNavigationStarted: false,
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
    runtime.api = null;
    runtime.editingApi = null;
    runtime.editOwnership = null;
    runtime.session = null;
    runtime.task = null;
    this.values.delete(page);
  }
}
