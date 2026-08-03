import type {
  AppDomainsPlanningSchemasTodayTask as TodayTask,
  PlanningEvidenceRef,
  TodayDashboard,
  TrackAllocation,
} from "../../api/generated/types.gen";
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
  completedTasks: TaskView[];
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

export function presentTodayDashboard(dashboard: TodayDashboard): TodayView {
  const gap = presentGoalGap(dashboard);
  const tasks = orderedTasks(dashboard.tasks).map(presentTask);
  const activeTasks = tasks.filter(
    (task) => task.status === "ready" || task.status === "in_progress",
  );
  const completedTasks = tasks.filter((task) => task.status === "complete");
  const hasExecutionActivity = tasks.some(
    (task) => task.status === "in_progress" || task.status === "complete" || task.elapsedMinutes,
  );
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
    capacityStatusLabel: dashboard.capacity.confirmed ? "今日时间已确认" : "确认今日时间",
    capacitySourceLabel: capacitySourceLabel(dashboard),
    planSectionTitle: planSectionTitle(dashboard),
    planModeLabel: modeLabels[dashboard.dualTrackExplanation.mode],
    planModeSummary: dashboard.dualTrackExplanation.summary,
    trackAllocations: dashboard.dualTrackExplanation.allocations.map(presentAllocation),
    omittedTrackReasons: presentOmittedReasons(dashboard, allocationTracks),
    tasks,
    activeTasks,
    completedTasks,
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
          "今天不安排学习任务，也不会把未完成内容累积成明日欠账。想恢复学习时，可以随时调整今天。",
      };
    }
    return {
      emptyStateTitle: "今天没有必做任务",
      emptyStateCopy: `${dashboard.rationale} 今天有 ${formatCapacityDuration(dashboard.capacity.availableMinutes)} 可用，不代表必须排满。`,
    };
  }
  if (dayState === "complete") {
    return {
      emptyStateTitle: "今天的任务已经完成",
      emptyStateCopy: "学习记录已经保存，可以结束今天并完成复盘。",
    };
  }
  return {
    emptyStateTitle: dashboard.headline || "今天还没有可执行任务",
    emptyStateCopy: dashboard.rationale || "先确认目标岗位和今天可用时间，岸岸才能生成真实安排。",
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
      detail: track === "xingce" ? "今天的行测记录已归档。" : "今天的申论记录已归档。",
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
        ? "可查看已有错题与到期复测。"
        : "申论仍保留在双轨计划中，可调整今天重新核对。"),
    actionLabel: track === "xingce" ? "查看复测" : "调整安排",
    taskId: "",
    tone: "idle",
  };
}

function presentObjectiveClosureSummary(dashboard: TodayDashboard): string {
  if (dashboard.dueReflectionCount === null || dashboard.dueVerificationCount === null) {
    return "复盘与间隔验证正在整理";
  }
  if (dashboard.dueReflectionCount === 0 && dashboard.dueVerificationCount === 0) {
    return "目前没有到期复盘或验证";
  }
  return `${String(dashboard.dueReflectionCount)} 项待复盘 · ${String(dashboard.dueVerificationCount)} 项待验证`;
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
      gapGuidance: "历史同类岗位目标与当前两科成绩来自可比口径，可以用于安排学习。",
    };
  }

  if (!goalId) {
    return unavailableGoalGap(
      "目标分待生成",
      "完善专业、学历和目标地区后，岸岸会用最近完整年度的同类岗位生成目标分；学习任务不受阻断。",
    );
  }
  if (!targetScore) {
    return unavailableGoalGap("目标分依据不足", "目标参照已建立，但还缺少可比较的双科分数记录。");
  }
  if (readiness === "stale") {
    return unavailableGoalGap(
      "总分记录已过期",
      "过期成绩不继续计算岗位分差，请补充近期行测和申论成绩。",
    );
  }
  return unavailableGoalGap(
    readiness === "partial" ? "两科成绩还不完整" : "暂无可用总分记录",
    "需要补齐同一考试口径的行测和申论成绩，才能计算可靠分差。",
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
        ? "这项任务已经结束，不需要重复开始。"
        : "任务入口生成失败，请刷新今日安排；若仍未恢复，请联系岸岸并提供问题编号。",
  };
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
    return dashboard.capacity.confirmed ? "你今天确认的时间" : "你今天设置的时间，还待确认";
  }
  return dashboard.capacity.confirmed ? "按你的每周学习时间" : "按每周学习时间预排，今天还待确认";
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
