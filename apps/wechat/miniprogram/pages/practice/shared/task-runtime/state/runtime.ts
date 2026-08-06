import type {
  AttemptActionEvent,
  AttemptActionType,
  CreateTaskRuntimeInput,
  QuestionAttemptDraft,
  TaskRuntimeEvent,
  TaskRuntimeState,
} from "../../../../../task-runtime/contracts";

/** 与后端 AnswerCheckpoint.actionEvents 的上限一致。 */
const MAX_ACTION_EVENTS = 400;
/** 与后端 AnswerCheckpoint.annotations 的上限一致。 */
const MAX_ANNOTATION_STROKES = 120;
/** 草稿输入按键频繁，5 秒内只记一条 draft_edited 事实。 */
const DRAFT_EVENT_WINDOW_MS = 5_000;
/** 契约增量 v1：材料滚动锚点事实按 ≥2 秒节流。 */
const MATERIAL_SCROLL_EVENT_WINDOW_MS = 2_000;
/** 与后端 atMs/sessionAtMs 上限一致（86_400_000 毫秒）。 */
const MAX_AT_MS = 86_400_000;
/** 后端 AttemptActionEvent.detail 上限。 */
const MAX_EVENT_DETAIL_LENGTH = 200;

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
  if (input.sessionElapsedMs !== undefined) {
    assertElapsedSeconds(input.sessionElapsedMs, "Session elapsed milliseconds");
  }
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
    ...(input.sessionElapsedMs !== undefined
      ? {
          sessionElapsedMs: Math.max(
            input.sessionElapsedMs,
            (input.totalElapsedSeconds ?? 0) * 1000,
          ),
        }
      : {}),
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
    case "TICK": {
      if (!Number.isInteger(event.seconds) || event.seconds <= 0) {
        throw new TaskTransitionError("Tick seconds must be a positive integer");
      }
      if (event.ms !== undefined && (!Number.isInteger(event.ms) || event.ms < 0)) {
        throw new TaskTransitionError("Tick milliseconds must be a non-negative integer");
      }
      if (state.phase !== "answering") return state;
      // 契约增量 v1：毫秒时钟只在活跃 TICK 时前进，暂停/切题期间自然冻结。
      const tickMs = event.ms ?? event.seconds * 1000;
      return updateAttempt(
        state,
        (attempt) => ({
          ...attempt,
          elapsedSeconds: attempt.elapsedSeconds + event.seconds,
          elapsedMs: Math.min(attemptElapsedMs(attempt) + tickMs, MAX_AT_MS),
        }),
        {
          totalElapsedSeconds: state.totalElapsedSeconds + event.seconds,
          sessionElapsedMs: Math.min(sessionElapsedMs(state) + tickMs, MAX_AT_MS),
        },
      );
    }
    case "QUESTION_CHANGED": {
      assertAnswering(state);
      if (
        !Number.isInteger(event.index) ||
        event.index < 0 ||
        event.index >= state.questionOrder.length
      ) {
        throw new TaskTransitionError("Question index is outside the task");
      }
      if (event.index === state.currentQuestionIndex) return state;
      // 契约增量 v1：进出题各记一条跨题时间线事实（optionId/detail 不填）。
      const fromQuestionId = currentQuestionId(state);
      const fromAttempt = state.attempts[fromQuestionId];
      const toQuestionId = state.questionOrder[event.index];
      const toAttempt = toQuestionId ? state.attempts[toQuestionId] : undefined;
      if (!fromAttempt || !toQuestionId || !toAttempt) {
        throw new TaskTransitionError("Current attempt does not exist");
      }
      const sessionAtMs = sessionElapsedMs(state);
      return changed(state, {
        currentQuestionIndex: event.index,
        attempts: {
          ...state.attempts,
          [fromQuestionId]: withActionEvent(fromAttempt, "question_left", sessionAtMs),
          [toQuestionId]: withActionEvent(toAttempt, "question_entered", sessionAtMs),
        },
      });
    }
    case "ANSWER_SELECTED":
      assertAnswering(state);
      if (
        new Set(event.optionIds).size !== event.optionIds.length ||
        event.optionIds.some((optionId) => !optionId.trim())
      ) {
        throw new TaskTransitionError("Selected options must be unique, non-empty IDs");
      }
      return updateAttempt(state, (attempt) => {
        const changedSelection =
          [...attempt.selectedOptionIds].sort().join(",") !== [...event.optionIds].sort().join(",");
        const next: QuestionAttemptDraft = {
          ...attempt,
          selectedOptionIds: [...event.optionIds],
          eliminatedOptionIds: attempt.eliminatedOptionIds.filter(
            (optionId) => !event.optionIds.includes(optionId),
          ),
          skipped: false,
        };
        if (!changedSelection || event.optionIds.length === 0) return next;
        return withActionEvent(
          next,
          attempt.selectedOptionIds.length > 0 ? "option_changed" : "option_selected",
          sessionElapsedMs(state),
          { optionId: event.optionIds.join(",") },
        );
      });
    case "OPTION_ELIMINATED":
      assertAnswering(state);
      if (!event.optionId.trim()) throw new TaskTransitionError("Eliminated option ID is required");
      return updateAttempt(state, (attempt) =>
        withActionEvent(
          {
            ...attempt,
            selectedOptionIds: event.eliminated
              ? attempt.selectedOptionIds.filter((optionId) => optionId !== event.optionId)
              : attempt.selectedOptionIds,
            eliminatedOptionIds: event.eliminated
              ? unique([...attempt.eliminatedOptionIds, event.optionId])
              : attempt.eliminatedOptionIds.filter((optionId) => optionId !== event.optionId),
          },
          event.eliminated ? "option_eliminated" : "elimination_undone",
          sessionElapsedMs(state),
          { optionId: event.optionId },
        ),
      );
    case "CONFIDENCE_SET":
      assertAnswering(state);
      return updateAttempt(state, (attempt) =>
        attempt.confidence === event.confidence
          ? attempt
          : withActionEvent(
              { ...attempt, confidence: event.confidence },
              "confidence_set",
              sessionElapsedMs(state),
              { detail: event.confidence },
            ),
      );
    case "QUESTION_MARKED":
      assertAnswering(state);
      return updateAttempt(state, (attempt) =>
        attempt.marked === event.marked
          ? attempt
          : withActionEvent(
              { ...attempt, marked: event.marked },
              event.marked ? "marked" : "unmarked",
              sessionElapsedMs(state),
            ),
      );
    case "QUESTION_SKIPPED":
      assertAnswering(state);
      return updateAttempt(state, (attempt) =>
        withActionEvent(
          {
            ...attempt,
            selectedOptionIds: [],
            skipped: true,
          },
          "skipped",
          sessionElapsedMs(state),
        ),
      );
    case "UNANSWERED_FINALIZED": {
      assertAnswering(state);
      const hasUnanswered = state.questionOrder.some((questionId) => {
        const attempt = state.attempts[questionId];
        if (!attempt) throw new TaskTransitionError("Attempt question does not exist");
        return !attempt.skipped && attempt.selectedOptionIds.length === 0;
      });
      if (!hasUnanswered) return state;
      const atMs = sessionElapsedMs(state);
      const attempts = Object.fromEntries(
        state.questionOrder.map((questionId) => {
          const attempt = state.attempts[questionId];
          if (!attempt) throw new TaskTransitionError("Attempt question does not exist");
          if (attempt.skipped || attempt.selectedOptionIds.length > 0) {
            return [questionId, attempt];
          }
          return [
            questionId,
            withActionEvent({ ...attempt, skipped: true }, "skipped", atMs, {
              detail: "submit_with_unanswered",
            }),
          ];
        }),
      );
      return changed(state, { attempts });
    }
    case "DRAFT_CHANGED":
      assertAnswering(state);
      return updateAttempt(state, (attempt) => {
        const next: QuestionAttemptDraft = { ...attempt, draftText: event.text };
        const events = attempt.actionEvents ?? [];
        const lastDraftEvent = [...events].reverse().find((entry) => entry.type === "draft_edited");
        if (
          lastDraftEvent &&
          attemptElapsedMs(attempt) - lastDraftEvent.atMs < DRAFT_EVENT_WINDOW_MS
        ) {
          return next;
        }
        return withActionEvent(next, "draft_edited", sessionElapsedMs(state));
      });
    case "ANNOTATIONS_CHANGED":
      assertAnswering(state);
      if (event.strokes.length > MAX_ANNOTATION_STROKES) {
        throw new TaskTransitionError("Annotation strokes exceed the checkpoint limit");
      }
      return updateAttempt(state, (attempt) =>
        withActionEvent(
          {
            ...attempt,
            annotations: event.strokes.map((stroke) => ({
              ...stroke,
              ...(stroke.anchor ? { anchor: { ...stroke.anchor } } : {}),
              points: stroke.points.map((point) => ({ ...point })),
            })),
          },
          event.action === "added" ? "annotation_added" : "annotation_erased",
          sessionElapsedMs(state),
        ),
      );
    case "ACTION_LOGGED":
      assertAnswering(state);
      return updateAttempt(state, (attempt) =>
        withActionEvent(
          attempt,
          event.action,
          sessionElapsedMs(state),
          event.detail ? { detail: event.detail } : {},
        ),
      );
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
      return updateAttempt(state, (attempt) => {
        const next: QuestionAttemptDraft = {
          ...attempt,
          materialPosition: { ...event.position },
        };
        // 契约增量 v1：材料锚点事实按 ≥2 秒节流；快照 materialPosition 行为保留不变。
        const events = attempt.actionEvents ?? [];
        const lastScrollEvent = [...events]
          .reverse()
          .find((entry) => entry.type === "material_scrolled");
        if (
          lastScrollEvent &&
          attemptElapsedMs(attempt) - lastScrollEvent.atMs < MATERIAL_SCROLL_EVENT_WINDOW_MS
        ) {
          return next;
        }
        return withActionEvent(next, "material_scrolled", sessionElapsedMs(state), {
          detail: event.position.anchorId.slice(0, MAX_EVENT_DETAIL_LENGTH),
        });
      });
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

