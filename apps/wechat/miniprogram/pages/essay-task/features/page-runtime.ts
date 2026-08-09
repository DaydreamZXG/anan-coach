import type {
  EssayIntelligenceApi,
  EssayReviewIntentView,
  EssayReviewResponse,
} from "../shared/api/essay-intelligence";
import type { LearningMediaApi } from "../shared/api/learning-media";
import type { EssayHandwritingApi } from "../shared/api/essay-handwriting";
import type { EssayWriterContext, EssayTaskStage } from "../../../features/essay-task/capabilities";
import type { EssayClosureDraft } from "./closure-store";
import type { PendingEssayDraftingSubmission } from "../../../features/essay-task/drafting";
import type { PendingPlanTaskCompletion } from "../../../features/essay-task/pending-store";
import type {
  WechatVoicePreview,
  WechatVoiceRecorder,
} from "../shared/features/learning-media/platform";
import type { ReflectionVoiceUploadCoordinator } from "../shared/features/learning-media/upload-controller";
import { currentAuthSession, type ActiveAuthSession } from "../../../auth/session";
import {
  EssayTaskEffectOwner,
  type EssayTaskEffectLane,
  type EssayTaskEffectLease,
} from "./page-host/effect-owner";

export interface EssayTaskPageRuntime {
  effectOwner: EssayTaskEffectOwner | null;
  api: EssayIntelligenceApi | null;
  learningMediaApi: LearningMediaApi | null;
  essayHandwritingApi: EssayHandwritingApi | null;
  voiceUploadCoordinator: ReflectionVoiceUploadCoordinator | null;
  voiceRecorder: WechatVoiceRecorder | null;
  voicePreview: WechatVoicePreview | null;
  activeStage: EssayTaskStage | null;
  writerContext: EssayWriterContext | null;
  activeTaskVersionId: string;
  activePlanTaskId: string;
  activeUserId: string;
  activeAccessToken: string;
  activeSessionGeneration: string;
  returnUrl: string;
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
  draftingAutosaveHandle: ReturnType<typeof setTimeout> | undefined;
  reviewPollHandle: ReturnType<typeof setTimeout> | undefined;
  reviewPollInFlight: boolean;
  pageVisible: boolean;
  requestEpoch: number;
  disposed: boolean;
}

export function createEssayTaskPageRuntime(): EssayTaskPageRuntime {
  return {
    effectOwner: null,
    api: null,
    learningMediaApi: null,
    essayHandwritingApi: null,
    voiceUploadCoordinator: null,
    voiceRecorder: null,
    voicePreview: null,
    activeStage: null,
    writerContext: null,
    activeTaskVersionId: "",
    activePlanTaskId: "",
    activeUserId: "",
    activeAccessToken: "",
    activeSessionGeneration: "",
    returnUrl: "",
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
    draftingAutosaveHandle: undefined,
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

  bindEffects(
    page: TPage,
    runtime: EssayTaskPageRuntime,
    session: ActiveAuthSession,
  ): boolean {
    if (!this.owns(page, runtime)) return false;
    runtime.effectOwner?.unload();
    runtime.effectOwner = new EssayTaskEffectOwner(page, session);
    return runtime.effectOwner.show(page, session);
  }

  show(
    page: TPage,
    runtime: EssayTaskPageRuntime,
    session: ActiveAuthSession,
  ): boolean {
    return this.owns(page, runtime) && Boolean(runtime.effectOwner?.show(page, session));
  }

  hide(runtime: EssayTaskPageRuntime): Set<EssayTaskEffectLane> {
    return runtime.effectOwner?.hide() ?? new Set();
  }

  invalidateAll(runtime: EssayTaskPageRuntime): Set<EssayTaskEffectLane> {
    return runtime.effectOwner?.invalidateAll() ?? new Set();
  }

  beginEffect(
    page: TPage,
    runtime: EssayTaskPageRuntime,
    lane: EssayTaskEffectLane,
  ): EssayTaskEffectLease | null {
    if (!this.owns(page, runtime)) return null;
    return runtime.effectOwner?.begin(page, lane, currentAuthSession()) ?? null;
  }

  isEffectCurrent(
    page: TPage,
    runtime: EssayTaskPageRuntime,
    effect: EssayTaskEffectLease | null,
  ): boolean {
    return (
      this.owns(page, runtime) &&
      Boolean(runtime.effectOwner?.owns(page, effect, currentAuthSession()))
    );
  }

  hasActiveEffect(runtime: EssayTaskPageRuntime, lane: EssayTaskEffectLane): boolean {
    return Boolean(runtime.effectOwner?.hasActive(lane));
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
    runtime.effectOwner?.unload();
    runtime.effectOwner = null;
    runtime.requestEpoch += 1;
    if (runtime.timerHandle !== undefined) clearInterval(runtime.timerHandle);
    if (runtime.draftingAutosaveHandle !== undefined) clearTimeout(runtime.draftingAutosaveHandle);
    if (runtime.reviewPollHandle !== undefined) clearTimeout(runtime.reviewPollHandle);
    runtime.timerHandle = undefined;
    runtime.draftingAutosaveHandle = undefined;
    runtime.reviewPollHandle = undefined;
    runtime.reviewPollInFlight = false;
    runtime.pageVisible = false;
    runtime.api = null;
    runtime.learningMediaApi = null;
    runtime.essayHandwritingApi = null;
    runtime.voiceRecorder?.dispose();
    runtime.voicePreview?.dispose();
    runtime.voiceUploadCoordinator = null;
    runtime.voiceRecorder = null;
    runtime.voicePreview = null;
    runtime.activeUserId = "";
    runtime.activeAccessToken = "";
    runtime.activeSessionGeneration = "";
    runtime.returnUrl = "";
    runtime.pendingCompletion = null;
    runtime.pendingDraftingSubmission = null;
    runtime.closureDraft = null;
    runtime.rewriteReview = null;
    runtime.transferSourceReview = null;
    runtime.transferReview = null;
    runtime.activeReviewIntent = null;
  }
}
