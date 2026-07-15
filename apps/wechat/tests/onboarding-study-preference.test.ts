import { describe, expect, it } from "vitest";
import {
  adjustStudyMinutes,
  buildStudyPreferenceCommand,
  defaultStudyPreferenceDraft,
  selectSpecialDay,
  validateStudyPreferenceDraft,
  weeklyRhythmSummary,
} from "../miniprogram/features/onboarding/study-preference";

describe("onboarding study preference", () => {
  it("builds the frozen 5+1+1 weekly rhythm without inventing a temporary override", () => {
    const command = buildStudyPreferenceCommand(defaultStudyPreferenceDraft, {
      expectedPreferenceVersion: 0,
      expectedProgressVersion: 2,
      effectiveFrom: "2026-07-13",
    });

    expect(command).toEqual({
      expectedPreferenceVersion: 0,
      expectedProgressVersion: 2,
      timezone: "Asia/Shanghai",
      effectiveFrom: "2026-07-13",
      weekdayMinutes: 45,
      weekendMinutes: 120,
      longBlockMinutes: 120,
      fragmentMinutes: 25,
      fixedRestDay: "sunday",
      mockExamDay: "saturday",
    });
    expect(weeklyRhythmSummary(defaultStudyPreferenceDraft)).toEqual({
      focusedTrainingDays: 5,
      mockExamDays: 1,
      recoveryDays: 1,
      weeklyMinutes: 345,
    });
  });

  it("clamps minute adjustments to the server contract", () => {
    expect(
      adjustStudyMinutes(defaultStudyPreferenceDraft, "weekdayMinutes", -100).weekdayMinutes,
    ).toBe(0);
    expect(
      adjustStudyMinutes(defaultStudyPreferenceDraft, "weekendMinutes", 800).weekendMinutes,
    ).toBe(720);
  });

  it("rejects a week with no learning time or overlapping rest and mock days", () => {
    expect(
      validateStudyPreferenceDraft({
        ...defaultStudyPreferenceDraft,
        weekdayMinutes: 0,
        weekendMinutes: 0,
      }),
    ).toContain("至少保留");
    expect(
      validateStudyPreferenceDraft(
        selectSpecialDay(defaultStudyPreferenceDraft, "mockExamDay", "sunday"),
      ),
    ).toContain("不能是同一天");
  });

  it("fails closed on stale or malformed command context", () => {
    expect(() =>
      buildStudyPreferenceCommand(defaultStudyPreferenceDraft, {
        expectedPreferenceVersion: -1,
        expectedProgressVersion: 2,
        effectiveFrom: "2026-07-13",
      }),
    ).toThrow("expected preference version");
    expect(() =>
      buildStudyPreferenceCommand(defaultStudyPreferenceDraft, {
        expectedPreferenceVersion: 0,
        expectedProgressVersion: 0,
        effectiveFrom: "2026-07-13",
      }),
    ).toThrow("progress version");
  });
});
