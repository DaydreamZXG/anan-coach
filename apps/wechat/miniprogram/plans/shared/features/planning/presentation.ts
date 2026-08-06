const reasonLabels: Record<string, string> = {
  weekly_rhythm_initialized: "首次生成本周安排",
  weekly_preference_changed: "每周可用时间发生变化",
  strategy_decision_changed: "学习取舍发生变化",
  daily_plan_changed: "本周的每日任务已经更新",
  task_execution_changed: "本周新增了任务完成记录",
  weekly_inputs_reconciled: "已同步最新学习记录",
  no_schedulable_capacity: "今天没有可安排的学习时间",
  mandatory_calibration_first: "优先完成必要的能力校准",
  mandatory_task_capacity_insufficient: "今天的时间不足以完成必要任务",
  recovery_day_no_new_learning: "恢复日只安排复盘和复测",
  evidence_weighted_priority: "按目标收益和当前证据排序",
  xingce_essay_dual_track: "行测与申论都保留了训练",
  rolling_track_balance: "补回近期训练偏少的科目",
  daily_capacity_changed: "今天的可用时间发生变化",
  task_execution_recorded: "新增了任务完成记录",
  weekly_preference_pending_confirmation: "每周学习时间还未确认",
  daily_capacity_pending_confirmation: "今天的可用时间还未确认",
  capacity_recovery_mode: "根据今天的状态切换为恢复安排",
  weekly_mock_capacity_insufficient: "今天的时间不足以完成整卷模考",
  strategy_preserved_required_closure: "保留了必须完成的复盘闭环",
  strategy_abandon_applied: "已按你确认的取舍移除对应训练",
  strategy_phase_deprioritize_applied: "已按阶段取舍降低对应训练优先级",
  strategy_exam_skip_active: "已按考试范围跳过当前不考内容",
};

const inputLabels: Record<string, string> = {
  profile: "个人与报考资料",
  weeklyPreference: "每周学习时间",
  dailyCapacity: "今日可用时间和状态",
  positionGoal: "主目标岗位",
  abilityEstimate: "当前能力判断",
  objectiveClosure: "行测练习与复盘",
  essayClosure: "申论练习与复盘",
  essayTask: "申论任务",
  essayMaterialBundle: "申论材料",
  essayRubric: "申论评分依据",
  taskExecutionState: "任务完成记录",
  strategyDecisions: "学习取舍",
};

const dayModeLabels: Record<string, string> = {
  normal: "日常训练",
  mock: "整卷模考",
  recovery: "恢复复盘",
  unavailable: "未安排任务",
};

export function planningReasonLabel(codes: readonly string[], emptyLabel: string): string {
  const labels = codes
    .map((code) => reasonLabels[code])
    .filter((value): value is string => !!value);
  return [...new Set(labels)].join("、") || emptyLabel;
}

export function changedInputLabel(keys: readonly string[]): string {
  const labels = keys.map((key) => {
    if (key.startsWith("dailyPlan:")) return "每日任务";
    if (key.startsWith("execution:")) return "任务完成记录";
    return inputLabels[key];
  });
  return [...new Set(labels.filter((value): value is string => !!value))].join("、") || "首次建立";
}

export function planningDayModeLabel(value: string): string {
  return dayModeLabels[value] ?? "学习安排";
}

export function weeklyRhythmLabel(value: string): string {
  return value === "5+1+1" ? "5 天训练 · 1 天模考 · 1 天恢复" : "本周学习节奏";
}

export function durationLabel(minutes: number): string {
  if (minutes < 60) return `${String(minutes)} 分钟`;
  const hours = minutes / 60;
  return `${Number.isInteger(hours) ? String(hours) : hours.toFixed(1)} 小时`;
}
