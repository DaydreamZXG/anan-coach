import type {
  AppDomainsPlanningSchemasTodayTask as TodayTask,
  InferenceCandidateVersion,
  LearnerCoachContext,
  LearningBrainProfileVersion,
  PlanningEvidenceRef,
  TodayDashboard,
  TrackAllocation,
} from "../../api/generated/types.gen";
import type { CoachEntryIntent } from "../../features/onboarding/coach-entry";
import type {
  CoachEvidenceCounters,
  CoachTaskBeforeSnapshot,
} from "../../features/coach-loop/feedback-store";
import {
  registeredTaskNavigationTarget,
  type RegisteredNavigationTarget,
} from "../../app/navigation/routes";
import { evidenceLabel, orderedTasks, scoreRangeLabel } from "../../domain/presentation";
import { withEssayTaskContext } from "../../features/essay-task/capabilities";
import { formatCapacityDuration } from "./capacity";

type LearningTrack = TodayTask["track"];
type TaskStage = TodayTask["stage"];

export interface PriorityMetricView {
  label: string;
  value: string;
}

export interface TaskView {
  id: string;
  kind: TodayTask["kind"];
  track: LearningTrack;
  title: string;
  reason: string;
  estimatedMinutes: number;
  completionRule: string;
  kindLabel: string;
  trackLabel: string;
  stageLabel: string;
  status: TodayTask["status"];
  statusLabel: string;
  elapsedMinutes: number | null;
  priorityMetrics: PriorityMetricView[];
  evidenceLabels: string[];
  canOpen: boolean;
  navigationMode: RegisteredNavigationTarget["mode"] | null;
  navigationUrl: string | null;
  disabledReason: string;
}

export interface TaskQueueView extends TaskView {
  queueSize: number;
  queueMetaLabel: string;
  queueActionLabel: string;
}

export interface TrackAllocationView {
  track: LearningTrack;
  trackLabel: string;
  minutes: number;
  reason: string;
  stageSummary: string;
}

export interface OmittedTrackReasonView {
  track: string;
  trackLabel: string;
  reason: string;
}

export interface TodayTrackView {
  track: "xingce" | "essay";
  label: string;
  summary: string;
  detail: string;
  actionLabel: string;
  taskId: string;
  tone: "active" | "complete" | "idle";
}

export type TodayDayState = "active" | "recovery" | "unavailable" | "complete";

export type CoachLoopPhase = "act" | "resume" | "reflect" | "verify";

export type CoachRecognitionLevel = "just_met" | "initial_hypothesis" | "verifying" | "understands";

export interface CoachStrategyCandidateView {
  candidateId: string;
  candidateVersionId: string;
  title: string;
  rationale: string;
}

export interface CoachReturnFeedbackView {
  title: string;
  changes: string[];
  nextAction: string;
}

export interface CoachLoopPresentationContext {
  profileStatus: "loading" | "ready" | "unavailable";
  coachContext: LearnerCoachContext | null;
  candidate: InferenceCandidateVersion | null;
  beforeSnapshot: CoachTaskBeforeSnapshot | null;
  entryIntent?: CoachEntryIntent | null;
}

export interface CoachLoopView {
  phase: CoachLoopPhase;
  statusLabel: string;
  recognitionLevel: CoachRecognitionLevel;
  recognitionLabel: string;
  observation: string;
  judgment: string;
  why: string;
  actionTitle: string;
  completionRule: string;
  verification: string;
  correction: string;
  feedbackPromise: string;
  feedback: CoachReturnFeedbackView | null;
  candidate: CoachStrategyCandidateView | null;
}

type CoachLoopTask = Pick<
  TaskView,
  "id" | "kind" | "status" | "reason" | "title" | "completionRule"
>;

export interface TodayView {
  headline: string;
  rationale: string;
  positionName: string;
  hasCompleteGoalGap: boolean;
  targetScore: string | null;
  currentScore: string | null;
  scoreGap: number | null;
  confidence: string;
  gapStateLabel: string;
  gapGuidance: string;
  evidenceSummary: string;
  capacityVersionId: string;
  availableMinutes: number;
  availableDurationLabel: string;
  energy: number;
  stress: number;
  confidenceLevel: number;
  capacityStatusLabel: string;
  capacitySourceLabel: string;
  planSectionTitle: string;
  planModeLabel: string;
  planModeSummary: string;
  trackAllocations: TrackAllocationView[];
  omittedTrackReasons: OmittedTrackReasonView[];
  tasks: TaskView[];
  activeTasks: TaskView[];
  loop: CoachLoopView | null;
  secondaryTasks: TaskQueueView[];
  completedTasks: TaskView[];
  hiddenCompletedCount: number;
  hiddenCompletedLabel: string;
  hasTasks: boolean;
  hasExecutionActivity: boolean;
  dayState: TodayDayState;
  emptyStateTitle: string;
  emptyStateCopy: string;
  trackViews: TodayTrackView[];
  dueSummary: string;
  isRecoveryDay: boolean;
}

