import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";

import { ApiClient, ApiClientError } from "../miniprogram/api/client";
import {
  CommunityMethodCardsApi,
  communityMethodCardsContractVersion,
  type CommunityCardWorkspace,
  type CommunityDiscoveryCard,
  type CommunityRewardLedgerResponse,
  type CommunityStructureCandidate,
} from "../miniprogram/community/shared/api/community-method-cards";
import {
  assertEligibleTrial,
  assertFeedbackChoice,
  assertRewardLedgerTruth,
  assertStructureCandidate,
  assertTrialCard,
  buildConfirmation,
  commercialPageStates,
  communitySourceOptions,
  feedbackReasons,
  mapCommunityError,
  reportCategories,
  rightsOptions,
} from "../miniprogram/community/shared/features/community-method-cards/contracts";
import {
  communityTrialRecoveryView,
  commercialStateView,
  presentTrialCard,
} from "../miniprogram/community/shared/features/community-method-cards/presentation";
import { apiSuccess } from "./work_packages/vj-02-real-xingce-wechat/harness";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Stage 3.5 typed commercial boundary", () => {
  it("uses the Stage 3.5 v2 client contract", () => {
    expect(communityMethodCardsContractVersion).toBe("stage35-v2");
  });

  it("defines every commercial page state and all four private authoring sources", () => {
    expect(commercialPageStates).toEqual([
      "default",
      "loading",
      "empty",
      "partial",
      "error",
      "expired",
      "conflict",
      "recovery",
      "processing",
    ]);
    expect(communitySourceOptions.map((option) => option.value)).toEqual([
      "text",
      "voice_transcript",
      "handwriting_extraction",
      "reflection",
    ]);
    for (const state of commercialPageStates) {
      const view = commercialStateView(state);
      expect(typeof view.title).toBe("string");
      expect(typeof view.description).toBe("string");
    }
  });

  it("keeps AI output as an editable candidate and confirms against the exact head version", () => {
    const workspace = workspaceFixture();
    const candidate = candidateFixture();
    expect(assertStructureCandidate(candidate, workspace)).toBe(candidate);

    const confirmation = buildConfirmation(workspace, candidate, {
      title: "  我确认后的标题 ",
      recognition: "  先识别题干的比较口径 ",
      methodStepsText: "1. 圈出口径\n2. 对齐分母\n2. 对齐分母",
      applicabilityText: "资料分析，增长率比较，资料分析",
      exclusionsText: "缺少基期数据",
      pitfallsText: "混用百分点",
      rightsDeclaration: "original",
    });

    expect(confirmation).toMatchObject({
      cardId: "card-a",
      baseVersionId: "version-source",
      candidateId: "candidate-a",
      title: "我确认后的标题",
      methodSteps: ["圈出口径", "对齐分母"],
      applicability: ["资料分析", "增长率比较"],
      rightsDeclaration: "original",
    });
    expect(rightsOptions.find((option) => option.value === "private_third_party")).toMatchObject({
      publishable: false,
    });

    expect(() =>
      assertStructureCandidate({ ...candidate, baseVersionId: "stale-version" }, workspace),
    ).toThrow("AI 整理结果与当前草稿不一致");
  });

  it("allows only opt-in versioned trial evidence and fixed feedback/report choices", () => {
    const card = discoveryCardFixture();
    assertTrialCard(card);
    const view = presentTrialCard(card);
    expect(view).toMatchObject({
      methodSteps: ["先读问题", "再定位材料"],
      exclusions: ["纯常识判断"],
      pitfalls: ["忽略时间口径"],
      evidenceLabel: "12 条独立证据 · 已按保守效果政策排序",
      riskLabel: "风险提示：样本仍少",
    });
    expect(view).not.toHaveProperty("rawVotes");
    expect(view).not.toHaveProperty("voteCount");

    expect(() => {
      assertTrialCard({ ...card, isAuthor: true });
    }).toThrow("作者不能试用");
    expect(() => {
      assertEligibleTrial({
        exposureId: "exposure-a",
        questionAttemptId: "",
        questionVersionId: "question-version-a",
      });
    }).toThrow("缺少独立试题的有效记录");

    assertFeedbackChoice("helpful", "clear");
    expect(() => {
      assertFeedbackChoice("helpful", "incorrect");
    }).toThrow("不一致");
    expect(feedbackReasons.map((reason) => reason.value)).toEqual([
      "clear",
      "transferable",
      "too_specific",
      "unclear",
      "incorrect",
      "unsafe",
    ]);
    expect(reportCategories.map((category) => category.value)).toEqual([
      "copyright",
      "incorrect",
      "unsafe",
      "advertising",
      "contact_information",
      "other_fixed",
    ]);
  });

  it("maps expired, conflict and recoverable requests without converting them into content", () => {
    expect(mapCommunityError(new ApiClientError("expired", 401, "request-auth"))).toMatchObject({
      state: "expired",
      requestId: "request-auth",
    });
    expect(
      mapCommunityError(new ApiClientError("conflict", 409, "request-conflict")),
    ).toMatchObject({
      state: "conflict",
    });
    expect(mapCommunityError(new ApiClientError("offline", 0, "request-offline"))).toMatchObject({
      state: "recovery",
    });
    expect(
      mapCommunityError(
        new ApiClientError("社区方法服务的已发布策略暂不可用。", 503, "request-policy", {
          code: "community_policy_unavailable",
          message: "社区方法服务的已发布策略暂不可用。",
          requestId: "request-policy",
        }),
      ),
    ).toMatchObject({
      state: "error",
      title: "社区试用还没开放",
      requestId: "request-policy",
    });
  });

  it("recovers the exact interrupted trial operation with plain-language actions", () => {
    expect(communityTrialRecoveryView("load_cards")).toMatchObject({
      title: "方法还没加载出来",
      actionLabel: "重新读取方法",
    });
    expect(communityTrialRecoveryView("opt_in").actionLabel).toBe("继续加入试用");
    expect(communityTrialRecoveryView("complete_trial").actionLabel).toBe("继续核验");
    expect(communityTrialRecoveryView("submit_feedback").description).toContain("只保留一次");
    expect(communityTrialRecoveryView("submit_report").description).toContain("不会重复受理");

    const pageSource = readFileSync("miniprogram/community/community-trial/index.ts", "utf8");
    expect(pageSource).toContain('operation === "opt_in") void this.optIn()');
    expect(pageSource).toContain('operation === "complete_trial") void this.completeTrial()');
    expect(pageSource).toContain('operation === "submit_feedback") void this.submitFeedback()');
    expect(pageSource).toContain('operation === "submit_report") void this.submitReport()');
  });
});

