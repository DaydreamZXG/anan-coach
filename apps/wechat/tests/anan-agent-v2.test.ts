import { afterEach, describe, expect, it, vi } from "vitest";
import { StudyPreferenceController } from "../miniprogram/features/onboarding/study-preference-controller";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("岸岸 Agent 周节奏预填", () => {
  it("只解析来自岸岸的白名单星期参数", async () => {
    vi.stubGlobal("Page", vi.fn());
    const { parseAgentWeeklyPrefill } = await import("../miniprogram/pages/study-preference/index");

    expect(
      parseAgentWeeklyPrefill({
        source: "anan",
        mockExamDay: "saturday",
        fixedRestDay: "sunday",
      }),
    ).toEqual({ mockExamDay: "saturday", fixedRestDay: "sunday" });
    expect(parseAgentWeeklyPrefill({ source: "other", mockExamDay: "saturday" })).toBeNull();
    expect(parseAgentWeeklyPrefill({ source: "anan", mockExamDay: "holiday" })).toBeNull();
    expect(
      parseAgentWeeklyPrefill({
        source: "anan",
        mockExamDay: "saturday",
        fixedRestDay: "saturday",
      }),
    ).toBeNull();
  });

  it("读取权威偏好后应用预填，但不自动保存", async () => {
    vi.stubGlobal("wx", {
      getStorageSync: vi.fn(() => ""),
      setStorageSync: vi.fn(),
      removeStorageSync: vi.fn(),
    });
    const putStudyPreference = vi.fn();
    let latest: ReturnType<StudyPreferenceController["snapshot"]> | undefined;
    const controller = new StudyPreferenceController({
      userId: "user-anan-v2",
      mode: "settings",
      api: {
        getProgress: vi.fn().mockResolvedValue({
          data: {
            nextStep: "workspace",
            progressVersion: 3,
            studyPreferenceReady: true,
            studyPreferenceVersion: 2,
          },
        }),
        getStudyPreference: vi.fn().mockResolvedValue({
          data: {
            preferenceVersion: 2,
            weekdayMinutes: 180,
            weekendMinutes: 360,
            fixedRestDay: "saturday",
            mockExamDay: "sunday",
          },
        }),
        putStudyPreference,
      },
      initialDraftPatch: { fixedRestDay: "sunday", mockExamDay: "saturday" },
      onChange: (state) => {
        latest = state;
      },
      onAdvance: vi.fn(),
    });

    await controller.load();

    expect(latest?.status).toBe("ready");
    expect(latest?.draft).toMatchObject({
      weekdayMinutes: 180,
      weekendMinutes: 360,
      fixedRestDay: "sunday",
      mockExamDay: "saturday",
    });
    expect(putStudyPreference).not.toHaveBeenCalled();
  });
});