const kindLabels: Record<TodayTask["kind"], string> = {
  practice: "针对训练",
  reflection: "错题复盘",
  verification: "间隔验证",
  mock_exam: "整卷模考",
  essay: "申论训练",
};

const trackLabels: Record<LearningTrack, string> = {
  xingce: "行测",
  essay: "申论",
  recovery: "恢复",
  mock: "模考",
};
const omittedTrackLabels: Readonly<Record<string, string>> = trackLabels;

const stageLabels: Record<TaskStage, string> = {
  objective_baseline: "基线诊断",
  objective_practice: "专项训练",
  objective_reflection: "复盘归因",
  objective_verification: "间隔验证",
  essay_material_reading: "材料理解",
  essay_point_extraction: "要点提炼",
  essay_outlining: "结构搭建",
  essay_drafting: "答案成稿",
  essay_review: "答案复盘",
  essay_rewrite: "二次改写",
  essay_transfer: "迁移验证",
  recovery_review: "恢复复习",
  weekly_mock: "整卷模考",
};

const statusLabels: Record<TodayTask["status"], string> = {
  ready: "待开始",
  in_progress: "进行中",
  complete: "已完成",
  abandoned: "已结束",
};

const modeLabels: Record<TodayDashboard["dualTrackExplanation"]["mode"], string> = {
  balanced: "双轨平衡",
  capacity_limited: "时间较少",
  mock_day: "模考日",
  recovery_day: "恢复日",
  evidence_missing: "还需补资料",
};

const ownerLabels: Record<string, string> = {
  planning: "计划依据",
  planning_agent: "计划依据",
  position_goal: "目标岗位",
  ability_estimation: "当前水平",
  "ability-estimation-agent": "当前水平",
  objective_learning: "行测学习记录",
  essay_intelligence: "申论学习记录",
  planning_execution: "任务执行",
  "learning-brain": "学习记录",
  "reflection-agent": "复盘记录",
};

const entityLabels: Record<string, string> = {
  ability_estimate: "当前能力估计",
  ability_estimate_version: "当前能力估计",
  daily_capacity: "今日可用时间",
  essay_attempt: "申论作答",
  essay_material_bundle_version: "申论材料",
  essay_review_version: "申论评阅",
  essay_rubric_version: "申论评分依据",
  essay_rewrite_version: "申论改写",
  essay_task_version: "申论任务",
  essay_transfer_verification: "申论迁移验证",
  evidence_collection_need: "待补学习记录",
  error_pattern: "错因模式",
  objective_state: "行测学习状态",
  position_goal_version: "主目标岗位",
  reflection: "复盘记录",
  score_evidence: "成绩证据",
  verification_schedule: "间隔验证日程",
  weekly_study_preference: "每周学习设置",
  task_execution_version: "任务完成记录",
};

const roleLabels: Readonly<Record<string, string>> = {
  ready: "待开始",
  in_progress: "进行中",
  complete: "已完成",
  abandoned: "已结束",
};

