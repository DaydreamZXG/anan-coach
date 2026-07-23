import type {
  MockExamAnswerCheckpoint,
  MockExamCheckpointRequest,
  MockExamSession,
  MockExamSubmitRequest,
} from "../../api/generated/types.gen";

/** 单题作答草稿（不含材料阅读位置，新版暂不恢复该维度） */
export interface MockAttempt {
  questionId: string;
  selectedAnswer: string | null;
  eliminatedOptionIds: string[];
  marked: boolean;
  draftText: string;
}

/** 整卷运行时状态：revision 乐观锁 + 题序 + 当前位置 + 全部作答 */
export interface MockRuntimeState {
  revision: number;
  questionOrder: string[];
  currentQuestionIndex: number;
  attempts: Record<string, MockAttempt>;
}

/** 从服务端会话恢复本地运行时（断点续做的入口） */
export function runtimeFromSession(session: MockExamSession): MockRuntimeState {
  if (session.status !== "assigned") throw new Error("只有进行中的模考才能继续作答");
  const questionOrder = session.questions.map((question) => question.questionId);
  let currentQuestionIndex = questionOrder.indexOf(session.checkpoint.currentQuestionId);
  if (currentQuestionIndex < 0) currentQuestionIndex = 0;
  const answers = new Map(session.checkpoint.answers.map((answer) => [answer.questionId, answer]));
  const attempts: Record<string, MockAttempt> = {};
  for (const questionId of questionOrder) {
    const answer = answers.get(questionId);
    attempts[questionId] = {
      questionId,
      selectedAnswer: answer?.selectedAnswer ?? null,
      eliminatedOptionIds: [...(answer?.eliminatedOptionIds ?? [])],
      marked: answer?.marked ?? false,
      draftText: answer?.draftText ?? "",
    };
  }
  return {
    revision: session.revision,
    questionOrder,
    currentQuestionIndex,
    attempts,
  };
}

/** 构造 checkpoint 命令：默认只带脏题 + 当前题，未脏题目不上行 */
export function buildCheckpointCommand(
  state: MockRuntimeState,
  changedQuestionIds: ReadonlySet<string> = new Set(state.questionOrder),
): MockExamCheckpointRequest {
  const currentQuestionId = state.questionOrder[state.currentQuestionIndex];
  if (!currentQuestionId) throw new Error("整卷缺少当前题");
  const included = state.questionOrder.filter(
    (questionId) => changedQuestionIds.has(questionId) || questionId === currentQuestionId,
  );
  return {
    expectedRevision: state.revision,
    currentQuestionId,
    answers: included.map((questionId) => checkpointFromAttempt(state, questionId)),
  };
}

/** 构造交卷命令：整卷全量答案 */
export function buildSubmitCommand(state: MockRuntimeState): MockExamSubmitRequest {
  return {
    expectedRevision: state.revision,
    answers: state.questionOrder.map((questionId) => ({
      questionId,
      selectedAnswer: requiredAttempt(state, questionId).selectedAnswer,
    })),
  };
}

/** 把本地暂存的 checkpoint 重放到运行时（仅当 revision 匹配） */
export function applyPendingCheckpoint(
  state: MockRuntimeState,
  command: MockExamCheckpointRequest,
): MockRuntimeState {
  if (command.expectedRevision !== state.revision) return state;
  const answerById = new Map(command.answers.map((answer) => [answer.questionId, answer]));
  if ([...answerById.keys()].some((questionId) => !state.questionOrder.includes(questionId))) {
    throw new Error("本地暂存进度与整卷题目不匹配");
  }
  const currentQuestionIndex = state.questionOrder.indexOf(command.currentQuestionId);
  const attempts: Record<string, MockAttempt> = {};
  for (const questionId of state.questionOrder) {
    const current = requiredAttempt(state, questionId);
    const pending = answerById.get(questionId);
    attempts[questionId] = pending
      ? {
          questionId,
          selectedAnswer: pending.selectedAnswer ?? null,
          eliminatedOptionIds: [...(pending.eliminatedOptionIds ?? [])],
          marked: pending.marked ?? false,
          draftText: pending.draftText ?? "",
        }
      : current;
  }
  return {
    ...state,
    currentQuestionIndex:
      currentQuestionIndex >= 0 ? currentQuestionIndex : state.currentQuestionIndex,
    attempts,
  };
}

