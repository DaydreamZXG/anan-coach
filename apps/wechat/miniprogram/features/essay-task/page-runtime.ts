import type { EssayIntelligenceApi, EssayReviewIntentView } from "../../api/essay-intelligence";
import type { EssayTaskStage } from "./capabilities";
import type { PendingEssayDraftingSubmission } from "./drafting";
import type { PendingPlanTaskCompletion } from "./pending-store";

export interface EssayTaskPageRuntime {
  api: EssayIntelligenceApi | null;
  activeStage: EssayTaskStage | null;
  activeTaskVersionId: string;
  activePlanTaskId: string;
  activeUserId: string;
  basedOnExecutionVersionId: string | undefined;
  pendingCompletion: PendingPlanTaskCompletion | null;
  pendingDraftingSubmission: PendingEssayDraftingSubmission | null;
  activeReviewIntent: EssayReviewIntentView | null;
  activeReviewJobId: string;
  startedAt: number;
  activeElapsedMs: number;
  activeSince: number;
  timerHandle: ReturnType<typeof setInterval> | undefined;
  reviewPollHandle: ReturnType<typeof setTimeout> | undefined;
  reviewPollInFlight: boolean;
  pageVisible: boolean;
  requestEpoch: number;
  disposed: boolean;
}

export function createEssayTaskPageRuntime(): EssayTaskPageRuntime {
  return {
    api: null,
    activeStage: null,
    activeTaskVersionId: "",
    activePlanTaskId: "",
    activeUserId: "",
    basedOnExecutionVersionId: undefined,
    pendingCompletion: null,
    pendingDraftingSubmission: null,
    activeReviewIntent: null,
    activeReviewJobId: "",
    startedAt: 0,
    activeElapsedMs: 0,
    activeSince: 0,
    timerHandle: undefined,
    reviewPollHandle: undefined,
    reviewPollInFlight: false,
    pageVisible: false,
    requestEpoch: 0,
    disposed: false,
  };
}

/** Owns one mutable runtime per concrete WeChat page instance. */
export class EssayTaskPageRuntimeRegistry<TPage extends object> {
  private readonly runtimes = new WeakMap<TPage, EssayTaskPageRuntime>();

  attach(page: TPage): EssayTaskPageRuntime {
    const previous = this.runtimes.get(page);
    if (previous) this.dispose(previous);
    const runtime = createEssayTaskPageRuntime();
    this.runtimes.set(page, runtime);
    return runtime;
  }

  get(page: TPage): EssayTaskPageRuntime | undefined {
    return this.runtimes.get(page);
  }

  detach(page: TPage): void {
    const runtime = this.runtimes.get(page);
    if (!runtime) return;
    this.dispose(runtime);
    this.runtimes.delete(page);
  }

  beginRequest(runtime: EssayTaskPageRuntime): number {
    runtime.requestEpoch += 1;
    return runtime.requestEpoch;
  }

  isCurrent(page: TPage, runtime: EssayTaskPageRuntime, requestEpoch: number): boolean {
    return (
      this.runtimes.get(page) === runtime &&
      !runtime.disposed &&
      runtime.requestEpoch === requestEpoch
    );
  }

  owns(page: TPage, runtime: EssayTaskPageRuntime): boolean {
    return this.runtimes.get(page) === runtime && !runtime.disposed;
  }

  private dispose(runtime: EssayTaskPageRuntime): void {
    runtime.disposed = true;
    runtime.requestEpoch += 1;
    if (runtime.timerHandle !== undefined) clearInterval(runtime.timerHandle);
    if (runtime.reviewPollHandle !== undefined) clearTimeout(runtime.reviewPollHandle);
    runtime.timerHandle = undefined;
    runtime.reviewPollHandle = undefined;
    runtime.reviewPollInFlight = false;
    runtime.pageVisible = false;
    runtime.api = null;
    runtime.activeUserId = "";
    runtime.pendingCompletion = null;
    runtime.pendingDraftingSubmission = null;
    runtime.activeReviewIntent = null;
  }
}