export function presentTodayDashboard(
  dashboard: TodayDashboard,
  coachContext: CoachLoopPresentationContext = loadingCoachContext(),
): TodayView {
  const gap = presentGoalGap(dashboard);
  const tasks = orderedTasks(dashboard.tasks).map(presentTask);
  const activeTasks = tasks.filter(
    (task) => task.status === "ready" || task.status === "in_progress",
  );
  const completedTasks = tasks.filter((task) => task.status === "complete");
  const completedExecutionCount = new Set(
    dashboard.evidenceRefs
      .filter((item) => item.entityType === "task_execution_version" && item.role === "complete")
      .map((item) => item.entityId),
  ).size;
  const hiddenCompletedCount = Math.max(0, completedExecutionCount - completedTasks.length);
  const secondaryTasks = presentSecondaryTaskQueues(activeTasks.slice(1));
  const hasExecutionActivity =
    tasks.some(
      (task) => task.status === "in_progress" || task.status === "complete" || task.elapsedMinutes,
    ) || completedExecutionCount > 0;
  const dayState = todayDayState(dashboard, activeTasks, hasExecutionActivity);
  const allocationTracks = new Set(
    dashboard.dualTrackExplanation.allocations.map((item) => item.track),
  );

  return {
    headline: dashboard.headline,
    rationale: dashboard.rationale,
    positionName: dashboard.goalGap.positionName ?? "目标分待生成",
    ...gap,
    evidenceSummary: dashboard.goalGap.evidenceSummary,
    capacityVersionId: dashboard.capacity.capacityVersionId,
    availableMinutes: dashboard.capacity.availableMinutes,
    availableDurationLabel: formatCapacityDuration(dashboard.capacity.availableMinutes),
    energy: dashboard.capacity.energy,
    stress: dashboard.capacity.stress,
    confidenceLevel: dashboard.capacity.confidenceLevel,
    capacityStatusLabel:
      dashboard.capacity.sourceType === "user_override" ? "今天已调整" : "按每周计划",
    capacitySourceLabel: capacitySourceLabel(dashboard),
    planSectionTitle: planSectionTitle(dashboard),
    planModeLabel: modeLabels[dashboard.dualTrackExplanation.mode],
    planModeSummary: dashboard.dualTrackExplanation.summary,
    trackAllocations: dashboard.dualTrackExplanation.allocations.map(presentAllocation),
    omittedTrackReasons: presentOmittedReasons(dashboard, allocationTracks),
    tasks,
    activeTasks,
    loop: presentCoachLoop(
      dashboard,
      activeTasks[0] ?? coachTaskFromSnapshot(coachContext.beforeSnapshot),
      coachContext,
    ),
    secondaryTasks,
    completedTasks,
    hiddenCompletedCount,
    hiddenCompletedLabel: `今日已完成 ${String(hiddenCompletedCount)} 项`,
    hasTasks: activeTasks.length > 0,
    hasExecutionActivity,
    dayState,
    ...emptyStateView(dashboard, dayState),
    trackViews: (["xingce", "essay"] as const).map((track) =>
      presentTodayTrack(dashboard, tasks, track),
    ),
    dueSummary: presentObjectiveClosureSummary(dashboard),
    isRecoveryDay: dashboard.dayMode === "recovery",
  };
}

export function presentCoachLoop(
  dashboard: TodayDashboard,
  task: CoachLoopTask | null,
  context: CoachLoopPresentationContext = loadingCoachContext(),
): CoachLoopView | null {
  if (!task) return null;
  const resumed = task.status === "in_progress";
  const recognition = presentRecognition(
    context.profileStatus,
    context.coachContext,
    context.entryIntent ?? null,
  );
  const profile = context.coachContext?.profile ?? null;
  const candidate = presentStrategyCandidate(context.candidate, profile);
  return {
    phase: resumed
      ? "resume"
      : task.kind === "reflection"
        ? "reflect"
        : task.kind === "verification"
          ? "verify"
          : "act",
    statusLabel: resumed ? "继续上次进度" : coachLoopStatusLabel(task.kind),
    recognitionLevel: recognition.level,
    recognitionLabel: recognition.label,
    observation: recognition.observation,
    judgment: candidate?.rationale || dashboard.headline.trim() || "岸岸正在整理当前判断",
    why: task.reason.trim() || dashboard.rationale.trim() || "当前任务暂未附带更多判断依据。",
    actionTitle: task.title,
    completionRule: task.completionRule.trim() || "任务页暂未给出完成标准。",
    verification: coachLoopVerification(task),
    correction: "如果完成后的权威证据不支持这个判断，岸岸会收回它，再用新证据重排下一项。",
    feedbackPromise: coachLoopFeedbackPromise(task.kind),
    feedback: presentReturnFeedback(dashboard, profile, context.beforeSnapshot),
    candidate,
  };
}

function loadingCoachContext(): CoachLoopPresentationContext {
  return {
    profileStatus: "loading",
    coachContext: null,
    candidate: null,
    beforeSnapshot: null,
    entryIntent: null,
  };
}

function presentRecognition(
  status: CoachLoopPresentationContext["profileStatus"],
  coachContext: LearnerCoachContext | null,
  entryIntent: CoachEntryIntent | null,
): { level: CoachRecognitionLevel; label: string; observation: string } {
  if (status === "loading") {
    return {
      level: "just_met",
      label: "正在同步证据",
      observation: "岸岸正在核对你的成绩、作答和复测记录，不会用缺失数据假装精准。",
    };
  }
  if (status === "unavailable" || !coachContext) {
    return {
      level: "just_met",
      label: "证据待同步",
      observation: "学习记录暂时没有同步成功；今天的任务仍可继续，但岸岸先不升级判断。",
    };
  }
  const recognition = coachContext.recognition;
  if (recognition.level !== "just_met") return recognition;
  return {
    ...recognition,
    observation: entryIntent ? entryIntentObservation(entryIntent) : recognition.observation,
  };
}