/** 把本地暂存的交卷命令重放到运行时（交卷恢复确认用） */
export function applyPendingSubmit(
  state: MockRuntimeState,
  command: MockExamSubmitRequest,
): MockRuntimeState {
  const answerById = new Map(command.answers.map((answer) => [answer.questionId, answer]));
  if (state.questionOrder.some((questionId) => !answerById.has(questionId))) {
    throw new Error("本地暂存交卷与整卷题目不匹配");
  }
  const attempts: Record<string, MockAttempt> = {};
  for (const questionId of state.questionOrder) {
    const current = requiredAttempt(state, questionId);
    const pending = answerById.get(questionId);
    attempts[questionId] = {
      ...current,
      selectedAnswer: pending?.selectedAnswer ?? null,
    };
  }
  return { ...state, attempts };
}

/** 服务器 revision 前进后，本地保留作答只更新基线 */
export function withServerRevision(state: MockRuntimeState, revision: number): MockRuntimeState {
  if (revision < state.revision) throw new Error("服务端版本号不能回退");
  return { ...state, revision };
}

/** 冲突后：以最新会话为基线，整体覆盖回本机作答，整卷标脏等待重存 */
export function rebaseLocalOntoSession(
  local: MockRuntimeState,
  session: MockExamSession,
): MockRuntimeState {
  const base = runtimeFromSession(session);
  const attempts: Record<string, MockAttempt> = {};
  for (const questionId of base.questionOrder) {
    const localAttempt = local.attempts[questionId];
    attempts[questionId] = localAttempt ? { ...localAttempt } : requiredAttempt(base, questionId);
  }
  const currentQuestionIndex =
    local.currentQuestionIndex >= 0 && local.currentQuestionIndex < base.questionOrder.length
      ? local.currentQuestionIndex
      : base.currentQuestionIndex;
  return { ...base, currentQuestionIndex, attempts };
}

export function remainingSeconds(deadlineAt: string, nowMs: number): number {
  const deadlineMs = Date.parse(deadlineAt);
  if (!Number.isFinite(deadlineMs) || !Number.isFinite(nowMs)) {
    throw new Error("模考计时需要有效的绝对时间");
  }
  return Math.max(0, Math.ceil((deadlineMs - nowMs) / 1000));
}

export function formatRemainingTime(totalSeconds: number): string {
  if (!Number.isInteger(totalSeconds) || totalSeconds < 0) {
    throw new Error("剩余时间必须是非负整数");
  }
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
}

export function countAnswers(state: MockRuntimeState): {
  answered: number;
  unanswered: number;
  marked: number;
} {
  const attempts = state.questionOrder.map((questionId) => requiredAttempt(state, questionId));
  const answered = attempts.filter((attempt) => attempt.selectedAnswer !== null).length;
  const marked = attempts.filter((attempt) => attempt.marked).length;
  return { answered, unanswered: attempts.length - answered, marked };
}

export function requiredAttempt(state: MockRuntimeState, questionId: string): MockAttempt {
  const attempt = state.attempts[questionId];
  if (!attempt) throw new Error("题目不属于本场整卷");
  return attempt;
}

function checkpointFromAttempt(
  state: MockRuntimeState,
  questionId: string,
): MockExamAnswerCheckpoint {
  const attempt = requiredAttempt(state, questionId);
  return {
    questionId,
    selectedAnswer: attempt.selectedAnswer,
    eliminatedOptionIds: [...attempt.eliminatedOptionIds],
    marked: attempt.marked,
    draftText: attempt.draftText,
  };
}
