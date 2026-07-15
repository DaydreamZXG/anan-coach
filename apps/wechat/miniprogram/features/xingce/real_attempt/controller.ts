import type { TodayDashboard } from "../../../api/generated/types.gen";
import type {
  XingceCheckpointCommand,
  XingceTaskMode,
  XingceTaskSession,
} from "../../../api/xingce-learning";
import type { QuestionAttemptDraft, TaskMode, TaskPhase } from "../../../task-runtime/contracts";
import { createTaskRuntime, reduceTaskRuntime } from "../../../task-runtime/state/runtime";
import type { TaskRuntimeState } from "../../../task-runtime/contracts";
import { assertLearnerSafeSession, normalizeSessionAnswers } from "./contracts";

export class XingcePageContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "XingcePageContractError";
  }
}

export function resolveXingcePlanTaskId(
  dashboard: TodayDashboard,
  mode: XingceTaskMode,
  expectedPlanTaskId?: string,
): string {
  const stages = stageSet(mode);
  const stageMatches = dashboard.tasks.filter(
    (task) =>
      task.track === "xingce" &&
      stages.has(task.stage) &&
      (task.status === "ready" || task.status === "in_progress"),
  );
  if (expectedPlanTaskId) {
    const exact = dashboard.tasks.find((task) => task.id === expectedPlanTaskId);
    if (!exact) {
      throw new XingcePageContractError("指定的行测任务不在当前 Today 计划中。");
    }
    if (!stageMatches.some((task) => task.id === exact.id)) {
      throw new XingcePageContractError("指定任务不属于当前行测模式或已不是可执行状态。");
    }
    return exact.id;
  }
  const matches = stageMatches;
  if (matches.length !== 1) {
    throw new XingcePageContractError(
      matches.length === 0
        ? "当前今日计划没有与入口匹配的可执行行测任务。"
        : "当前今日计划存在多个同类行测任务，无法安全猜测要执行哪一个。",
    );
  }
  const task = matches[0];
  if (!task) throw new XingcePageContractError("当前行测任务不存在。");
  return task.id;
}

export function runtimeWithPendingCheckpoint(
  session: XingceTaskSession,
  command: XingceCheckpointCommand,
): TaskRuntimeState {
  const server = runtimeFromXingceSession(session);
  if (server.serverState !== "answering") return server;
  const questions = new Map(
    session.bundle.questions.map((question) => [
      question.questionId,
      new Set(question.options.map((option) => option.optionId)),
    ]),
  );
  const attempts = { ...server.attempts };
  for (const answer of command.answers) {
    const optionIds = questions.get(answer.questionId);
    if (
      !optionIds ||
      [...answer.selectedOptionIds, ...answer.eliminatedOptionIds].some(
        (optionId) => !optionIds.has(optionId),
      )
    ) {
      throw new XingcePageContractError("待重试断点与当前服务端题组不一致。");
    }
    attempts[answer.questionId] = toRuntimeAttempt(answer);
  }
  const currentQuestionIndex = server.questionOrder.indexOf(command.currentQuestionId);
  if (currentQuestionIndex < 0) {
    throw new XingcePageContractError("待重试断点的当前题不在服务端题组中。");
  }
  const restored = createTaskRuntime({
    taskId: server.taskId,
    serverSessionId: server.serverSessionId,
    planTaskId: server.planTaskId,
    contentVersionId: server.contentVersionId,
    mode: server.mode,
    questionOrder: server.questionOrder,
    currentQuestionIndex,
    attempts,
    totalElapsedSeconds: Math.max(server.totalElapsedSeconds, command.totalElapsedSeconds),
    revision: server.revision,
    serverState: "answering",
    phase: "answering",
  });
  return reduceTaskRuntime(restored, {
    type: "SAVE_FAILED",
    requestId: "pending-checkpoint-retry",
  });
}

export function runtimeFromXingceSession(sessionValue: XingceTaskSession): TaskRuntimeState {
  const session = assertLearnerSafeSession(sessionValue);
  const questionOrder = session.bundle.questions.map((item) => item.questionId);
  const currentQuestionIndex = questionOrder.indexOf(session.currentQuestionId);
  const phase = phaseForServerState(session.state);
  const attempts = Object.fromEntries(
    normalizeSessionAnswers(session).map((answer) => [answer.questionId, toRuntimeAttempt(answer)]),
  );
  return createTaskRuntime({
    taskId: session.sessionId,
    serverSessionId: session.sessionId,
    planTaskId: session.planTaskId,
    contentVersionId: session.bundle.bundleVersionId,
    mode: runtimeMode(session.mode),
    questionOrder,
    currentQuestionIndex,
    attempts,
    totalElapsedSeconds: session.totalElapsedSeconds,
    revision: session.revision,
    serverState: session.state,
    phase,
    reflectionRequired: session.review !== null,
    reflectionCompleted: session.review?.reflection !== null,
    causeDecisionRequired: session.review?.state === "cause_decision_required",
    causeDecisionCompleted: session.review?.causeDecision !== null,
    reviewId: session.review?.reviewId,
  });
}