function entryIntentObservation(intent: CoachEntryIntent | null): string {
  if (!intent) return "岸岸还没看过你的真实作答，现在只用目标和可用时间决定先从哪里开始。";
  if (intent.path === "score_report") {
    return intent.evidenceSource === "score_report"
      ? "你刚导入了成绩单，岸岸正在等权威画像同步；同步前先不把粗轮廓当定论。"
      : "你选择先用最近成绩单认识自己；岸岸会先看分差，再安排一道任务验证粗判断。";
  }
  if (intent.path === "wrong_question") {
    return intent.evidenceSource === "external_answer"
      ? "你刚导入了真实错题作答，岸岸正在等它进入画像；暂时只把错因当候选。"
      : "你选择从错题开始；岸岸会结合当时答案、用时和思路提出一个待验证错因。";
  }
  if (intent.path === "quick_diagnostic") {
    return "你选择了快速诊断；这次只用来决定先从哪里开始，不会包装成完整能力测评。";
  }
  return intent.evidenceSource === "position_preference"
    ? "你已经确认了目标地区和岗位层级；岸岸会把它用于后续岗位筛选，再用真实作答校准目标分。"
    : "你还没选岗；岸岸先用岗位方向和目标分确定起点，再逐步补真实作答证据。";
}

export function coachEvidenceCounters(profile: LearningBrainProfileVersion): CoachEvidenceCounters {
  return {
    scoreEvidenceCount: profile.evidenceSummary.scoreEvidenceCount,
    xingceAttemptCount: profile.evidenceSummary.xingceAttemptCount,
    xingceMasteryCount: profile.evidenceSummary.xingceMasteryCount,
    essayMasteryCount: profile.evidenceSummary.essayMasteryCount,
    taskExecutionCount: profile.evidenceSummary.taskExecutionCount,
    externalAnswerCount: profile.evidenceSummary.externalAnswerCount ?? 0,
  };
}

function presentStrategyCandidate(
  candidate: InferenceCandidateVersion | null,
  profile: LearningBrainProfileVersion | null,
): CoachStrategyCandidateView | null {
  if (!candidate || candidate.status !== "awaiting_confirmation" || !profile) return null;
  const basedOnProfileVersionId = candidate.value.basedOnProfileVersionId;
  if (
    typeof basedOnProfileVersionId !== "string" ||
    basedOnProfileVersionId !== profile.profileVersionId
  ) {
    return null;
  }
  const rationale = candidate.value.rationale;
  const decisionType = candidate.value.decisionType;
  const scopeKey = candidate.value.scopeKey;
  if (typeof rationale !== "string" || !rationale.trim()) return null;
  return {
    candidateId: candidate.candidateId,
    candidateVersionId: candidate.candidateVersionId,
    title: strategyCandidateTitle(decisionType, scopeKey),
    rationale: rationale.trim(),
  };
}

function strategyCandidateTitle(decisionType: unknown, scopeKey: unknown): string {
  const scope =
    scopeKey === "xingce"
      ? "行测"
      : scopeKey === "essay"
        ? "申论"
        : typeof scopeKey === "string" && scopeKey.trim()
          ? scopeKey.trim()
          : "当前阶段";
  return (
    {
      baseline_guidance: `先校准${scope}基线`,
      exam_skip: `暂时跳过${scope}`,
      phase_deprioritize: `降低${scope}优先级`,
      strategic_abandon: `阶段性放下${scope}`,
    }[typeof decisionType === "string" ? decisionType : ""] ?? `调整${scope}侧重`
  );
}

function coachLoopVerification(task: CoachLoopTask): string {
  const evidence = {
    practice: "用本次正确率、用时和作答过程，看问题是知识缺口、方法不稳还是速度问题。",
    reflection: "用错因确认和下一次同类题复测，看这次归因能否解释并修复错误。",
    verification: "用间隔后的同类题表现，看改善是否稳定，而不是只在刚练完时记得。",
    mock_exam: "用整卷成绩、节奏和分模块表现，校准当前两科判断与训练侧重。",
    essay: "用本次作答、评阅证据和后续改写，验证当前申论卡点是否判断正确。",
  }[task.kind];
  return `${evidence} 完成标准：${task.completionRule.trim() || "以任务页的权威完成记录为准。"}`;
}

