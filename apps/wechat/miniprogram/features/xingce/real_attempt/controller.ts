import type { TodayDashboard } from "../../../api/generated/types.gen";
import type {
  XingceCheckpointCommand,
  XingceTaskMode,
  XingceTaskSession,
} from "../../../api/xingce-learning";
import type {
  QuestionAttemptDraft,
  TaskMode,
  TaskPhase,
  TaskServerState,
} from "../../../task-runtime/contracts";
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
      throw new XingcePageContractError("待重试进度与当前题组不一致。");
    }
    attempts[answer.questionId] = toRuntimeAttempt(answer);
  }
  const currentQuestionIndex = server.questionOrder.indexOf(command.currentQuestionId);
  if (currentQuestionIndex < 0) {
    throw new XingcePageContractError("待重试进度的当前题不在当前题组中。");
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
    sessionElapsedMs: Math.max(
      restoredSessionElapsedMs(server),
      sessionElapsedMsFromAttempts(attempts),
    ),
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
    sessionElapsedMs: Math.max(
      session.totalElapsedSeconds * 1000,
      sessionElapsedMsFromAttempts(attempts),
    ),
    revision: session.revision,
    serverState: runtimeServerState(session.state),
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
    sessionElapsedMs: Math.max(
      restoredSessionElapsedMs(local),
      restoredSessionElapsedMs(server),
      sessionElapsedMsFromAttempts(attempts),
    ),
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
        answer.skipped ||
        answer.selectedOptionIds.length > 0 ||
        answer.eliminatedOptionIds.length > 0 ||
        answer.marked ||
        answer.draftText.trim().length > 0 ||
        answer.materialPosition !== undefined ||
        // 上线审计 §三 P1：只有批注轨迹或动作事实的题也必须入选，
        // 否则重启后永久丢失；全空题仍被排除，保持最小 payload。
        (answer.annotations !== undefined && answer.annotations.length > 0) ||
        (answer.actionEvents !== undefined && answer.actionEvents.length > 0),
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
      // Figma 70:2/75:2：批注与动作事实随检查点持久化。
      ...(answer.annotations && answer.annotations.length > 0
        ? { annotations: answer.annotations }
        : {}),
      ...(answer.actionEvents && answer.actionEvents.length > 0
        ? { actionEvents: answer.actionEvents }
        : {}),
      // 契约增量 v1：400 事件截断不再静默，上报溢出标记。
      ...(answer.actionEventsTruncated ? { actionEventsTruncated: true } : {}),
    }));
  if (answers.length === 0) {
    throw new XingcePageContractError("至少明确作答或跳过一道题后才能保存进度。");
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
  if (
    [
      "cause_candidate_processing",
      "cause_followup_required",
      "cause_decision_required",
      "cause_evidence_insufficient",
      "cause_agent_terminated",
    ].includes(state)
  ) {
    return "candidate_review";
  }
  return "summary";
}

function runtimeServerState(state: XingceTaskSession["state"]): TaskServerState {
  if (state === "answering" || state === "submitted" || state === "reflection_required") {
    return state;
  }
  if (state === "completed") return "completed";
  return "cause_decision_required";
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
  const elapsedMs = Math.max(
    answer.elapsedSeconds * 1000,
    ...(answer.actionEvents ?? []).map((entry) => entry.atMs),
    ...(answer.annotations ?? []).map((stroke) => stroke.atMs),
  );
  return {
    questionId: answer.questionId,
    selectedOptionIds: [...answer.selectedOptionIds],
    eliminatedOptionIds: [...answer.eliminatedOptionIds],
    ...(answer.confidence ? { confidence: answer.confidence } : {}),
    marked: answer.marked,
    skipped: answer.skipped,
    elapsedSeconds: answer.elapsedSeconds,
    elapsedMs,
    draftText: answer.draftText,
    ...(answer.materialPosition ? { materialPosition: { ...answer.materialPosition } } : {}),
    // 恢复会话时把已持久化的批注与动作事实带回运行时。
    ...(answer.annotations && answer.annotations.length > 0
      ? {
          annotations: answer.annotations.map((stroke) => ({
            strokeId: stroke.strokeId,
            tool: stroke.tool,
            width: stroke.width,
            points: stroke.points.map((point) => ({
              x: point.x,
              y: point.y,
              // 契约增量 v1：逐点毫秒偏移随会话恢复带回。
              ...(point.t !== undefined && point.t !== null ? { t: point.t } : {}),
              // 上线审计 §三 P1：已落库压感 p（0<p<=1，可选）原样带回，
              // 否则恢复后下一次保存会以无 p 的 payload 覆盖最新版本；
              // 无 p 的旧点不硬造该字段。
              ...(point.p !== undefined && point.p !== null ? { p: point.p } : {}),
            })),
            atMs: stroke.atMs,
            // 契约增量 v1：擦除墓碑随会话恢复带回，渲染层负责过滤。
            ...(stroke.erasedAtMs !== undefined && stroke.erasedAtMs !== null
              ? { erasedAtMs: stroke.erasedAtMs }
              : {}),
            // 契约增量 v2：锚定文字段随会话恢复带回，否则保存→重进后 AI 无法
            // 知道用户标记了哪句原文。
            ...(stroke.anchor
              ? {
                  anchor: {
                    blockId: stroke.anchor.blockId,
                    segmentIndex: stroke.anchor.segmentIndex,
                    segmentText: stroke.anchor.segmentText,
                  },
                }
              : {}),
          })),
        }
      : {}),
    ...(answer.actionEvents && answer.actionEvents.length > 0
      ? {
          actionEvents: answer.actionEvents.map((entry) => ({
            atMs: entry.atMs,
            type: entry.type,
            ...(entry.sessionAtMs !== undefined && entry.sessionAtMs !== null
              ? { sessionAtMs: entry.sessionAtMs }
              : {}),
            ...(entry.optionId ? { optionId: entry.optionId } : {}),
            ...(entry.detail ? { detail: entry.detail } : {}),
          })),
        }
      : {}),
    // 契约增量 v1：溢出标记随会话恢复带回，避免再次静默。
    ...(answer.actionEventsTruncated ? { actionEventsTruncated: true } : {}),
  };
}

function restoredSessionElapsedMs(runtime: TaskRuntimeState): number {
  return Math.max(
    runtime.sessionElapsedMs ?? runtime.totalElapsedSeconds * 1000,
    sessionElapsedMsFromAttempts(runtime.attempts),
  );
}

function sessionElapsedMsFromAttempts(attempts: Record<string, QuestionAttemptDraft>): number {
  return Math.max(
    0,
    ...Object.values(attempts).flatMap((attempt) =>
      (attempt.actionEvents ?? []).flatMap((entry) =>
        entry.sessionAtMs === undefined ? [] : [entry.sessionAtMs],
      ),
    ),
  );
}

function cloneAttempt(attempt: QuestionAttemptDraft | undefined): QuestionAttemptDraft {
  if (!attempt) throw new XingcePageContractError("当前题组缺少对应作答进度。");
  return {
    ...attempt,
    selectedOptionIds: [...attempt.selectedOptionIds],
    eliminatedOptionIds: [...attempt.eliminatedOptionIds],
    ...(attempt.materialPosition ? { materialPosition: { ...attempt.materialPosition } } : {}),
  };
}
