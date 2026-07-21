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
  hasTasks: boolean;
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
  capacity_limited: "容量受限",
  mock_day: "模考日",
  recovery_day: "恢复日",
  evidence_missing: "证据缺口",
};

const ownerLabels: Record<string, string> = {
  planning: "规划 Agent",
  planning_agent: "规划 Agent",
  position_goal: "岗位 Agent",
  ability_estimation: "能力估计",
  "ability-estimation-agent": "能力估计",
  objective_learning: "行测学习大脑",
  essay_intelligence: "申论学习大脑",
  planning_execution: "任务执行",
  "learning-brain": "学习大脑",
  "reflection-agent": "复盘 Agent",
};

const entityLabels: Record<string, string> = {
  ability_estimate: "能力估计版本",
  ability_estimate_version: "能力估计版本",
  daily_capacity: "当日时间容量",
  essay_attempt: "申论作答",
  essay_material_bundle_version: "申论材料包版本",
  essay_review_version: "申论评阅版本",
  essay_rubric_version: "申论评分规程版本",
  essay_rewrite_version: "申论改写版本",
  essay_task_version: "申论任务版本",
  essay_transfer_verification: "申论迁移验证",
  evidence_collection_need: "待补学习证据",
  error_pattern: "错因模式",
  objective_state: "行测学习状态",
  position_goal_version: "主目标岗位版本",
  reflection: "复盘记录",
  score_evidence: "成绩证据",
  verification_schedule: "间隔验证日程",
  weekly_study_preference: "每周学习设置",
  task_execution_version: "任务执行版本",
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
  const allocationTracks = new Set(
    dashboard.dualTrackExplanation.allocations.map((item) => item.track),
  );

  return {
    headline: dashboard.headline,
    rationale: dashboard.rationale,
    positionName: dashboard.goalGap.positionName ?? "尚未确认主目标岗位",
    ...gap,
    evidenceSummary: dashboard.goalGap.evidenceSummary,
    capacityVersionId: dashboard.capacity.capacityVersionId,
    availableMinutes: dashboard.capacity.availableMinutes,
    availableDurationLabel: formatCapacityDuration(dashboard.capacity.availableMinutes),
    energy: dashboard.capacity.energy,
    stress: dashboard.capacity.stress,
    confidenceLevel: dashboard.capacity.confidenceLevel,
    capacityStatusLabel: dashboard.capacity.confirmed ? "已确认容量版本" : "待确认容量版本",
    capacitySourceLabel: capacitySourceLabel(dashboard),
    planSectionTitle: planSectionTitle(dashboard),
    planModeLabel: modeLabels[dashboard.dualTrackExplanation.mode],
    planModeSummary: dashboard.dualTrackExplanation.summary,
    trackAllocations: dashboard.dualTrackExplanation.allocations.map(presentAllocation),
    omittedTrackReasons: presentOmittedReasons(dashboard, allocationTracks),
    tasks,
    hasTasks: tasks.length > 0,
    dueSummary: presentObjectiveClosureSummary(dashboard),
    isRecoveryDay: dashboard.dayMode === "recovery",
  };
}

function presentObjectiveClosureSummary(dashboard: TodayDashboard): string {
  if (dashboard.dueReflectionCount === null || dashboard.dueVerificationCount === null) {
    return "行测复盘与间隔验证：闭环能力待发布";
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
      gapStateLabel: "岗位分差可用",
      gapGuidance: "当前岗位线与两科成绩证据可在同一考试版本下比较。",
    };
  }

  if (!goalId) {
    return unavailableGoalGap(
      "尚未确认主目标岗位",
      "先确认一个主目标岗位，规划 Agent 才能读取对应的备考线。",
    );
  }
  if (!targetScore) {
    return unavailableGoalGap(
      "岗位备考线证据不足",
      "主目标已确认，但该岗位的可比较分数证据尚未通过门禁。",
    );
  }
  if (readiness === "stale") {
    return unavailableGoalGap(
      "总分证据已过期",
      "过期成绩不继续计算岗位分差，请补充近期行测和申论成绩证据。",
    );
  }
  return unavailableGoalGap(
    readiness === "partial" ? "两科成绩证据未完整" : "暂无可用总分证据",
    "需要同一考试版本的行测、申论成绩；补齐前不会用单科分冒充总分。",
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
      : task.track === "xingce"
        ? withXingcePlanTaskContext(task.startUrl, task.stage, task.id)
        : task.startUrl;
  const navigation = terminal ? null : registeredTaskNavigationTarget(contextualStartUrl);
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
        ? "该工作项已终止，不能重复开始。"
        : "该页面尚未接入真实学习证据与任务执行写入，暂不开放。",
  };
}

function withXingcePlanTaskContext(startUrl: string, stage: TaskStage, planTaskId: string): string {
  if (
    stage !== "objective_baseline" &&
    stage !== "objective_practice" &&
    stage !== "objective_verification"
  ) {
    return startUrl;
  }
  const separator = startUrl.includes("?") ? "&" : "?";
  return `${startUrl}${separator}planTaskId=${encodeURIComponent(planTaskId)}`;
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
    return dashboard.capacity.confirmed
      ? "来自你对今日容量的确认"
      : "来自今日容量设置，当前版本尚未确认";
  }
  return dashboard.capacity.confirmed
    ? "来自已确认的每周学习设置"
    : "由每周学习设置推导，今日尚未单独确认";
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
