export type TaskMode = "practice" | "verification" | "mock_exam" | "essay";
export type TaskPhase =
  | "briefing"
  | "answering"
  | "feedback"
  | "reflection"
  | "candidate_review"
  | "variant_verification"
  | "summary"
  | "submitted";
export type SyncState = "saved" | "saving" | "pending" | "conflict" | "error";
export type ExitPolicy = "immediate" | "confirm" | "timer_continues" | "blocked";
export type AnswerConfidence = "low" | "medium" | "high";
export type TaskServerState =
  "answering" | "submitted" | "reflection_required" | "cause_decision_required" | "completed";

export interface MaterialPosition {
  anchorId: string;
  scrollTop: number;
  zoom: number;
}

export interface QuestionAttemptDraft {
  questionId: string;
  selectedOptionIds: string[];
  eliminatedOptionIds: string[];
  confidence?: AnswerConfidence;
  marked: boolean;
  skipped: boolean;
  elapsedSeconds: number;
  draftText: string;
  materialPosition?: MaterialPosition;
}

export interface TaskRuntimeState {
  taskId: string;
  serverSessionId?: string;
  planTaskId?: string;
  contentVersionId?: string;
  mode: TaskMode;
  phase: TaskPhase;
  serverState: TaskServerState;
  revision: number;
  syncState: SyncState;
  requestId?: string;
  questionOrder: string[];
  currentQuestionIndex: number;
  attempts: Record<string, QuestionAttemptDraft>;
  totalElapsedSeconds: number;
  exitPolicy: ExitPolicy;
  reflectionRequired: boolean;
  reflectionCompleted: boolean;
  causeDecisionRequired: boolean;
  causeDecisionCompleted: boolean;
  reviewId?: string;
  startedAt?: string;
  resultReceivedAt?: string;
  submittedAt?: string;
}

export type TaskRuntimeEvent =
  | { type: "TASK_STARTED"; startedAt: string }
  | { type: "TICK"; seconds: number }
  | { type: "QUESTION_CHANGED"; index: number }
  | { type: "ANSWER_SELECTED"; optionIds: string[] }
  | { type: "OPTION_ELIMINATED"; optionId: string; eliminated: boolean }
  | { type: "CONFIDENCE_SET"; confidence: AnswerConfidence }
  | { type: "QUESTION_MARKED"; marked: boolean }
  | { type: "QUESTION_SKIPPED" }
  | { type: "DRAFT_CHANGED"; text: string }
  | { type: "MATERIAL_POSITION_CHANGED"; position: MaterialPosition }
  | { type: "SAVE_REQUESTED" }
  | { type: "SAVE_SUCCEEDED"; revision: number }
  | { type: "SAVE_FAILED"; requestId: string }
  | { type: "REVISION_CONFLICT"; requestId: string }
  | {
      type: "TASK_RESULT_RECEIVED";
      receivedAt: string;
      reviewId: string;
      causeDecisionRequired: boolean;
      revision: number;
    }
  | {
      type: "REFLECTION_COMMITTED";
      causeDecisionRequired: boolean;
      revision: number;
    }
  | { type: "CAUSE_DECISION_COMMITTED"; revision: number }
  | { type: "PHASE_CHANGED"; phase: TaskPhase }
  | { type: "TASK_SUBMITTED"; submittedAt: string };

export interface CreateTaskRuntimeInput {
  taskId: string;
  mode: TaskMode;
  questionOrder: string[];
  serverSessionId?: string | undefined;
  planTaskId?: string | undefined;
  contentVersionId?: string | undefined;
  serverState?: TaskServerState | undefined;
  phase?: TaskPhase | undefined;
  attempts?: Record<string, QuestionAttemptDraft> | undefined;
  currentQuestionIndex?: number | undefined;
  totalElapsedSeconds?: number | undefined;
  reflectionRequired?: boolean | undefined;
  reflectionCompleted?: boolean | undefined;
  causeDecisionRequired?: boolean | undefined;
  causeDecisionCompleted?: boolean | undefined;
  reviewId?: string | undefined;
  revision?: number | undefined;
}