function coachTaskFromSnapshot(before: CoachTaskBeforeSnapshot | null): CoachLoopTask | null {
  if (!before) return null;
  return {
    id: before.taskId,
    kind: before.taskKind,
    status: "complete",
    reason: before.taskReason,
    title: before.taskTitle,
    completionRule: before.completionRule,
  };
}

export function coachReturnFeedbackConsumedAuthoritatively(
  dashboard: TodayDashboard,
  profile: LearningBrainProfileVersion | null,
  before: CoachTaskBeforeSnapshot | null,
): boolean {
  if (!before) return false;
  const currentTask = dashboard.tasks.find((task) => task.id === before.taskId);
  return (
    currentTask?.status === "complete" ||
    dashboard.planVersionId !== before.planVersionId ||
    Boolean(
      profile && before.profileVersionId && profile.profileVersionId !== before.profileVersionId,
    )
  );
}

function presentReturnFeedback(
  dashboard: TodayDashboard,
  profile: LearningBrainProfileVersion | null,
  before: CoachTaskBeforeSnapshot | null,
): CoachReturnFeedbackView | null {
  if (!before) return null;
  const changes: string[] = [];
  const currentTask = dashboard.tasks.find((task) => task.id === before.taskId);
  if (currentTask?.status === "complete") {
    changes.push(`你刚完成了“${before.taskTitle}”，这次结果已经记下来。`);
  }
  if (profile && before.evidence) {
    const current = coachEvidenceCounters(profile);
    addErrorPatternDeltas(changes, profile, before);
    addEvidenceDelta(
      changes,
      current.scoreEvidenceCount - before.evidence.scoreEvidenceCount,
      "份成绩证据已进入岸岸的判断",
    );
    addEvidenceDelta(
      changes,
      current.xingceAttemptCount - before.evidence.xingceAttemptCount,
      "道真实作答让岸岸更了解你的做题过程",
    );
    addEvidenceDelta(
      changes,
      current.externalAnswerCount - before.evidence.externalAnswerCount,
      "条你导入的作答已用于校准判断",
    );
    addEvidenceDelta(
      changes,
      current.xingceMasteryCount +
        current.essayMasteryCount -
        before.evidence.xingceMasteryCount -
        before.evidence.essayMasteryCount,
      "条掌握或复测证据改变了当前判断",
    );
    if (before.profileVersionId && profile.profileVersionId !== before.profileVersionId) {
      changes.push("岸岸已根据这次证据更新了对你的认识。");
    }
  }
  if (dashboard.planVersionId !== before.planVersionId) {
    changes.push("接下来的安排已经根据这次结果重新排过。");
  }
  if (changes.length === 0) {
    changes.push(
      currentTask?.status === "in_progress"
        ? "这项任务仍在进行中，岸岸还没有把过程当成新的结论。"
        : "暂时还没收到新的权威完成或学习证据，当前判断保持不变。",
    );
  }
  const next = dashboard.tasks.find(
    (task) => task.status === "ready" || task.status === "in_progress",
  );
  return {
    title: "这次练习带来了这些真实变化",
    changes: changes.map((item) => item.trim()),
    nextAction: next ? `下一步：${next.title}` : "下一步：今天先收口，等待结算和后续复测。",
  };
}

function addEvidenceDelta(changes: string[], delta: number, label: string): void {
  if (delta > 0) changes.push(`${String(delta)} ${label}。`);
}

function addErrorPatternDeltas(
  changes: string[],
  profile: LearningBrainProfileVersion,
  before: CoachTaskBeforeSnapshot,
): void {
  if (!before.errorPatterns) return;
  const previous = new Map(before.errorPatterns.map((pattern) => [pattern.patternId, pattern]));
  const current = new Map(profile.errorPatterns.map((pattern) => [pattern.patternId, pattern]));

  for (const pattern of profile.errorPatterns) {
    const prior = previous.get(pattern.patternId);
    if (!prior && pattern.status === "confirmed") {
      changes.push(`岸岸新增了一条待验证判断：“${pattern.displayName}”。`);
      continue;
    }
    if (!prior) continue;
    if (prior.status === "confirmed" && pattern.status === "counterevidence") {
      changes.push(`独立复测已形成反证，岸岸收回“${pattern.displayName}”这个当前弱项判断。`);
      continue;
    }
    if (
      pattern.status === "confirmed" &&
      pattern.counterevidenceCount > prior.counterevidenceCount
    ) {
      changes.push(`“${pattern.displayName}”出现了新的反向证据，但还有未修复记录，岸岸暂不收回。`);
    }
  }

  for (const pattern of before.errorPatterns) {
    if (pattern.status === "confirmed" && !current.has(pattern.patternId)) {
      changes.push(`“${pattern.displayName}”已不再出现在当前错误模式中，岸岸停止按它安排训练。`);
    }
  }
}

