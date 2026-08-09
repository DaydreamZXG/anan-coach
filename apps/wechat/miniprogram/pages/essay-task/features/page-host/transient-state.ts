import {
  emptyHandwritingState,
  type EssayHandwritingState,
} from "../essay-handwriting/workflow";

type SubmissionStatus = "idle" | "submitting" | "saved" | "error";

export interface EssayTaskTransientState {
  submissionStatus: SubmissionStatus;
  submissionMessage: string;
  voiceRecording: boolean;
  voicePlayingSegmentId: string;
  handwriting: EssayHandwritingState;
}

export interface CancelledVisibleEffects {
  reviewPoll: boolean;
  voice: boolean;
  handwriting: boolean;
  submissionContinues: boolean;
}

export function cancelledVisibleEffectPatch(
  state: EssayTaskTransientState,
  cancelled: CancelledVisibleEffects,
): Partial<EssayTaskTransientState> {
  const patch: Partial<EssayTaskTransientState> = {
    voiceRecording: false,
    voicePlayingSegmentId: "",
  };
  if (
    cancelled.handwriting &&
    (state.handwriting.phase === "uploading" || state.handwriting.phase === "confirming")
  ) {
    patch.handwriting = emptyHandwritingState();
  }
  if (
    state.submissionStatus === "submitting" &&
    !cancelled.submissionContinues &&
    (cancelled.reviewPoll || cancelled.voice || cancelled.handwriting)
  ) {
    patch.submissionStatus = cancelled.reviewPoll ? "saved" : "idle";
    if (!cancelled.reviewPoll) patch.submissionMessage = "";
  }
  return patch;
}
