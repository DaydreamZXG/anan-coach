import type {
  CreateTaskRuntimeInput,
  QuestionAttemptDraft,
  TaskRuntimeEvent,
  TaskRuntimeState,
} from "../contracts";

export class TaskTransitionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TaskTransitionError";
  }
}

export function createTaskRuntime(input: CreateTaskRuntimeInput): TaskRuntimeState {
  if (!input.taskId.trim()) throw new TaskTransitionError("taskId is required");
  if (input.questionOrder.length === 0) {
    throw new TaskTransitionError("A task must contain at least one question");
  }
  if (input.questionOrder.some((questionId) => !questionId.trim())) {
    throw new TaskTransitionError("Question IDs must not be empty");
  }
  if (new Set(input.questionOrder).size !== input.questionOrder.length) {
    throw new TaskTransitionError("questionOrder must not contain duplicate question IDs");
  }
  assertRevision(input.revision ?? 0);
  assertElapsedSeconds(input.totalElapsedSeconds ?? 0, "Total elapsed seconds");
  assertOptionalIdentifier(input.serverSessionId, "serverSessionId");
  assertOptionalIdentifier(input.planTaskId, "planTaskId");
  assertOptionalIdentifier(input.contentVersionId, "contentVersionId");
  const currentQuestionIndex = input.currentQuestionIndex ?? 0;
  if (currentQuestionIndex < 0 || currentQuestionIndex >= input.questionOrder.length) {
    throw new TaskTransitionError("Current question index is outside the task");
  }
  const attempts = Object.fromEntries(
    input.questionOrder.map((questionId) => {
      const attempt = cloneAttempt(input.attempts?.[questionId] ?? emptyAttempt(questionId));
      if (attempt.questionId !== questionId) {
        throw new TaskTransitionError("Attempt questionId must match its task question");
      }
      return [questionId, attempt];
    }),
  );
  if (
    input.attempts &&
    Object.keys(input.attempts).some((questionId) => !input.questionOrder.includes(questionId))
  ) {
    throw new TaskTransitionError("Attempts must belong to the task question order");
  }

  return {
    taskId: input.taskId,
    ...(input.serverSessionId ? { serverSessionId: input.serverSessionId } : {}),
    ...(input.planTaskId ? { planTaskId: input.planTaskId } : {}),
    ...(input.contentVersionId ? { contentVersionId: input.contentVersionId } : {}),
    mode: input.mode,
    phase: input.phase ?? "briefing",
    serverState: input.serverState ?? "answering",
    revision: input.revision ?? 0,
    syncState: "saved",
    questionOrder: [...input.questionOrder],
    currentQuestionIndex,
    attempts,
    totalElapsedSeconds: input.totalElapsedSeconds ?? 0,
    exitPolicy:
      input.reflectionRequired && !input.reflectionCompleted
        ? "blocked"
        : input.mode === "mock_exam"
          ? "timer_continues"
          : "confirm",
    reflectionRequired: input.reflectionRequired ?? false,
    reflectionCompleted: input.reflectionCompleted ?? false,
    causeDecisionRequired: input.causeDecisionRequired ?? false,
    causeDecisionCompleted: input.causeDecisionCompleted ?? false,
    ...(input.reviewId ? { reviewId: input.reviewId } : {}),
  };
}