describe("Stage 3.5 API transport", () => {
  it("sends only canonical attempt locators for trial and delayed verification", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    vi.stubGlobal("wx", {
      request: vi.fn((options: WechatMiniprogram.RequestOption) => {
        requests.push(options);
        if (options.url.endsWith("/drafts")) {
          options.success?.(
            apiSuccess({
              cardId: "card-a",
              versionId: "version-source",
              status: "private_draft",
              candidateStatus: "processing",
            }),
          );
        } else if (options.url.endsWith("/exposures")) {
          options.success?.(
            apiSuccess({
              exposureId: "exposure-a",
              cardId: "card-a",
              versionId: "version-confirmed",
              optedInAt: "2026-07-16T08:00:00Z",
            }),
          );
        } else if (options.url.endsWith("/trials")) {
          options.success?.(
            apiSuccess({
              trialId: "trial-a",
              feedbackEligible: true,
              outcomeCode: "server_verified",
              delayedVerificationDueAt: "2026-07-23T08:00:00Z",
            }),
          );
        } else if (options.url.includes("/delayed-verifications")) {
          options.success?.(apiSuccess({ verificationId: "verification-a", recorded: true }));
        } else if (options.url.endsWith("/feedback")) {
          options.success?.(apiSuccess({ voteId: "vote-a", recorded: true }));
        } else {
          throw new Error(`unexpected request: ${options.url}`);
        }
      }),
    });
    const api = new CommunityMethodCardsApi(
      new ApiClient("https://api.example.test", "access-token"),
    );

    await api.createDraft({
      sourceType: "reflection",
      sourceText: "我的复盘方法",
      track: "xingce",
      applicability: ["判断推理"],
      idempotencyKey: "draft-command-a",
    });
    await api.optInToTrial("version-confirmed", ["判断推理"], "ranking-policy-v1");
    await api.completeTrial({
      exposureId: "exposure-a",
      questionAttemptId: "attempt-current",
      questionVersionId: "question-current",
    });
    await api.recordDelayedVerification({
      trialId: "trial-a",
      questionAttemptId: "attempt-delayed",
      questionVersionId: "question-delayed",
    });
    await api.recordFeedback("trial-a", "helpful", "transferable");

    expect(requests.map((request) => request.url)).toEqual([
      "https://api.example.test/api/v2/community-method-cards/drafts",
      "https://api.example.test/api/v2/community-method-cards/exposures",
      "https://api.example.test/api/v2/community-method-cards/trials",
      "https://api.example.test/api/v2/community-method-cards/trials/trial-a/delayed-verifications",
      "https://api.example.test/api/v2/community-method-cards/feedback",
    ]);
    expect(requests[0]?.header).toMatchObject({
      Authorization: "Bearer access-token",
      "Idempotency-Key": "draft-command-a",
    });
    expect(requests[1]?.data).toEqual({
      cardVersionId: "version-confirmed",
      applicabilitySnapshot: ["判断推理"],
      rankingPolicyVersion: "ranking-policy-v1",
    });
    expect(requests[2]?.data).toEqual({
      exposureId: "exposure-a",
      questionAttemptId: "attempt-current",
      questionVersionId: "question-current",
    });
    expect(requests[3]?.data).toEqual({
      trialId: "trial-a",
      questionAttemptId: "attempt-delayed",
      questionVersionId: "question-delayed",
    });
    for (const requestIndex of [2, 3]) {
      for (const untrustedField of [
        "correctAfter",
        "durationAfterMs",
        "deviceClusterId",
        "outcome",
        "evidenceRefs",
      ]) {
        expect(requests[requestIndex]?.data).not.toHaveProperty(untrustedField);
      }
    }
    expect(requests[4]?.data).toEqual({
      trialId: "trial-a",
      vote: "helpful",
      reasonCode: "transferable",
    });
    expect(requests[4]?.data).not.toHaveProperty("comment");
  });
});