export function mergePendingDraft(
  authoritative: XingceTaskSession,
  local: TaskRuntimeState,
): TaskRuntimeState {
  const server = runtimeFromXingceSession(authoritative);
  if (server.serverState !== "answering" || local.serverSessionId !== server.serverSessionId) {
    return server;
  }
  const questionIds = new Set(server.questionOrder);
  const attempts = Object.fromEntries(
    server.questionOrder.map((questionId) => [
      questionId,
      cloneAttempt(
        questionIds.has(questionId) && local.attempts[questionId]
          ? local.attempts[questionId]
          : server.attempts[questionId],
      ),
    ]),
  );
  const merged = createTaskRuntime({
    taskId: server.taskId,
    serverSessionId: server.serverSessionId,
    planTaskId: server.planTaskId,
    contentVersionId: server.contentVersionId,
    mode: server.mode,
    questionOrder: server.questionOrder,
    currentQuestionIndex: Math.min(local.currentQuestionIndex, server.questionOrder.length - 1),
    attempts,
    totalElapsedSeconds: Math.max(local.totalElapsedSeconds, server.totalElapsedSeconds),
    revision: server.revision,
    serverState: server.serverState,
    phase: "answering",
  });
  return reduceTaskRuntime(merged, { type: "SAVE_FAILED", requestId: "reconcile-pending" });
}

export function checkpointCommand(runtime: TaskRuntimeState): XingceCheckpointCommand {
  const answers = runtime.questionOrder
    .map((questionId) => runtime.attempts[questionId])
    .filter((answer): answer is QuestionAttemptDraft => Boolean(answer))
    .filter(
      (answer) =>
        answer.skipped || answer.selectedOptionIds.length > 0 || answer.draftText.trim().length > 0,
    )
    .map((answer) => ({
      questionId: answer.questionId,
      selectedOptionIds: [...answer.selectedOptionIds],
      eliminatedOptionIds: [...answer.eliminatedOptionIds],
      confidence: answer.confidence ?? null,
      marked: answer.marked,
      skipped: answer.skipped,
      elapsedSeconds: answer.elapsedSeconds,
      draftText: answer.draftText,
      ...(answer.materialPosition ? { materialPosition: { ...answer.materialPosition } } : {}),
    }));
  if (answers.length === 0) {
    throw new XingcePageContractError("至少明确作答或跳过一道题后才能保存断点。");
  }
  const currentQuestionId = runtime.questionOrder[runtime.currentQuestionIndex];
  if (!currentQuestionId) throw new XingcePageContractError("当前题目不存在。");
  return {
    expectedRevision: runtime.revision,
    currentQuestionId,
    answers,
    totalElapsedSeconds: runtime.totalElapsedSeconds,
  };
}

function phaseForServerState(state: XingceTaskSession["state"]): TaskPhase {
  if (state === "answering") return "answering";
  if (state === "reflection_required") return "feedback";
  if (state === "cause_decision_required") return "candidate_review";
  return "summary";
}

function runtimeMode(mode: XingceTaskMode): TaskMode {
  return mode === "verification" ? "verification" : "practice";
}

function stageSet(mode: XingceTaskMode): ReadonlySet<string> {
  if (mode === "baseline") return new Set(["objective_baseline"]);
  if (mode === "verification") return new Set(["objective_verification"]);
  return new Set(["objective_practice"]);
}

function toRuntimeAttempt(answer: XingceTaskSession["answers"][number]): QuestionAttemptDraft {
  return {
    questionId: answer.questionId,
    selectedOptionIds: [...answer.selectedOptionIds],
    eliminatedOptionIds: [...answer.eliminatedOptionIds],
    ...(answer.confidence ? { confidence: answer.confidence } : {}),
    marked: answer.marked,
    skipped: answer.skipped,
    elapsedSeconds: answer.elapsedSeconds,
    draftText: answer.draftText,
    ...(answer.materialPosition ? { materialPosition: { ...answer.materialPosition } } : {}),
  };
}

function cloneAttempt(attempt: QuestionAttemptDraft | undefined): QuestionAttemptDraft {
  if (!attempt) throw new XingcePageContractError("服务端题组缺少对应作答断点。");
  return {
    ...attempt,
    selectedOptionIds: [...attempt.selectedOptionIds],
    eliminatedOptionIds: [...attempt.eliminatedOptionIds],
    ...(attempt.materialPosition ? { materialPosition: { ...attempt.materialPosition } } : {}),
  };
}