export function reduceTaskRuntime(
  state: TaskRuntimeState,
  event: TaskRuntimeEvent,
): TaskRuntimeState {
  switch (event.type) {
    case "TASK_STARTED":
      assertPhase(state, "briefing");
      if (!event.startedAt.trim()) throw new TaskTransitionError("startedAt is required");
      return changed(state, {
        phase: "answering",
        startedAt: event.startedAt,
      });
    case "TICK":
      if (!Number.isInteger(event.seconds) || event.seconds <= 0) {
        throw new TaskTransitionError("Tick seconds must be a positive integer");
      }
      if (state.phase !== "answering") return state;
      return updateAttempt(
        state,
        (attempt) => ({
          ...attempt,
          elapsedSeconds: attempt.elapsedSeconds + event.seconds,
        }),
        {
          totalElapsedSeconds: state.totalElapsedSeconds + event.seconds,
        },
      );
    case "QUESTION_CHANGED":
      assertAnswering(state);
      if (
        !Number.isInteger(event.index) ||
        event.index < 0 ||
        event.index >= state.questionOrder.length
      ) {
        throw new TaskTransitionError("Question index is outside the task");
      }
      return changed(state, { currentQuestionIndex: event.index });
    case "ANSWER_SELECTED":
      assertAnswering(state);
      if (
        new Set(event.optionIds).size !== event.optionIds.length ||
        event.optionIds.some((optionId) => !optionId.trim())
      ) {
        throw new TaskTransitionError("Selected options must be unique, non-empty IDs");
      }
      return updateAttempt(state, (attempt) => ({
        ...attempt,
        selectedOptionIds: [...event.optionIds],
        eliminatedOptionIds: attempt.eliminatedOptionIds.filter(
          (optionId) => !event.optionIds.includes(optionId),
        ),
        skipped: false,
      }));
    case "OPTION_ELIMINATED":
      assertAnswering(state);
      if (!event.optionId.trim()) throw new TaskTransitionError("Eliminated option ID is required");
      return updateAttempt(state, (attempt) => ({
        ...attempt,
        selectedOptionIds: event.eliminated
          ? attempt.selectedOptionIds.filter((optionId) => optionId !== event.optionId)
          : attempt.selectedOptionIds,
        eliminatedOptionIds: event.eliminated
          ? unique([...attempt.eliminatedOptionIds, event.optionId])
          : attempt.eliminatedOptionIds.filter((optionId) => optionId !== event.optionId),
      }));
    case "CONFIDENCE_SET":
      assertAnswering(state);
      return updateAttempt(state, (attempt) => ({ ...attempt, confidence: event.confidence }));
    case "QUESTION_MARKED":
      assertAnswering(state);
      return updateAttempt(state, (attempt) => ({ ...attempt, marked: event.marked }));
    case "QUESTION_SKIPPED":
      assertAnswering(state);
      return updateAttempt(state, (attempt) => ({
        ...attempt,
        selectedOptionIds: [],
        skipped: true,
      }));
    case "DRAFT_CHANGED":
      assertAnswering(state);
      return updateAttempt(state, (attempt) => ({ ...attempt, draftText: event.text }));
    case "MATERIAL_POSITION_CHANGED":
      assertAnswering(state);
      if (
        !event.position.anchorId.trim() ||
        !Number.isFinite(event.position.zoom) ||
        !Number.isFinite(event.position.scrollTop) ||
        event.position.zoom <= 0 ||
        event.position.scrollTop < 0
      ) {
        throw new TaskTransitionError("Material position is invalid");
      }
      return updateAttempt(state, (attempt) => ({
        ...attempt,
        materialPosition: { ...event.position },
      }));
    case "SAVE_REQUESTED":
      if (state.syncState === "saving") return state;
      return { ...clearRequestId(state), syncState: "saving" };
    case "SAVE_SUCCEEDED":
      assertServerRevision(state, event.revision);
      return serverCommitted(state, { revision: event.revision });
    case "SAVE_FAILED":
      if (!event.requestId.trim()) throw new TaskTransitionError("requestId is required");
      return { ...state, syncState: "error", requestId: event.requestId };
    case "REVISION_CONFLICT":
      if (!event.requestId.trim()) throw new TaskTransitionError("requestId is required");
      return { ...state, syncState: "conflict", requestId: event.requestId };
    case "TASK_RESULT_RECEIVED":
      assertPhase(state, "answering");
      assertServerRevision(state, event.revision);
      if (!event.reviewId.trim() || !event.receivedAt.trim()) {
        throw new TaskTransitionError("Result receipt identity and time are required");
      }
      if (unansweredQuestionIds(state).length > 0) {
        throw new TaskTransitionError("Every question must be answered or explicitly skipped");
      }
      return serverCommitted(state, {
        phase: "feedback",
        serverState: "reflection_required",
        revision: event.revision,
        reflectionRequired: true,
        reflectionCompleted: false,
        causeDecisionRequired: event.causeDecisionRequired,
        causeDecisionCompleted: false,
        reviewId: event.reviewId,
        resultReceivedAt: event.receivedAt,
        exitPolicy: "blocked",
      });
    case "REFLECTION_COMMITTED":
      assertPhase(state, "reflection");
      assertServerRevision(state, event.revision);
      return serverCommitted(state, {
        phase: event.causeDecisionRequired ? "candidate_review" : "summary",
        serverState: event.causeDecisionRequired ? "cause_decision_required" : "completed",
        revision: event.revision,
        reflectionCompleted: true,
        causeDecisionRequired: event.causeDecisionRequired,
        causeDecisionCompleted: !event.causeDecisionRequired,
        exitPolicy: event.causeDecisionRequired ? "blocked" : "confirm",
      });
    case "CAUSE_DECISION_COMMITTED":
      assertPhase(state, "candidate_review");
      assertServerRevision(state, event.revision);
      return serverCommitted(state, {
        phase: "summary",
        serverState: "completed",
        revision: event.revision,
        causeDecisionCompleted: true,
        exitPolicy: "confirm",
      });
    case "PHASE_CHANGED":
      assertPhaseTransition(state.phase, event.phase);
      return { ...state, phase: event.phase };
    case "TASK_SUBMITTED":
      if (state.mode === "mock_exam" && state.phase !== "answering") {
        throw new TaskTransitionError("A mock exam can only be submitted while answering");
      }
      if (state.mode !== "mock_exam" && state.phase !== "summary") {
        throw new TaskTransitionError("A learning task can only be submitted after summary");
      }
      if (state.mode !== "mock_exam" && state.reflectionRequired && !state.reflectionCompleted) {
        throw new TaskTransitionError("Required reflection must be committed before submission");
      }
      if (state.causeDecisionRequired && !state.causeDecisionCompleted) {
        throw new TaskTransitionError(
          "Required cause decision must be committed before submission",
        );
      }
      return serverCommitted(state, {
        phase: "submitted",
        serverState: "completed",
        submittedAt: event.submittedAt,
        exitPolicy: "immediate",
      });
  }
}

