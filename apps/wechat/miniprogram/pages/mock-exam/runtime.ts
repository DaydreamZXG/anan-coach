import type {
  MockExamAnswerCheckpoint,
  MockExamCheckpointCommand,
  MockExamLearnerSession,
  MockExamSubmitCommand,
} from "../../api/mock-exam";
import type { QuestionAttemptDraft, TaskRuntimeState } from "../../task-runtime/contracts";
import { createTaskRuntime } from "../../task-runtime/state/runtime";

export interface MockExamTiming {
  startedAt: string;
  deadlineAt: string;
}

export function runtimeFromMockSession(
  session: MockExamLearnerSession,
  nowMs: number,
): TaskRuntimeState {
  if (session.status !== "assigned") throw new Error("Only an assigned mock exam can be answered");
  const questionOrder = session.questions.map((question) => question.questionId);
  const currentQuestionIndex = questionOrder.indexOf(session.checkpoint.currentQuestionId);
  if (currentQuestionIndex < 0)
    throw new Error("Mock checkpoint points outside the assigned paper");
  const answers = new Map(session.checkpoint.answers.map((answer) => [answer.questionId, answer]));
  const attempts = Object.fromEntries(
    questionOrder.map((questionId) => {
      const answer = answers.get(questionId);
      if (!answer) throw new Error("Mock checkpoint does not cover the whole assigned paper");
      return [questionId, attemptFromCheckpoint(answer)];
    }),
  );
  const totalElapsedSeconds = Math.max(
    0,
    Math.floor((nowMs - Date.parse(session.startedAt)) / 1000),
  );
  return {
    ...createTaskRuntime({
      taskId: session.sessionId,
      serverSessionId: session.sessionId,
      mode: "mock_exam",
      phase: "answering",
      serverState: "answering",
      questionOrder,
      currentQuestionIndex,
      attempts,
      totalElapsedSeconds,
      revision: session.revision,
    }),
    startedAt: session.startedAt,
  };
}

export function buildMockCheckpointCommand(
  state: TaskRuntimeState,
  changedQuestionIds: ReadonlySet<string> = new Set(state.questionOrder),
): MockExamCheckpointCommand {
  const currentQuestionId = state.questionOrder[state.currentQuestionIndex];
  if (!currentQuestionId) throw new Error("Mock paper has no current question");
  const includedQuestionIds = state.questionOrder.filter(
    (questionId) => changedQuestionIds.has(questionId) || questionId === currentQuestionId,
  );
  return {
    expectedRevision: state.revision,
    currentQuestionId,
    answers: includedQuestionIds.map((questionId) => checkpointFromAttempt(state, questionId)),
  };
}

export function buildMockSubmitCommand(state: TaskRuntimeState): MockExamSubmitCommand {
  return {
    expectedRevision: state.revision,
    answers: state.questionOrder.map((questionId) => {
      const attempt = requiredAttempt(state, questionId);
      return {
        questionId,
        selectedAnswer: attempt.selectedOptionIds.at(0) ?? null,
      };
    }),
  };
}

export function runtimeWithServerRevision(
  local: TaskRuntimeState,
  session: MockExamLearnerSession,
): TaskRuntimeState {
  if (session.sessionId !== local.taskId) throw new Error("Mock session ownership changed");
  if (session.revision < local.revision) throw new Error("Mock server revision moved backwards");
  return { ...local, revision: session.revision, syncState: "pending" };
}

export function runtimeWithPendingMockCheckpoint(
  state: TaskRuntimeState,
  command: MockExamCheckpointCommand,
): TaskRuntimeState {
  if (command.expectedRevision !== state.revision) return state;
  const answerById = new Map(command.answers.map((answer) => [answer.questionId, answer]));
  if (
    answerById.size === 0 ||
    [...answerById].some(([questionId]) => !state.questionOrder.includes(questionId))
  ) {
    throw new Error("Pending mock checkpoint does not match the assigned paper");
  }
  const currentQuestionIndex = state.questionOrder.indexOf(command.currentQuestionId);
  if (currentQuestionIndex < 0) throw new Error("Pending mock checkpoint points outside the paper");
  return {
    ...state,
    currentQuestionIndex,
    attempts: Object.fromEntries(
      state.questionOrder.map((questionId) => {
        const current = requiredAttempt(state, questionId);
        const pending = answerById.get(questionId);
        if (!pending) return [questionId, current];
        return [
          questionId,
          {
            ...current,
            selectedOptionIds: pending.selectedAnswer ? [pending.selectedAnswer] : [],
            eliminatedOptionIds: [...pending.eliminatedOptionIds],
            marked: pending.marked,
            draftText: pending.draftText,
            ...(pending.materialPosition
              ? { materialPosition: { ...pending.materialPosition } }
              : {}),
          },
        ];
      }),
    ),
    syncState: "pending",
  };
}