/**
 * 把一条动作事实追加到当前题的时间线。
 * 契约增量 v1：atMs 取本题活跃毫秒（真毫秒精度），sessionAtMs 取会话全局累计毫秒；
 * 达到 400 上限时不再静默丢弃，而是置 actionEventsTruncated。
 */
function withActionEvent(
  attempt: QuestionAttemptDraft,
  type: AttemptActionType,
  sessionAtMs: number,
  extra: Pick<AttemptActionEvent, "optionId" | "detail"> | Record<string, never> = {},
): QuestionAttemptDraft {
  const events = attempt.actionEvents ?? [];
  if (events.length >= MAX_ACTION_EVENTS) {
    return attempt.actionEventsTruncated ? attempt : { ...attempt, actionEventsTruncated: true };
  }
  return {
    ...attempt,
    actionEvents: [
      ...events,
      {
        atMs: Math.min(attemptElapsedMs(attempt), MAX_AT_MS),
        type,
        sessionAtMs: Math.min(sessionAtMs, MAX_AT_MS),
        ...extra,
      },
    ],
  };
}

/** 本题活跃毫秒；旧数据没有 elapsedMs 时按 elapsedSeconds 的秒级精度换算。 */
function attemptElapsedMs(attempt: QuestionAttemptDraft): number {
  return attempt.elapsedMs ?? attempt.elapsedSeconds * 1000;
}

/** 会话全局活跃毫秒；旧数据没有 sessionElapsedMs 时按 totalElapsedSeconds 换算。 */
function sessionElapsedMs(state: TaskRuntimeState): number {
  return state.sessionElapsedMs ?? state.totalElapsedSeconds * 1000;
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
  if (attempt.elapsedMs !== undefined) {
    assertElapsedSeconds(attempt.elapsedMs, "Question elapsed milliseconds");
  }
  if (attempt.materialPosition) assertMaterialPosition(attempt.materialPosition);
  return {
    ...attempt,
    selectedOptionIds: [...attempt.selectedOptionIds],
    eliminatedOptionIds: [...attempt.eliminatedOptionIds],
    ...(attempt.materialPosition ? { materialPosition: { ...attempt.materialPosition } } : {}),
    ...(attempt.annotations
      ? {
          annotations: attempt.annotations.map((stroke) => ({
            ...stroke,
            ...(stroke.anchor ? { anchor: { ...stroke.anchor } } : {}),
            points: stroke.points.map((point) => ({ ...point })),
          })),
        }
      : {}),
    ...(attempt.actionEvents
      ? { actionEvents: attempt.actionEvents.map((entry) => ({ ...entry })) }
      : {}),
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
