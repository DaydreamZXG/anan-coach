import type { EssayTaskPageRuntime } from "../page-runtime";

interface ElapsedLabelPage {
  setData(data: { elapsedLabel: string }): void;
}

export function startTimer(
  page: ElapsedLabelPage,
  runtime: EssayTaskPageRuntime,
  isOwned: () => boolean,
): void {
  stopTimer(runtime);
  runtime.activeSince = Date.now();
  const update = () => {
    if (!isOwned()) return;
    const elapsedSeconds = currentElapsedSeconds(runtime);
    const hours = Math.floor(elapsedSeconds / 3600);
    const minutes = Math.floor((elapsedSeconds % 3600) / 60);
    const seconds = elapsedSeconds % 60;
    page.setData({
      elapsedLabel: `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`,
    });
  };
  update();
  runtime.timerHandle = setInterval(update, 1000);
}

export function stopTimer(runtime: EssayTaskPageRuntime): void {
  if (runtime.timerHandle !== undefined) clearInterval(runtime.timerHandle);
  runtime.timerHandle = undefined;
  if (runtime.activeSince > 0) {
    runtime.activeElapsedMs += Math.max(0, Date.now() - runtime.activeSince);
    runtime.activeSince = 0;
  }
}

export function currentElapsedSeconds(runtime: EssayTaskPageRuntime): number {
  const runningMs = runtime.activeSince > 0 ? Math.max(0, Date.now() - runtime.activeSince) : 0;
  return Math.max(0, Math.floor((runtime.activeElapsedMs + runningMs) / 1_000));
}