export function runtimeWithPendingMockSubmit(
  state: TaskRuntimeState,
  command: MockExamSubmitCommand,
): TaskRuntimeState {
  const answerById = new Map(command.answers.map((answer) => [answer.questionId, answer]));
  if (
    answerById.size !== state.questionOrder.length ||
    state.questionOrder.some((questionId) => !answerById.has(questionId))
  ) {
    throw new Error("Pending mock submission does not match the assigned paper");
  }
  return {
    ...state,
    attempts: Object.fromEntries(
      state.questionOrder.map((questionId) => {
        const attempt = requiredAttempt(state, questionId);
        const pending = answerById.get(questionId);
        if (!pending) throw new Error("Pending mock submission answer is missing");
        return [
          questionId,
          {
            ...attempt,
            selectedOptionIds: pending.selectedAnswer ? [pending.selectedAnswer] : [],
            eliminatedOptionIds: pending.selectedAnswer
              ? attempt.eliminatedOptionIds.filter(
                  (optionId) => optionId !== pending.selectedAnswer,
                )
              : attempt.eliminatedOptionIds,
          },
        ];
      }),
    ),
    syncState: "pending",
  };
}

export function remainingMockSeconds(deadlineAt: string, nowMs: number): number {
  const deadlineMs = Date.parse(deadlineAt);
  if (!Number.isFinite(deadlineMs) || !Number.isFinite(nowMs)) {
    throw new Error("Mock exam timing requires valid absolute instants");
  }
  return Math.max(0, Math.ceil((deadlineMs - nowMs) / 1000));
}

export function formatRemainingTime(totalSeconds: number): string {
  if (!Number.isInteger(totalSeconds) || totalSeconds < 0) {
    throw new Error("Remaining time must be a non-negative integer");
  }
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
}

export function countMockAnswers(state: TaskRuntimeState): {
  answered: number;
  unanswered: number;
  marked: number;
} {
  const attempts = state.questionOrder.map((questionId) => requiredAttempt(state, questionId));
  const answered = attempts.filter((attempt) => attempt.selectedOptionIds.length > 0).length;
  const marked = attempts.filter((attempt) => attempt.marked).length;
  return { answered, unanswered: attempts.length - answered, marked };
}

function attemptFromCheckpoint(answer: MockExamAnswerCheckpoint): QuestionAttemptDraft {
  return {
    questionId: answer.questionId,
    selectedOptionIds: answer.selectedAnswer ? [answer.selectedAnswer] : [],
    eliminatedOptionIds: [...answer.eliminatedOptionIds],
    marked: answer.marked,
    skipped: false,
    elapsedSeconds: 0,
    draftText: answer.draftText,
    ...(answer.materialPosition ? { materialPosition: { ...answer.materialPosition } } : {}),
  };
}

function checkpointFromAttempt(
  state: TaskRuntimeState,
  questionId: string,
): MockExamAnswerCheckpoint {
  const attempt = requiredAttempt(state, questionId);
  return {
    questionId,
    selectedAnswer: attempt.selectedOptionIds.at(0) ?? null,
    eliminatedOptionIds: [...attempt.eliminatedOptionIds],
    marked: attempt.marked,
    draftText: attempt.draftText,
    ...(attempt.materialPosition ? { materialPosition: { ...attempt.materialPosition } } : {}),
  };
}

function requiredAttempt(state: TaskRuntimeState, questionId: string): QuestionAttemptDraft {
  const attempt = state.attempts[questionId];
  if (!attempt) throw new Error("Question does not belong to this mock paper");
  return attempt;
}