function coachLoopStatusLabel(kind: TodayTask["kind"]): string {
  return {
    practice: "先解决最高收益缺口",
    reflection: "先复盘，再加练",
    verification: "到了复测时间",
    mock_exam: "用整卷校准判断",
    essay: "推进申论主线",
  }[kind];
}

function coachLoopFeedbackPromise(kind: TodayTask["kind"]): string {
  return {
    practice: "完成后，岸岸会根据正确率、用时和过程记录更新当前判断与后续安排。",
    reflection: "完成后，岸岸会更新错因判断，并安排下一次复测时间。",
    verification: "完成后，岸岸会判断这个问题是否已经稳定修复，还是需要继续训练。",
    mock_exam: "完成后，岸岸会根据整卷结果更新两科判断和接下来的训练重点。",
    essay: "完成后，岸岸会根据本次作答与评阅结果更新当前卡点和下一步。",
  }[kind];
}

function todayDayState(
  dashboard: TodayDashboard,
  activeTasks: TaskView[],
  hasExecutionActivity: boolean,
): TodayDayState {
  if (activeTasks.length > 0) return "active";
  if (hasExecutionActivity) return "complete";
  if (dashboard.dayMode === "recovery") return "recovery";
  return "unavailable";
}

function emptyStateView(dashboard: TodayDashboard, dayState: TodayDayState) {
  if (dayState === "recovery") {
    if (dashboard.capacity.availableMinutes === 0) {
      return {
        emptyStateTitle: "今天已经设为休息",
        emptyStateCopy:
          "今天设为休息，不安排学习任务。没完成的内容不会留到明天，想学的时候随时调整就好。",
      };
    }
    return {
      emptyStateTitle: "今天没有必做任务",
      emptyStateCopy: `${dashboard.rationale} 今天有 ${formatCapacityDuration(dashboard.capacity.availableMinutes)} 可用，不代表必须排满，按自己状态来。`,
    };
  }
  if (dayState === "complete") {
    return {
      emptyStateTitle: "今天的任务已经完成",
      emptyStateCopy: "今天的任务都完成啦，记录已经存好，可以去做复盘了。",
    };
  }
  return {
    emptyStateTitle: dashboard.headline || "今天还没有可执行任务",
    emptyStateCopy: dashboard.rationale || "先定好目标岗位和今天的可用时间，岸岸才好帮你安排。",
  };
}

function presentTodayTrack(
  dashboard: TodayDashboard,
  tasks: TaskView[],
  track: "xingce" | "essay",
): TodayTrackView {
  const matching = tasks.filter((task) => task.track === track);
  const active = matching.filter(
    (task) => task.status === "ready" || task.status === "in_progress",
  );
  const completed = matching.filter((task) => task.status === "complete");
  const first = active[0];
  const allocation = dashboard.dualTrackExplanation.allocations.find(
    (item) => item.track === track,
  );
  const omittedReason = dashboard.dualTrackExplanation.omittedTrackReasons[track];
  if (first) {
    const minutes = active.reduce((total, task) => total + task.estimatedMinutes, 0);
    return {
      track,
      label: trackLabels[track],
      summary: `${String(active.length)} 项待完成 · ${formatCapacityDuration(minutes)}`,
      detail: allocation?.reason ?? first.reason,
      actionLabel: first.status === "in_progress" ? "继续" : "开始",
      taskId: first.id,
      tone: "active",
    };
  }
  if (completed.length > 0) {
    return {
      track,
      label: trackLabels[track],
      summary: `${String(completed.length)} 项已完成`,
      detail: track === "xingce" ? "今天的行测任务都完成啦。" : "今天的申论任务都完成啦。",
      actionLabel: track === "xingce" ? "查看复测" : "调整安排",
      taskId: "",
      tone: "complete",
    };
  }
  return {
    track,
    label: trackLabels[track],
    summary: dashboard.dayMode === "recovery" ? "恢复日不安排新任务" : "今天暂未安排",
    detail:
      omittedReason ??
      (track === "xingce"
        ? "可以看看错题和到期的复测。"
        : "申论还在你的计划里，调整今天的时间就能排进来。"),
    actionLabel: track === "xingce" ? "查看复测" : "调整安排",
    taskId: "",
    tone: "idle",
  };
}

