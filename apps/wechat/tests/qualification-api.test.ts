import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../miniprogram/api/client";
import { QualificationApi } from "../miniprogram/api/qualification";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("QualificationApi", () => {
  it("loads qualification gaps from the authenticated server profile", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe("https://api.example.test/api/v2/qualification/gaps");
      expect(options.method).toBe("GET");
      expect(options.data).toBeUndefined();
      options.success?.({
        data: {
          data: {
            examYear: 2026,
            learnerProfileVersion: 2,
            ruleSetVersion: "rules-v1",
            positionDataVersion: "positions-v1",
            totalAffectedPositions: 0,
            gaps: [],
            complete: true,
          },
          requestId: "gap-request-1",
          generatedAt: "2026-07-12T12:00:00Z",
        },
        statusCode: 200,
        header: {},
        cookies: [],
        exception: { reasons: [], retryCount: 0 },
        profile: {} as WechatMiniprogram.RequestProfile,
        useHttpDNS: false,
        errMsg: "request:ok",
      });
    });
    vi.stubGlobal("wx", { request });
    const api = new QualificationApi(new ApiClient("https://api.example.test", "token"));

    await api.getQualificationGaps();

    expect(request).toHaveBeenCalledOnce();
  });

  it("compares only the explicitly selected position IDs", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe("https://api.example.test/api/v2/positions/compare");
      expect(options.method).toBe("POST");
      expect(options.data).toEqual({ positionIds: ["position-2", "position-1"] });
      options.success?.({
        data: {
          data: {
            examYear: 2026,
            learnerProfileVersion: 2,
            ruleSetVersion: "rules-v1",
            positionDataVersion: "positions-v1",
            items: [],
            highlights: {
              confirmedEligiblePositionIds: [],
              largestHeadcountPositionIds: [],
              lowestTargetScorePositionIds: [],
              primaryGoalPositionId: null,
            },
          },
          requestId: "comparison-request-1",
          generatedAt: "2026-07-12T12:00:00Z",
        },
        statusCode: 200,
        header: {},
        cookies: [],
        exception: { reasons: [], retryCount: 0 },
        profile: {} as WechatMiniprogram.RequestProfile,
        useHttpDNS: false,
        errMsg: "request:ok",
      });
    });
    vi.stubGlobal("wx", { request });
    const api = new QualificationApi(new ApiClient("https://api.example.test", "token"));

    await api.comparePositions(["position-2", "position-1"]);

    expect(request).toHaveBeenCalledOnce();
  });

  it("discovers positions with encoded filters and an opaque cursor", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe(
        "https://api.example.test/api/v2/positions?examYear=2026&region=%E6%B1%9F%E8%A5%BF&q=%E7%A8%8E%E5%8A%A1&limit=20&cursor=next%2Fpage",
      );
      options.success?.({
        data: {
          data: {
            learnerProfileVersion: 2,
            ruleSetVersion: "rules-v1",
            positionDataVersion: "positions-v1",
            items: [],
            nextCursor: null,
            hasMore: false,
          },
          requestId: "discovery-request-1",
          generatedAt: "2026-07-12T12:00:00Z",
        },
        statusCode: 200,
        header: {},
        cookies: [],
        exception: { reasons: [], retryCount: 0 },
        profile: {} as WechatMiniprogram.RequestProfile,
        useHttpDNS: false,
        errMsg: "request:ok",
      });
    });
    vi.stubGlobal("wx", { request });
    const api = new QualificationApi(new ApiClient("https://api.example.test", "token"));

    const result = await api.discoverPositions({
      examYear: 2026,
      region: "江西",
      query: "税务",
      limit: 20,
      cursor: "next/page",
    });

    expect(result.data.items).toEqual([]);
  });

  it("loads a deep-linkable v2 position detail instead of relying on list cache", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe("https://api.example.test/api/v2/positions/position%2F1");
      expect(options.method).toBe("GET");
      options.success?.({
        data: {
          data: {
            positionId: "position/1",
            positionCode: "3001",
            name: "一级行政执法员",
            departmentFull: "国家税务总局测试局",
            systemCode: "tax",
            region: "江西",
            city: "赣州",
            headcount: 2,
            eligibilityStatus: "eligible",
            metCount: 3,
            pendingCount: 0,
            notMetCount: 0,
            reasonCodes: [],
            targetScore: {
              lowerBound: 126,
              recommended: 129,
              upperBound: 132,
              sampleCount: 6,
              basisLevel: "direct",
              evidenceLevel: "direct",
              generatedAt: "2026-07-01T00:00:00Z",
              validUntil: "2027-07-01T00:00:00Z",
            },
            isPrimaryGoal: false,
            paperType: "administrativeLaw",
            examYear: 2026,
            learnerProfileVersion: 2,
            ruleSetVersion: "rules-v1",
            positionDataVersion: "positions-v1",
          },
          requestId: "detail-request-1",
          generatedAt: "2026-07-13T00:00:00Z",
        },
        statusCode: 200,
        header: {},
        cookies: [],
        exception: { reasons: [], retryCount: 0 },
        profile: {} as WechatMiniprogram.RequestProfile,
        useHttpDNS: false,
        errMsg: "request:ok",
      });
    });
    vi.stubGlobal("wx", { request });
    const api = new QualificationApi(new ApiClient("https://api.example.test", "token"));

    const result = await api.getPositionDetail("position/1");

    expect(result.data.name).toBe("一级行政执法员");
    expect(result.data.targetScore?.validUntil).toBe("2027-07-01T00:00:00Z");
  });

  it("writes only qualification facts with an idempotency key", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe("https://api.example.test/api/v2/qualification/profile");
      expect(options.method).toBe("PUT");
      expect(options.header).toMatchObject({
        Authorization: "Bearer session-token",
        "Idempotency-Key": "profile-a02-1",
      });
      expect(options.data).toEqual({
        facts: [
          { key: "education", value: "本科" },
          { key: "major", value: "法学" },
        ],
      });
      options.success?.({
        data: {
          data: { profileVersion: 2, facts: [] },
          requestId: "profile-request-1",
          generatedAt: "2026-07-12T12:00:00Z",
        },
        statusCode: 200,
        header: {},
        cookies: [],
        exception: { reasons: [], retryCount: 0 },
        profile: {} as WechatMiniprogram.RequestProfile,
        useHttpDNS: false,
        errMsg: "request:ok",
      });
    });
    vi.stubGlobal("wx", { request });
    const api = new QualificationApi(new ApiClient("https://api.example.test", "session-token"));

    const result = await api.updateProfile(
      [
        { key: "education", value: "本科" },
        { key: "major", value: "法学" },
      ],
      "profile-a02-1",
    );

    expect(result.data.profileVersion).toBe(2);
    expect(request).toHaveBeenCalledOnce();
  });

  it("previews a primary goal using only a server eligibility snapshot", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe("https://api.example.test/api/v2/position-goals/primary/preview");
      expect(options.data).toEqual({
        positionId: "position-1",
        eligibilitySnapshotId: "snapshot-1",
      });
      expect(options.data).not.toHaveProperty("targetScore");
      expect(options.data).not.toHaveProperty("learnerId");
      expect(options.header).toMatchObject({ "Idempotency-Key": "goal-preview-1" });
      options.success?.({
        data: {
          data: {
            previewId: "preview-1",
            positionId: "position-1",
            eligibilitySnapshotId: "snapshot-1",
            currentGoalVersionId: null,
            learnerProfileVersion: 2,
            positionDataVersion: "guokao-2026-v1",
            targetScore: {
              lowerBound: 126,
              recommended: 129,
              upperBound: 132,
              evidenceLevel: "direct",
              sampleCount: 6,
            },
            planImpact: {
              requiresPlanRebuild: true,
              changeType: "goal_selected",
              positionChanged: true,
              previousTargetScore: null,
              nextTargetScore: 129,
              targetScoreDelta: null,
              invalidates: ["daily_plan", "weekly_plan", "score_gap"],
            },
            expiresAt: "2026-07-12T12:15:00Z",
            createdAt: "2026-07-12T12:00:00Z",
          },
          requestId: "goal-preview-request-1",
          generatedAt: "2026-07-12T12:00:00Z",
        },
        statusCode: 200,
        header: {},
        cookies: [],
        exception: { reasons: [], retryCount: 0 },
        profile: {} as WechatMiniprogram.RequestProfile,
        useHttpDNS: false,
        errMsg: "request:ok",
      });
    });
    vi.stubGlobal("wx", { request });
    const api = new QualificationApi(new ApiClient("https://api.example.test", "session-token"));

    const result = await api.previewPrimaryGoal(
      {
        positionId: "position-1",
        eligibilitySnapshotId: "snapshot-1",
      },
      "goal-preview-1",
    );

    expect(result.data.previewId).toBe("preview-1");
  });

  it("confirms a primary goal using only a single-use preview", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe("https://api.example.test/api/v2/position-goals/primary");
      expect(options.data).toEqual({
        previewId: "preview-1",
        changeReason: "确认主报岗位",
      });
      expect(options.data).not.toHaveProperty("positionId");
      expect(options.data).not.toHaveProperty("targetScore");
      options.success?.({
        data: {
          data: {
            goal: {
              goalVersionId: "goal-1",
              positionId: "position-1",
              role: "primary",
              status: "confirmed",
              targetScore: {
                lowerBound: 126,
                recommended: 129,
                upperBound: 132,
                evidenceLevel: "direct",
                sampleCount: 6,
              },
              learnerProfileVersion: 2,
              eligibilitySnapshotId: "snapshot-1",
              positionDataVersion: "guokao-2026-v1",
              createdAt: "2026-07-12T12:00:00Z",
            },
            planImpact: {
              requiresPlanRebuild: true,
              changeType: "goal_selected",
              positionChanged: true,
              previousTargetScore: null,
              nextTargetScore: 129,
              targetScoreDelta: null,
              invalidates: ["daily_plan", "weekly_plan", "score_gap"],
            },
          },
          requestId: "goal-request-1",
          generatedAt: "2026-07-12T12:00:00Z",
        },
        statusCode: 200,
        header: {},
        cookies: [],
        exception: { reasons: [], retryCount: 0 },
        profile: {} as WechatMiniprogram.RequestProfile,
        useHttpDNS: false,
        errMsg: "request:ok",
      });
    });
    vi.stubGlobal("wx", { request });
    const api = new QualificationApi(new ApiClient("https://api.example.test", "session-token"));

    const result = await api.confirmPrimaryGoal(
      { previewId: "preview-1", changeReason: "确认主报岗位" },
      "goal-command-1",
    );

    expect(result.data.goal.goalVersionId).toBe("goal-1");
  });

  it("records an explicit learner response for a manual requirement", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toContain(
        "/api/v2/positions/position-1/requirements/requirement-1/confirmation",
      );
      expect(options.data).toEqual({
        response: "satisfied",
        statement: "我的情况符合该条件。",
      });
      expect(options.header).toMatchObject({ "Idempotency-Key": "confirmation-1" });
      options.success?.({
        data: {
          data: {
            confirmation: {
              confirmationId: "confirmation-1",
              positionId: "position-1",
              requirementId: "requirement-1",
              profileVersion: 3,
              response: "satisfied",
              statement: "我的情况符合该条件。",
              confirmedAt: "2026-07-12T12:00:00Z",
            },
            eligibility: {
              snapshotId: "snapshot-2",
              positionId: "position-1",
              learnerProfileVersion: 3,
              facts: [],
              evaluation: {
                status: "eligible",
                checks: [],
                missingFactKeys: [],
                ruleSetVersion: "rules-v1",
                positionDataVersion: "positions-v1",
                evaluatedAt: "2026-07-12T12:00:00Z",
              },
            },
          },
          requestId: "confirmation-request-1",
          generatedAt: "2026-07-12T12:00:00Z",
        },
        statusCode: 200,
        header: {},
        cookies: [],
        exception: { reasons: [], retryCount: 0 },
        profile: {} as WechatMiniprogram.RequestProfile,
        useHttpDNS: false,
        errMsg: "request:ok",
      });
    });
    vi.stubGlobal("wx", { request });
    const api = new QualificationApi(new ApiClient("https://api.example.test", "token"));

    const result = await api.confirmRequirement(
      "position-1",
      "requirement-1",
      { response: "satisfied", statement: "我的情况符合该条件。" },
      "confirmation-1",
    );

    expect(result.data.confirmation.profileVersion).toBe(3);
  });
});