function emptyAttempt(questionId: string): QuestionAttemptDraft {
  return {
    questionId,
    selectedOptionIds: [],
    eliminatedOptionIds: [],
    marked: false,
    skipped: false,
    elapsedSeconds: 0,
    draftText: "",
  };
}

function cloneAttempt(attempt: QuestionAttemptDraft): QuestionAttemptDraft {
  if (attempt.questionId.trim().length === 0) {
    throw new TaskTransitionError("Attempt questionId is required");
  }
  if (
    new Set(attempt.selectedOptionIds).size !== attempt.selectedOptionIds.length ||
    attempt.selectedOptionIds.some((optionId) => !optionId.trim())
  ) {
    throw new TaskTransitionError("Selected options must be unique, non-empty IDs");
  }
  if (
    new Set(attempt.eliminatedOptionIds).size !== attempt.eliminatedOptionIds.length ||
    attempt.eliminatedOptionIds.some((optionId) => !optionId.trim())
  ) {
    throw new TaskTransitionError("Eliminated options must be unique, non-empty IDs");
  }
  if (
    attempt.selectedOptionIds.some((optionId) => attempt.eliminatedOptionIds.includes(optionId))
  ) {
    throw new TaskTransitionError("Selected and eliminated options must not overlap");
  }
  if (attempt.skipped && attempt.selectedOptionIds.length > 0) {
    throw new TaskTransitionError("A skipped question cannot have a selected option");
  }
  assertElapsedSeconds(attempt.elapsedSeconds, "Question elapsed seconds");
  if (attempt.materialPosition) assertMaterialPosition(attempt.materialPosition);
  return {
    ...attempt,
    selectedOptionIds: [...attempt.selectedOptionIds],
    eliminatedOptionIds: [...attempt.eliminatedOptionIds],
    ...(attempt.materialPosition ? { materialPosition: { ...attempt.materialPosition } } : {}),
  };
}

