import type {
  EssayIntelligenceApi,
  EssayReviewIntentView,
  EssayReviewResponse,
} from "../../api/essay-intelligence";
import type { LearningMediaApi } from "../../api/learning-media";
import type { EssayWriterContext, EssayTaskStage } from "./capabilities";
import type { EssayClosureDraft } from "./closure-store";
import type { PendingEssayDraftingSubmission } from "./drafting";
import type { PendingPlanTaskCompletion } from "./pending-store";
import type { WechatVoicePreview, WechatVoiceRecorder } from "../learning-media/platform";
import type { ReflectionVoiceUploadCoordinator } from "../learning-media/upload-controller";

export interface EssayTaskPageRuntime {
  api: EssayIntelligenceApi | null;
  learningMediaApi: LearningMediaApi | null;
  voiceUploadCoordinator: ReflectionVoiceUploadCoordinator | null;
  voiceRecorder: WechatVoiceRecorder | null;
  voicePreview: WechatVoicePreview | null;
  activeStage: EssayTaskStage | null;
  writerContext: EssayWriterContext | null;
  activeTaskVersionId: string;
  activePlanTaskId: string;
  activeUserId: string;
  basedOnExecutionVersionId: string | undefined;
  pendingCompletion: PendingPlanTaskCompletion | null;
  pendingDraftingSubmission: PendingEssayDraftingSubmission | null;
  closureDraft: EssayClosureDraft | null;
  rewriteReview: EssayReviewResponse | null;
  transferSourceReview: EssayReviewResponse | null;
  transferReview: EssayReviewResponse | null;
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
    learningMediaApi: null,
    voiceUploadCoordinator: null,
    voiceRecorder: null,
    voicePreview: null,
    activeStage: null,
    writerContext: null,
    activeTaskVersionId: "",
    activePlanTaskId: "",
    activeUserId: "",
    basedOnExecutionVersionId: undefined,
    pendingCompletion: null,
    pendingDraftingSubmission: null,
    closureDraft: null,
    rewriteReview: null,
    transferSourceReview: null,
    transferReview: null,
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
    runtime.learningMediaApi = null;
    runtime.voiceRecorder?.dispose();
    runtime.voicePreview?.dispose();
    runtime.voiceUploadCoordinator = null;
    runtime.voiceRecorder = null;
    runtime.voicePreview = null;
    runtime.activeUserId = "";
    runtime.pendingCompletion = null;
    runtime.pendingDraftingSubmission = null;
    runtime.closureDraft = null;
    runtime.rewriteReview = null;
    runtime.transferSourceReview = null;
    runtime.transferReview = null;
    runtime.activeReviewIntent = null;
  }
}