function presentObjectiveClosureSummary(dashboard: TodayDashboard): string {
  if (dashboard.dueReflectionCount === null || dashboard.dueVerificationCount === null) {
    return "复盘和复测正在整理";
  }
  if (dashboard.dueReflectionCount === 0 && dashboard.dueVerificationCount === 0) {
    return "现在没有到期的复盘和复测";
  }
  return `${String(dashboard.dueReflectionCount)} 项待复盘 · ${String(dashboard.dueVerificationCount)} 项待复测`;
}

function presentGoalGap(dashboard: TodayDashboard) {
  const { goalId, currentScore, targetScore, baselineGap, readiness } = dashboard.goalGap;
  const hasCompleteGoalGap =
    readiness === "complete" &&
    currentScore !== null &&
    currentScore !== undefined &&
    targetScore !== null &&
    targetScore !== undefined &&
    baselineGap !== null &&
    baselineGap !== undefined;
  if (hasCompleteGoalGap) {
    return {
      hasCompleteGoalGap: true,
      targetScore: scoreRangeLabel(targetScore.lower, targetScore.upper),
      currentScore: scoreRangeLabel(currentScore.lower, currentScore.upper),
      scoreGap: baselineGap,
      confidence: evidenceLabel(currentScore.confidence),
      gapStateLabel: "目标分差可用",
      gapGuidance: "同岗位往年的目标和你的两科成绩来自同一口径，可以用来帮你规划。",
    };
  }

  if (!goalId) {
    return unavailableGoalGap(
      "目标分待生成",
      "把专业、学历和目标地区填好，岸岸就会用近年同类岗位帮你定目标分，学习照常。",
    );
  }
  if (!targetScore) {
    return unavailableGoalGap(
      "目标分依据不足",
      "目标参照已经建立，还差一份可对得上的行测、申论成绩。",
    );
  }
  if (readiness === "stale") {
    return unavailableGoalGap(
      "总分记录已过期",
      "这份成绩有点久了，补一下最近的行测和申论成绩，分差才算得准。",
    );
  }
  return unavailableGoalGap(
    readiness === "partial" ? "两科成绩还不完整" : "暂无可用总分记录",
    "补上同一场考试的行测和申论成绩，分差才算得准。",
  );
}

function unavailableGoalGap(gapStateLabel: string, gapGuidance: string) {
  return {
    hasCompleteGoalGap: false,
    targetScore: null,
    currentScore: null,
    scoreGap: null,
    confidence: gapStateLabel,
    gapStateLabel,
    gapGuidance,
  };
}

function presentTask(task: TodayTask): TaskView {
  const terminal = task.status === "complete" || task.status === "abandoned";
  const contextualStartUrl =
    task.track === "essay"
      ? withEssayTaskContext(task.startUrl, task.stage, task.id, task.execution?.executionVersionId)
      : task.startUrl;
  const navigation = terminal ? null : registeredTaskNavigationTarget(contextualStartUrl, task.id);
  return {
    id: task.id,
    kind: task.kind,
    track: task.track,
    title: task.title,
    reason: task.reason,
    estimatedMinutes: task.estimatedMinutes,
    completionRule: task.completionRule,
    kindLabel: kindLabels[task.kind],
    trackLabel: trackLabels[task.track],
    stageLabel: stageLabels[task.stage],
    status: task.status,
    statusLabel: statusLabels[task.status],
    elapsedMinutes: task.execution?.elapsedMinutes ?? null,
    priorityMetrics: [
      { label: "优先度", value: percent(task.priorityScore) },
      { label: "目标收益", value: percent(task.priorityBreakdown.goalGain) },
      { label: "能力收益", value: percent(task.priorityBreakdown.abilityGain) },
      { label: "验证紧迫", value: percent(task.priorityBreakdown.verificationUrgency) },
      { label: "时间适配", value: percent(task.priorityBreakdown.timeFit) },
      { label: "负荷扣减", value: percent(task.priorityBreakdown.loadPenalty) },
    ],
    evidenceLabels: task.evidenceRefs.map(evidenceReferenceLabel),
    canOpen: navigation !== null,
    navigationMode: navigation?.mode ?? null,
    navigationUrl: navigation?.url ?? null,
    disabledReason: navigation
      ? ""
      : terminal
        ? "这项任务已经完成，不用再点一遍。"
        : "任务入口没生成好，刷新一下今日安排；还不行就找岸岸，把问题编号发过来。",
  };
}