function currentQuestionId(state: TaskRuntimeState): string {
  const questionId = state.questionOrder[state.currentQuestionIndex];
  if (!questionId) throw new TaskTransitionError("Current question does not exist");
  return questionId;
}

function updateAttempt(
  state: TaskRuntimeState,
  update: (attempt: QuestionAttemptDraft) => QuestionAttemptDraft,
  statePatch: Partial<TaskRuntimeState> = {},
): TaskRuntimeState {
  const questionId = currentQuestionId(state);
  const attempt = state.attempts[questionId];
  if (!attempt) throw new TaskTransitionError("Current attempt does not exist");
  return changed(state, {
    ...statePatch,
    attempts: { ...state.attempts, [questionId]: update(attempt) },
  });
}

function changed(state: TaskRuntimeState, patch: Partial<TaskRuntimeState>): TaskRuntimeState {
  return { ...clearRequestId(state), ...patch, syncState: "pending" };
}

function serverCommitted(
  state: TaskRuntimeState,
  patch: Partial<TaskRuntimeState>,
): TaskRuntimeState {
  return { ...clearRequestId(state), ...patch, syncState: "saved" };
}

function clearRequestId(state: TaskRuntimeState): TaskRuntimeState {
  const next = { ...state };
  delete next.requestId;
  return next;
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}

function assertRevision(revision: number): void {
  if (!Number.isInteger(revision) || revision < 0) {
    throw new TaskTransitionError("Revision must be a non-negative integer");
  }
}

function assertServerRevision(state: TaskRuntimeState, revision: number): void {
  assertRevision(revision);
  if (revision < state.revision) {
    throw new TaskTransitionError("A server revision cannot move backwards");
  }
}

function assertElapsedSeconds(value: number, label: string): void {
  if (!Number.isInteger(value) || value < 0) {
    throw new TaskTransitionError(`${label} must be a non-negative integer`);
  }
}

function assertMaterialPosition(position: QuestionAttemptDraft["materialPosition"]): void {
  if (
    !position ||
    !position.anchorId.trim() ||
    !Number.isFinite(position.zoom) ||
    !Number.isFinite(position.scrollTop) ||
    position.zoom <= 0 ||
    position.scrollTop < 0
  ) {
    throw new TaskTransitionError("Material position is invalid");
  }
}

function assertOptionalIdentifier(value: string | undefined, label: string): void {
  if (value !== undefined && !value.trim()) {
    throw new TaskTransitionError(`${label} must not be empty`);
  }
}

function assertAnswering(state: TaskRuntimeState): void {
  assertPhase(state, "answering");
}

function assertPhase(state: TaskRuntimeState, phase: TaskRuntimeState["phase"]): void {
  if (state.phase !== phase) {
    throw new TaskTransitionError(`Expected phase ${phase}, received ${state.phase}`);
  }
}

function assertPhaseTransition(
  current: TaskRuntimeState["phase"],
  next: TaskRuntimeState["phase"],
): void {
  const transitions: Record<TaskRuntimeState["phase"], TaskRuntimeState["phase"][]> = {
    briefing: ["answering"],
    answering: ["feedback"],
    feedback: ["reflection"],
    reflection: ["candidate_review", "summary"],
    candidate_review: ["reflection", "variant_verification", "summary"],
    variant_verification: ["summary"],
    summary: ["submitted"],
    submitted: [],
  };
  if (!transitions[current].includes(next)) {
    throw new TaskTransitionError(`Invalid phase transition: ${current} -> ${next}`);
  }
}

export function unansweredQuestionIds(state: TaskRuntimeState): string[] {
  return state.questionOrder.filter((questionId) => {
    const attempt = state.attempts[questionId];
    return !attempt || (!attempt.skipped && attempt.selectedOptionIds.length === 0);
  });
}
