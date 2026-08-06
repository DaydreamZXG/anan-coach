import type {
  ExitPolicy,
  SyncState,
  TaskRuntimeState,
} from "../../../../../task-runtime/contracts";

export interface AutosaveDecision {
  shouldSave: boolean;
  reason: "no_changes" | "in_flight" | "not_answering" | "interval" | "leaving" | "blocked_exit";
}

export interface ExitDecision {
  action: "leave" | "confirm" | "save_then_leave" | "blocked";
  policy: ExitPolicy;
  syncState: SyncState;
  title: string;
  message: string;
}

export function decideAutosave(
  state: TaskRuntimeState,
  options: {
    saveInFlight: boolean;
    lastSavedElapsedSeconds: number;
    intervalSeconds: number;
    leaving?: boolean | undefined;
  },
): AutosaveDecision {
  if (options.saveInFlight || state.syncState === "saving") {
    return { shouldSave: false, reason: "in_flight" };
  }
  if (state.syncState !== "pending") return { shouldSave: false, reason: "no_changes" };
  if (state.exitPolicy === "blocked") return { shouldSave: true, reason: "blocked_exit" };
  if (options.leaving) return { shouldSave: true, reason: "leaving" };
  if (state.phase !== "answering") return { shouldSave: false, reason: "not_answering" };
  if (state.totalElapsedSeconds - options.lastSavedElapsedSeconds >= options.intervalSeconds) {
    return { shouldSave: true, reason: "interval" };
  }
  return { shouldSave: false, reason: "no_changes" };
}

export function decideExit(state: TaskRuntimeState | null | undefined): ExitDecision {
  if (!state) {
    return {
      action: "leave",
      policy: "immediate",
      syncState: "saved",
      title: "退出任务",
      message: "当前没有进行中的任务。",
    };
  }
  if (state.exitPolicy === "blocked") {
    return {
      action: "blocked",
      policy: state.exitPolicy,
      syncState: state.syncState,
      title: "复盘尚未完成",
      message: "结果已经记录，但完成原话复盘和必要错因确认前不会结算今日任务。",
    };
  }
  if (
    state.syncState === "pending" ||
    state.syncState === "error" ||
    state.syncState === "conflict"
  ) {
    return {
      action: "save_then_leave",
      policy: state.exitPolicy,
      syncState: state.syncState,
      title: "保存进度后退出",
      message: "当前有未确认的作答进度，必须保存成功后才能离开。",
    };
  }
  if (state.exitPolicy === "timer_continues") {
    return {
      action: "confirm",
      policy: state.exitPolicy,
      syncState: state.syncState,
      title: "模考计时仍会继续",
      message: "离开页面不会暂停模考计时，回来后会按统一时间恢复。",
    };
  }
  if (state.exitPolicy === "confirm") {
    return {
      action: "confirm",
      policy: state.exitPolicy,
      syncState: state.syncState,
      title: "退出任务",
      message: "已保存的进度可以稍后继续。",
    };
  }
  return {
    action: "leave",
    policy: state.exitPolicy,
    syncState: state.syncState,
    title: "退出任务",
    message: "任务已完成。",
  };
}
