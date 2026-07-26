import { describe, expect, it } from "vitest";
import {
  onboardingResultActionTarget,
  positionBriefingUrl,
  positionCompareUrl,
  positionDetailUrl,
  positionGoalUrl,
  positionSearchUrl,
  registeredNavigationTarget,
} from "../miniprogram/app/navigation/routes";

describe("position deep links", () => {
  it("routes each onboarding result and degrades a missing briefing target to the position list", () => {
    expect(onboardingResultActionTarget("enter_workspace")).toEqual({
      mode: "switchTab",
      url: "/pages/today/index",
    });
    expect(onboardingResultActionTarget("import_prior_evidence")).toEqual({
      mode: "navigateTo",
      url: "/pages/external-report/index",
    });
    expect(onboardingResultActionTarget("start_retake_recovery")).toEqual({
      mode: "navigateTo",
      url: "/pages/retake/index",
    });
    expect(onboardingResultActionTarget("review_exam_briefing", "position/1")).toEqual({
      mode: "navigateTo",
      url: "/pages/positions/briefing/index?positionId=position%2F1",
    });
    expect(onboardingResultActionTarget("review_exam_briefing")).toEqual({
      mode: "navigateTo",
      url: "/pages/positions/index",
    });
    expect(onboardingResultActionTarget("review_exam_briefing", "   ")).toEqual({
      mode: "navigateTo",
      url: "/pages/positions/index",
    });
  });

  it("builds registered detail and goal routes with encoded identifiers", () => {
    expect(positionDetailUrl("position/1")).toBe(
      "/pages/positions/detail/index?positionId=position%2F1",
    );
    expect(positionGoalUrl("position/1")).toBe(
      "/pages/positions/goal/index?positionId=position%2F1",
    );
    expect(positionBriefingUrl("position/1")).toBe(
      "/pages/positions/briefing/index?positionId=position%2F1",
    );
    expect(registeredNavigationTarget(positionDetailUrl("position/1"))).toEqual({
      mode: "navigateTo",
      url: "/pages/positions/detail/index?positionId=position%2F1",
    });
    expect(registeredNavigationTarget(positionBriefingUrl("position/1"))).toEqual({
      mode: "navigateTo",
      url: "/pages/positions/briefing/index?positionId=position%2F1",
    });
  });

  it("preserves explicit search filters and 2–3 comparison IDs", () => {
    expect(positionSearchUrl({ query: "税务 岗", region: "江西" })).toBe(
      "/pages/positions/search/index?q=%E7%A8%8E%E5%8A%A1%20%E5%B2%97&region=%E6%B1%9F%E8%A5%BF",
    );
    expect(positionCompareUrl(["position-1", "position-2"])).toBe(
      "/pages/positions/compare/index?ids=position-1%2Cposition-2",
    );
    expect(positionCompareUrl([" position-1 ", "position-2", " position-3"])).toBe(
      "/pages/positions/compare/index?ids=position-1%2Cposition-2%2Cposition-3",
    );
    expect(() => positionCompareUrl(["position-1"])).toThrow("two or three");
    expect(() => positionCompareUrl(["position-1", "position-1"])).toThrow("two or three");
    expect(() => positionCompareUrl(["position-1", "position-1", "position-2"])).toThrow(
      "two or three",
    );
    expect(() => positionCompareUrl([" position-1 ", "position-1"])).toThrow("two or three");
    expect(() => positionCompareUrl(["position-1", " "])).toThrow("two or three");
    expect(() =>
      positionCompareUrl(["position-1", "position-2", "position-3", "position-4"]),
    ).toThrow("two or three");
  });
});
