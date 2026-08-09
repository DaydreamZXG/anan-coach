import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DailySettlementView } from "../miniprogram/api/generated/types.gen";

describe("daily settlement closing ritual", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("shows one honest closing moment before returning to Today", async () => {
    const switchTab = vi.fn();
    const vibrateShort = vi.fn();
    const page = await loadPage({ switchTab, vibrateShort });
    page.data.settlement = settlement({ completedTaskCount: 3, elapsedMinutes: 75 });

    page.returnToday.call(page);

    expect(page.data).toMatchObject({
      closing: true,
      closingHasActivity: true,
      closingDate: "08.09",
      closingTitle: "今天，正式收卷",
      closingMeta: "3 项完成 · 1.3 小时",
    });
    expect(String(page.data.closingCopy)).toContain("这些真实投入会带到明天");
    expect(vibrateShort).toHaveBeenCalledWith(expect.objectContaining({ type: "light" }));
    expect(switchTab).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1300);

    expect(switchTab).toHaveBeenCalledWith(expect.objectContaining({ url: "/pages/today/index" }));
  });

  it("does not disguise a zero-activity day as an achievement", async () => {
    const page = await loadPage({ switchTab: vi.fn(), vibrateShort: vi.fn() });
    page.data.settlement = settlement({ completedTaskCount: 0, elapsedMinutes: 0 });

    page.returnToday.call(page);

    expect(page.data).toMatchObject({
      closingHasActivity: false,
      closingTitle: "今天，也先收卷",
      closingCopy: "没有开始也如实记下。明天从第一项重新落笔。",
      closingMeta: "0 项完成 · 0 分钟",
    });
  });

  it("cancels delayed navigation when the page is hidden", async () => {
    const switchTab = vi.fn();
    const page = await loadPage({ switchTab, vibrateShort: vi.fn() });
    page.data.settlement = settlement({ completedTaskCount: 1, elapsedMinutes: 15 });

    page.returnToday.call(page);
    page.onHide.call(page);
    vi.runAllTimers();

    expect(page.data.closing).toBe(false);
    expect(switchTab).not.toHaveBeenCalled();
  });
});

interface CapturedPage {
  data: Record<string, unknown> & { settlement: DailySettlementView | null };
  onHide(this: TestPage): void;
  returnToday(this: TestPage): void;
}

interface TestPage extends CapturedPage {
  setData(patch: Record<string, unknown>): void;
}

async function loadPage(wxStub: Record<string, unknown>): Promise<TestPage> {
  vi.resetModules();
  let captured: CapturedPage | undefined;
  vi.stubGlobal("Page", (definition: CapturedPage) => {
    captured = definition;
  });
  vi.stubGlobal("wx", wxStub);
  await import("../miniprogram/plans/daily-settlement/index");
  if (!captured) throw new Error("Daily settlement page definition was not registered");
  const data = JSON.parse(JSON.stringify(captured.data)) as TestPage["data"];
  return {
    ...captured,
    data,
    setData(patch: Record<string, unknown>) {
      Object.assign(data, patch);
    },
  };
}

function settlement(overrides: {
  completedTaskCount: number;
  elapsedMinutes: number;
}): DailySettlementView {
  return {
    abandonedTaskCount: 0,
    contractVersion: "daily-settlement@v1",
    generatedAt: "2026-08-09T10:00:00Z",
    localDate: "2026-08-09",
    openTaskCount: 0,
    planVersionId: "plan-1",
    scheduledMinutes: 90,
    status: overrides.elapsedMinutes > 0 ? "partial" : "not_started",
    completedTaskCount: overrides.completedTaskCount,
    elapsedMinutes: overrides.elapsedMinutes,
    summary: "",
    newRisks: [],
    tasks: [],
  };
}
