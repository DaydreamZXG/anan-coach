import type { AnnotationStroke, AttemptActionEvent } from "../../../task-runtime/contracts";
import { splitAnnotationSurfaces } from "../shared/task-runtime/annotation-layer/strokes";

export type ReplaySurface = "question" | "material";

export interface ReplaySource {
  elapsedSeconds: number;
  annotations: AnnotationStroke[];
  actionEvents: AttemptActionEvent[];
  materialAvailable: boolean;
}

export interface TrajectoryReplayState {
  durationMs: number;
  replayMs: number;
  playing: boolean;
  surface: ReplaySurface;
  surfaceOptions: Array<{ value: ReplaySurface; label: string }>;
  annotations: AnnotationStroke[];
  markers: Array<{ percent: number; tone: "default" | "warning" }>;
  summaryLabel: string;
}

export function createTrajectoryReplayState(source: ReplaySource): TrajectoryReplayState {
  const surfaces = splitAnnotationSurfaces(source.annotations);
  const durationMs = Math.max(
    source.elapsedSeconds * 1000,
    ...source.actionEvents.map((entry) => entry.atMs),
    ...source.annotations.map((stroke) => stroke.atMs),
    ...source.annotations.map((stroke) => stroke.erasedAtMs ?? 0),
    1000,
  );
  const surfaceOptions = [
    ...(surfaces.question.length > 0 || surfaces.material.length === 0
      ? [{ value: "question" as const, label: "题目" }]
      : []),
    ...(source.materialAvailable && surfaces.material.length > 0
      ? [{ value: "material" as const, label: "材料" }]
      : []),
  ];
  const surface = surfaceOptions[0]?.value ?? "question";
  const eraseCount = source.actionEvents.filter(
    (entry) => entry.type === "annotation_erased",
  ).length;
  const changeCount = source.actionEvents.filter((entry) => entry.type === "option_changed").length;
  const summaryParts = [
    eraseCount > 0 ? `${String(eraseCount)} 次擦除` : "",
    changeCount > 0 ? `${String(changeCount)} 次回改` : "",
  ].filter(Boolean);
  return {
    durationMs,
    replayMs: 0,
    playing: false,
    surface,
    surfaceOptions,
    annotations: surface === "material" ? surfaces.material : surfaces.question,
    markers: source.actionEvents.map((entry) => ({
      percent: Math.min(100, Math.round((entry.atMs / durationMs) * 100)),
      tone:
        entry.type === "option_changed" || entry.type === "annotation_erased"
          ? "warning"
          : "default",
    })),
    summaryLabel: summaryParts.join(" · ") || `${String(source.actionEvents.length)} 个动作`,
  };
}

export function seekTrajectoryReplay(
  state: TrajectoryReplayState,
  milliseconds: number,
): TrajectoryReplayState {
  const replayMs = Math.min(
    state.durationMs,
    Math.max(0, Number.isFinite(milliseconds) ? milliseconds : 0),
  );
  return { ...state, replayMs, playing: false };
}

export function advanceTrajectoryReplay(
  state: TrajectoryReplayState,
  milliseconds: number,
): TrajectoryReplayState {
  const replayMs = Math.min(state.durationMs, state.replayMs + Math.max(0, milliseconds));
  return { ...state, replayMs, playing: replayMs < state.durationMs };
}

export function selectTrajectoryReplaySurface(
  state: TrajectoryReplayState,
  source: ReplaySource,
  surface: ReplaySurface,
): TrajectoryReplayState {
  if (!state.surfaceOptions.some((option) => option.value === surface)) return state;
  const surfaces = splitAnnotationSurfaces(source.annotations);
  return {
    ...state,
    surface,
    annotations: surface === "material" ? surfaces.material : surfaces.question,
  };
}

export class ReplayClock {
  private readonly timers = new WeakMap<object, ReturnType<typeof setInterval>>();

  start(owner: object, tick: () => boolean, intervalMs: number): void {
    this.stop(owner);
    const timer = setInterval(() => {
      if (!tick()) this.stop(owner);
    }, intervalMs);
    this.timers.set(owner, timer);
  }

  stop(owner: object): void {
    const timer = this.timers.get(owner);
    if (timer === undefined) return;
    clearInterval(timer);
    this.timers.delete(owner);
  }

  active(owner: object): boolean {
    return this.timers.has(owner);
  }
}
