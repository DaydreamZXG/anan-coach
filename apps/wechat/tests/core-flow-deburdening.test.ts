import { describe, expect, it, vi } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import todayTemplateRaw from "../miniprogram/pages/today/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import todayStylesRaw from "../miniprogram/pages/today/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import externalReportSourceRaw from "../miniprogram/reports/external-report/index.ts?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import answerIngestionSourceRaw from "../miniprogram/xingce/answer-ingestion/index.ts?raw";
import { routes } from "../miniprogram/app/navigation/routes";
import { silentEntryNavigationTarget } from "../miniprogram/features/identity/silent-entry";
import {
  createTodayHarness,
  incompleteProgress,
  latestState,
} from "./work_packages/vj-01-real-today-r2/harness";

const todayTemplate = rawAsset(todayTemplateRaw);
const todayStyles = rawAsset(todayStylesRaw);
const externalReportSource = rawAsset(externalReportSourceRaw);
const answerIngestionSource = rawAsset(answerIngestionSourceRaw);

function rawAsset(source: unknown): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source;
}

describe("核心学习流去阻塞", () => {
  it.each([
    ["preparation_experience", routes.onboardingEntry],
    ["qualification", routes.onboarding],
    ["study_preference", routes.onboardingPositionPreference],
    ["result", routes.onboardingAbilityBaseline],
  ] as const)("%s 状态静默认证后继续新用户收集流程", (nextStep, url) => {
    expect(silentEntryNavigationTarget(nextStep, null)).toEqual({ mode: "navigateTo", url });
  });

  it("已完成建档的老用户静默认证后直接进入今日", () => {
    expect(silentEntryNavigationTarget("workspace", null)).toEqual({
      mode: "switchTab",
      url: routes.today,
    });
  });

  it("今日同步资料状态但不把它当门禁，并继续读取真实任务", async () => {
    const harness = createTodayHarness({
      getProgress: vi.fn(() => Promise.resolve(incompleteProgress("qualification"))),
    });

    await harness.controller.load();

    expect(harness.spies.getProgress).toHaveBeenCalledWith("server-token-a");
    expect(harness.spies.navigateOnboarding).not.toHaveBeenCalled();
    expect(harness.spies.getToday).toHaveBeenCalledWith("server-token-a");
    expect(latestState(harness.states)).toMatchObject({ status: "ready" });
  });

  it("资料进度接口暂时不兼容时也不会挡住真实任务", async () => {
    const harness = createTodayHarness({
      getProgress: vi.fn(() => Promise.reject(new Error("legacy progress mismatch"))),
    });

    await harness.controller.load();

    expect(harness.spies.getToday).toHaveBeenCalledWith("server-token-a");
    expect(harness.spies.navigateOnboarding).not.toHaveBeenCalled();
    expect(latestState(harness.states)).toMatchObject({ status: "ready" });
  });

  it("把真实任务和黑色主按钮放在首要位置，认识方式保持可选", () => {
    expect(todayStyles).toContain(".today-col--main {\n  order: 1;");
    expect(todayStyles).not.toContain(".today-col--side-a");
    expect(todayStyles).toContain("@media (min-width: 600px)");
    expect(todayTemplate).toContain('class="primary-button');
    expect(todayStyles).toContain("background: var(--ink)");
    expect(todayTemplate.indexOf('class="primary-task__body"')).toBeLessThan(
      todayTemplate.indexOf('class="anan-insight"'),
    );
    expect(todayTemplate).toContain('bindtap="openRecognitionOptions"');
    expect(todayTemplate).toContain("可选 · 让岸岸更快认识你");
    expect(todayTemplate).not.toContain('class="opening-start"');
    expect(todayTemplate).not.toContain('class="opening-card"');
    expect(todayStyles).not.toContain(".opening-start");
  });

  it("成绩单与错题导入完成后都无额外资格门禁地回到今日", () => {
    for (const source of [externalReportSource, answerIngestionSource]) {
      expect(source).toContain("continueCoachMeeting");
      expect(source).toContain("wx.switchTab({ url: routes.today })");
    }
  });
});
