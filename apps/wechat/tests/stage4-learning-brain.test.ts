import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../miniprogram/api/client";
import { CompanionApi } from "../miniprogram/api/companion";
import { LearningBrainApi } from "../miniprogram/api/learning-brain";
import { PlanningApi } from "../miniprogram/api/planning";
import { routes } from "../miniprogram/app/navigation/routes";
import { resolveCompanionAction } from "../miniprogram/features/learning-brain/companion-action";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("stage 4 WeChat learning brain", () => {
  it("exposes every B and E formal page through registered route constants", () => {
    expect(routes).toMatchObject({
      planBasis: "/plans/plan-basis/index",
      dayAdjustment: "/plans/day-adjustment/index",
      dailySettlement: "/plans/daily-settlement/index",
      weeklyPlan: "/plans/weekly-plan/index",
      ability: "/pages/ability/index",
      abilityModule: "/reports/ability-module/index",
      errorPattern: "/reports/error-pattern/index",
      anan: "/pages/anan/index",
      planHistory: "/plans/plan-history/index",
    });
  });

  it("uses v2 server authorities and never submits client-authored benefit evidence", async () => {
    const calls: WechatMiniprogram.RequestOption[] = [];
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      calls.push(options);
      options.success?.(success({}));
    });
    vi.stubGlobal("wx", { request });
    const client = new ApiClient("https://api.example.test", "token");
    const brain = new LearningBrainApi(client);
    const planning = new PlanningApi(client);

    await brain.getProfile();
    await brain.getModule("xingce", "资料/分析");
    await brain.getErrorPattern("pattern/1");
    await planning.getTodayBasis();
    await planning.getTodaySettlement();
    await planning.getWeek();
    await planning.getHistory();
    await brain.createStrategyCandidate(
      {
        basedOnProfileVersionId: "00000000-0000-4000-8000-000000000401",
        decisionType: "phase_deprioritize",
        scopeType: "module",
        scopeKey: "资料分析",
        rationale: "当前单位时间收益低，先保留复评边界。",
        reviewAt: "2026-07-20T00:00:00Z",
        effectiveUntil: "2026-07-27T00:00:00Z",
      },
      "strategy-command-1",
    );

    expect(calls.map((call) => call.url)).toEqual([
      "https://api.example.test/api/v2/learning-brain/profile",
      "https://api.example.test/api/v2/learning-brain/modules/xingce/%E8%B5%84%E6%96%99%2F%E5%88%86%E6%9E%90",
      "https://api.example.test/api/v2/learning-brain/error-patterns/pattern%2F1",
      "https://api.example.test/api/v2/plans/today/basis",
      "https://api.example.test/api/v2/plans/today/settlement",
      "https://api.example.test/api/v2/plans/week",
      "https://api.example.test/api/v2/plans/history",
      "https://api.example.test/api/v2/learning-brain/strategy-candidates",
    ]);
    expect(calls.at(-1)?.header).toMatchObject({
      Authorization: "Bearer token",
      "Idempotency-Key": "strategy-command-1",
    });
    expect(calls.at(-1)?.data).not.toHaveProperty("benefitEvidence");
    expect(calls.at(-1)?.data).not.toHaveProperty("evidenceRefs");
  });

  it("asks 岸岸 through the paid v2 companion command", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe("https://api.example.test/api/v2/companion/ask");
      expect(options.header).toMatchObject({ "Idempotency-Key": "companion-command-1" });
      expect(options.data).toEqual({
        questionText: "资料分析应该怎么复测？",
        context: {
          routeId: "pages/anan/index",
          workspace: "training",
          objectRefs: [],
          learnerStateVersion: "profile-version-1",
        },
      });
      options.success?.(success({}));
    });
    vi.stubGlobal("wx", { request });

    await new CompanionApi(new ApiClient("https://api.example.test", "token")).ask(
      {
        questionText: "资料分析应该怎么复测？",
        context: {
          routeId: "pages/anan/index",
          workspace: "training",
          objectRefs: [],
          learnerStateVersion: "profile-version-1",
        },
      },
      "companion-command-1",
    );
    expect(request).toHaveBeenCalledOnce();
  });

  it("executes only the workspace route declared for each 岸岸 action kind", () => {
    expect(
      resolveCompanionAction({
        kind: "adjust_plan_preview",
        label: "预览今日计划调整",
        targetRef: routes.dayAdjustment,
        requiresConfirmation: true,
      }),
    ).toEqual({
      kind: "navigate",
      target: { mode: "navigateTo", url: routes.dayAdjustment },
      requiresConfirmation: true,
    });
    expect(
      resolveCompanionAction({
        kind: "open_training",
        label: "伪装的计划按钮",
        targetRef: routes.privacy,
        requiresConfirmation: false,
      }),
    ).toEqual({ kind: "unavailable" });
    expect(
      resolveCompanionAction({
        kind: "refresh_context",
        label: "刷新学习证据",
        targetRef: null,
        requiresConfirmation: false,
      }),
    ).toEqual({ kind: "refresh_context" });
  });
});

function success(data: unknown): WechatMiniprogram.RequestSuccessCallbackResult {
  return {
    data: {
      data,
      requestId: "stage4-request-1",
      generatedAt: "2026-07-16T00:00:00Z",
    },
    statusCode: 200,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  };
}