describe("Stage 3.5 reward truth", () => {
  it("accepts an empty-policy ledger only with real zero balances and an entitlement version", () => {
    const ledger: CommunityRewardLedgerResponse = {
      activePolicyVersions: [],
      awardIds: [],
      entryIds: [],
      aiQuotaBalance: 0,
      membershipSecondsBalance: 0,
      entitlementVersion: "entitlement-empty-v1",
    };
    assertRewardLedgerTruth(ledger);
  });

  it("validates plural policies, immutable receipt IDs and real entitlement balances", () => {
    const ledger = rewardLedgerFixture();
    assertRewardLedgerTruth(ledger);
    expect(ledger).toMatchObject({
      activePolicyVersions: ["reward-policy-ai-v2", "reward-policy-membership-v1"],
      awardIds: ["award-a"],
      entryIds: ["entry-consume"],
      aiQuotaBalance: 5,
      membershipSecondsBalance: 7200,
      entitlementVersion: "entitlement-projection-42",
    });
    expect(() => {
      assertRewardLedgerTruth({ ...ledger, aiQuotaBalance: -1 });
    }).toThrow("权益余额无效");
    expect(() => {
      assertRewardLedgerTruth({ ...ledger, awardIds: ["award-a", "award-a"] });
    }).toThrow("奖励批次记录无效");
    expect(() => {
      assertRewardLedgerTruth({ ...ledger, entitlementVersion: " " });
    }).toThrow("权益记录缺失");
  });
});

function workspaceFixture(): CommunityCardWorkspace {
  return {
    cardId: "card-a",
    status: "private_draft",
    headVersionId: "version-source",
    versionIds: ["version-source"],
    candidateStatus: "ready",
    reviewStatus: "none",
    versions: [
      {
        versionId: "version-source",
        cardId: "card-a",
        versionNumber: 1,
        baseVersionId: null,
        title: null,
        recognition: null,
        methodSteps: [],
        applicability: ["资料分析"],
        exclusions: [],
        pitfalls: [],
        rightsDeclaration: null,
        authorConfirmedAt: null,
      },
    ],
  };
}

function candidateFixture(): CommunityStructureCandidate {
  return {
    candidateId: "candidate-a",
    baseVersionId: "version-source",
    title: "AI 候选标题",
    recognition: "AI 候选识别信号",
    methodSteps: ["候选步骤一", "候选步骤二"],
    sourceSpanRefs: ["source:0-8"],
    status: "ready",
    failureCode: null,
  };
}

function discoveryCardFixture(): CommunityDiscoveryCard {
  return {
    cardVersionId: "version-confirmed",
    title: "定位材料的方法",
    recognition: "题干出现时间与主体对比",
    methodSteps: ["先读问题", "再定位材料"],
    applicability: ["资料分析"],
    exclusions: ["纯常识判断"],
    pitfalls: ["忽略时间口径"],
    conservativeScore: 0.62,
    evidenceCount: 12,
    riskFlags: ["样本仍少"],
    evidencePolicyVersion: "effect-policy-v1",
    isAuthor: false,
  };
}

function rewardLedgerFixture(): CommunityRewardLedgerResponse {
  return {
    activePolicyVersions: ["reward-policy-ai-v2", "reward-policy-membership-v1"],
    awardIds: ["award-a"],
    entryIds: ["entry-consume"],
    aiQuotaBalance: 5,
    membershipSecondsBalance: 7200,
    entitlementVersion: "entitlement-projection-42",
  };
}