function presentSecondaryTaskQueues(tasks: TaskView[]): TaskQueueView[] {
  const queues: TaskQueueView[] = [];
  const groupedQueueIndexes = new Map<string, number>();

  for (const task of tasks) {
    const groupedTitle = groupedPracticeTitle(task);
    if (!groupedTitle) {
      queues.push(presentSingleTaskQueue(task));
      continue;
    }

    const key = `${task.track}:${task.kind}:${groupedTitle}`;
    const existingIndex = groupedQueueIndexes.get(key);
    if (existingIndex === undefined) {
      groupedQueueIndexes.set(key, queues.length);
      queues.push({
        ...task,
        title: groupedTitle,
        queueSize: 1,
        queueMetaLabel: taskQueueMetaLabel(task.trackLabel, 1, task.estimatedMinutes),
        queueActionLabel: task.status === "in_progress" ? "继续" : "开始",
      });
      continue;
    }

    const queue = queues[existingIndex];
    if (!queue) {
      throw new Error("Task queue index is inconsistent");
    }
    queue.queueSize += 1;
    queue.estimatedMinutes += task.estimatedMinutes;
    queue.queueMetaLabel = taskQueueMetaLabel(
      queue.trackLabel,
      queue.queueSize,
      queue.estimatedMinutes,
    );
  }

  return queues;
}

function presentSingleTaskQueue(task: TaskView): TaskQueueView {
  return {
    ...task,
    queueSize: 1,
    queueMetaLabel: `${task.trackLabel} · 预计 ${String(task.estimatedMinutes)} 分钟`,
    queueActionLabel: task.status === "in_progress" ? "继续" : "开始",
  };
}

function groupedPracticeTitle(task: TaskView): string | null {
  if (task.track !== "xingce" || task.kind !== "practice") return null;
  const match = task.title.match(/^(.*?)（第\s*\d+\s*组）$/u);
  return match?.[1]?.trim() || null;
}

function taskQueueMetaLabel(trackLabel: string, queueSize: number, minutes: number): string {
  return `${trackLabel} · 还剩 ${String(queueSize)} 组 · 共 ${String(minutes)} 分钟`;
}

function presentAllocation(allocation: TrackAllocation): TrackAllocationView {
  return {
    track: allocation.track,
    trackLabel: trackLabels[allocation.track],
    minutes: allocation.minutes,
    reason: allocation.reason,
    stageSummary: allocation.stages.map((stage) => stageLabels[stage]).join("、"),
  };
}

function presentOmittedReasons(
  dashboard: TodayDashboard,
  allocationTracks: ReadonlySet<string>,
): OmittedTrackReasonView[] {
  const visibleTracks =
    dashboard.dayMode === "unavailable" ? null : new Set<string>(["xingce", "essay"]);
  if (dashboard.replanReasonCodes.includes("weekly_mock_capacity_insufficient")) {
    visibleTracks?.add("mock");
  }
  return Object.entries(dashboard.dualTrackExplanation.omittedTrackReasons)
    .filter(
      ([track]) => !allocationTracks.has(track) && (!visibleTracks || visibleTracks.has(track)),
    )
    .map(([track, reason]) => ({
      track,
      trackLabel: omittedTrackLabels[track] ?? track,
      reason,
    }));
}

function planSectionTitle(dashboard: TodayDashboard): string {
  if (dashboard.dayMode === "mock") return "今日标准模考";
  if (dashboard.dayMode === "recovery") return "今日恢复任务";
  if (dashboard.dayMode === "unavailable") return "今日暂无可执行任务";
  return dashboard.dualTrackExplanation.dualTrackConstraintApplied
    ? "今日双轨任务"
    : "今日优先任务";
}

function capacitySourceLabel(dashboard: TodayDashboard): string {
  if (dashboard.capacity.sourceType === "user_override") {
    return "按你今天的调整";
  }
  return "按每周学习时间自动安排";
}

export function evidenceReferenceLabel(reference: PlanningEvidenceRef): string {
  const role = reference.role?.trim();
  if (role) return roleLabels[role] ?? role;
  const owner = ownerLabels[reference.owner] ?? reference.owner;
  const entityType = reference.entityType ?? "evidence";
  const entity = entityLabels[entityType] ?? entityType;
  return `${owner} · ${entity}`;
}

function percent(value: number): string {
  return `${String(Math.round(value * 100))}%`;
}
