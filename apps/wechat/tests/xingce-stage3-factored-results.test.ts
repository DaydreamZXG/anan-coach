import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  XingceCauseCandidate,
  XingceTaskSession,
  XingceVerificationOutcome,
} from "../miniprogram/api/xingce-learning";
import { environment } from "../miniprogram/config/environment";
import {
  presentXingceCauseCandidate,
  presentXingceResult,
} from "../miniprogram/features/xingce/real_attempt/presentation";
import {
  apiSuccess,
  completedXingceSession,
  createMemoryWechatStorage,
  reflectedXingceSession,
  submittedXingceSession,
  xingceIds,
} from "./work_packages/vj-02-real-xingce-wechat/harness";

const originalDataMode = environment.dataMode;
const nextCandidateId = "56000000-0000-4000-8000-000000000001";

afterEach(() => {
  environment.dataMode = originalDataMode;
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.resetModules();
});

describe("stage 3 factored result presentation", () => {
  it("把真会、猜对、不会、主动放弃和方法对计算错作为独立事实", () => {
    expect(presentXingceResult(submittedXingceSession("correct"))?.items[0]?.resultLabel).toBe(
      "真会",
    );
    expect(
      presentXingceResult(submittedXingceSession("guessed_correct"))?.items[0]?.resultLabel,
    ).toBe("猜对");
    expect(presentXingceResult(submittedXingceSession("incorrect"))?.items[0]?.resultLabel).toBe(
      "不会·答错",
    );
    expect(presentXingceResult(submittedXingceSession("skipped"))?.items[0]?.resultLabel).toBe(
      "主动放弃",
    );

    const interpreted = reflectedXingceSession("incorrect");
    if (!interpreted.review?.reflection || !interpreted.result?.groupSummary) {
      throw new Error("fixture reflection is required");
    }
    interpreted.review.reflection.itemInterpretations = [
      { questionId: "q-1", interpretation: "method_correct_calculation_error" },
    ];
    interpreted.result.groupSummary.methodCorrectCalculationError = 1;
    expect(presentXingceResult(interpreted)?.items[0]?.resultLabel).toBe("方法正确但计算错误");
  });

  it("超建议用时只是计时事实，不改写客观判定或分类", () => {
    const regular = presentXingceResult(submittedXingceSession("incorrect"));
    const exceeded = presentXingceResult(submittedXingceSession("timeout"));
    expect(exceeded?.items[0]).toMatchObject({
      resultLabel: regular?.items[0]?.resultLabel,
      objectiveLabel: "客观答错",
      timingExceeded: true,
    });
    expect(exceeded?.items[0]?.timingLabel).toContain("超过建议");
  });

  it("展示 3 题组 attained/not_attained/interrupted 计数和下一步", () => {
    expect(presentXingceResult(submittedXingceSession("correct"))?.group).toMatchObject({
      outcome: "attained",
      nextActionLabel: "下一步：完成用户原话复盘",
    });
    expect(presentXingceResult(submittedXingceSession("incorrect"))?.group).toMatchObject({
      outcome: "not_attained",
    });
    expect(presentXingceResult(submittedXingceSession("skipped"))?.group).toMatchObject({
      outcome: "interrupted",
    });
  });

  it.each([
    ["passed", "valid", "复测通过", "证据有效"],
    ["not_passed", "valid", "复测未通过", "证据有效"],
    ["disputed_or_invalidated", "pending_dispute", "复测结果待核验", "争议处理中"],
    ["disputed_or_invalidated", "confirmed_invalidated", "复测结果待核验", "证据已确认失效"],
  ] as const)(
    "shows %s with %s evidence without reading compatibility resultCode",
    (outcome, evidenceState, outcomeLabel, evidenceLabel) => {
      const session = verificationSession(outcome, evidenceState);
      expect(presentXingceResult(session)?.verification).toMatchObject({
        outcome,
        evidenceState,
        outcomeLabel,
        evidenceLabel,
      });
    },
  );
});

describe("stage 3 cause workflow", () => {
  it("从处理中进入追问，且追问与否定决策均绑定当前候选版本", async () => {
    let captured: CapturedReflectionPage | undefined;
    const { wxStorage } = createMemoryWechatStorage();
    const writes: WechatMiniprogram.RequestOption[] = [];
    let reads = 0;
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      if (options.method === "GET") {
        reads += 1;
        options.success?.(
          apiSuccess(reads === 1 ? processingSession() : followupRequiredSession()),
        );
        return;
      }
      writes.push(options);
      if (options.url.endsWith("/cause-followup")) {
        options.success?.(apiSuccess(evidenceInsufficientSession()));
        return;
      }
      options.success?.(apiSuccess(completedXingceSession("reject")));
    });
    stubReflectionPage(wxStorage, request, (definition) => {
      captured = definition;
    });
    await import("../miniprogram/pages/reflection/index");
    if (!captured) throw new Error("reflection page was not registered");
    const page = instantiateReflectionPage(captured);

    page.onLoad.call(page, { sessionId: xingceIds.session, reviewId: xingceIds.review });
    await vi.waitFor(() => {
      expect(page.data.phase).toBe("cause_processing");
    });
    await page.refreshCauseCandidate.call(page);
    expect(page.data.phase).toBe("cause_followup");

    page.inputFollowupAnswer.call(page, inputEvent("我当时没看到否定词。"));
    await page.submitCauseFollowup.call(page);
    expect(page.data.phase).toBe("cause_insufficient");
    expect(writes[0]?.data).toMatchObject({
      expectedRevision: 2,
      candidateVersionId: xingceIds.candidate,
      answer: "我当时没看到否定词。",
    });

    await page.rejectCause.call(page);
    expect(writes[1]?.data).toMatchObject({
      expectedRevision: 3,
      candidateVersionId: nextCandidateId,
      decision: "reject",
    });
    expect(JSON.stringify(writes.map((item) => item.data))).not.toContain('"skip"');
    page.onUnload.call(page);
  });

  it("全对高信心直达证据不足，可否定但不会伪造已确认错因", () => {
    const candidate = evidenceInsufficientCandidate(
      xingceIds.candidate,
      "all_correct_high_confidence",
    );
    expect(presentXingceCauseCandidate(candidate)).toMatchObject({
      state: "evidence_insufficient",
      candidateVersionId: xingceIds.candidate,
      canConfirm: false,
      canDecide: true,
    });

    const omittedVersion = evidenceInsufficientCandidate(
      xingceIds.candidate,
      "all_correct_high_confidence",
    );
    delete omittedVersion.candidateVersionId;
    expect(presentXingceCauseCandidate(omittedVersion)).toMatchObject({
      candidateVersionId: null,
      canConfirm: false,
      canDecide: false,
    });
  });
});

function verificationSession(
  outcome: XingceVerificationOutcome["outcome"],
  evidenceState: XingceVerificationOutcome["evidenceState"],
): XingceTaskSession {
  const session = submittedXingceSession(outcome === "passed" ? "correct" : "incorrect");
  session.mode = "verification";
  session.verificationOutcome = {
    outcome,
    evidenceState,
    sourceMasteryVersionId: "90000000-0000-4000-8000-000000000001",
    verificationStage: "first_review",
  };
  return session;
}

function processingSession(): XingceTaskSession {
  const session = reflectedXingceSession();
  if (!session.review) throw new Error("fixture review is required");
  session.state = "cause_candidate_processing";
  session.review.state = "cause_candidate_pending";
  session.review.aiCandidate = causeCandidate("processing", null);
  return session;
}

function followupRequiredSession(): XingceTaskSession {
  const session = reflectedXingceSession();
  if (!session.review) throw new Error("fixture review is required");
  session.state = "cause_followup_required";
  session.review.state = "cause_followup_required";
  session.review.aiCandidate = {
    ...causeCandidate("followup_required", xingceIds.candidate),
    question: "你当时是没看到否定词，还是理解反了？",
  };
  return session;
}

function evidenceInsufficientSession(): XingceTaskSession {
  const session = reflectedXingceSession();
  if (!session.review) throw new Error("fixture review is required");
  session.state = "cause_evidence_insufficient";
  session.revision = 6;
  session.review.revision = 3;
  session.review.state = "cause_evidence_insufficient";
  session.review.aiCandidate = evidenceInsufficientCandidate(
    nextCandidateId,
    "followup_still_insufficient",
  );
  return session;
}

function evidenceInsufficientCandidate(
  candidateVersionId: string,
  reason: string,
): XingceCauseCandidate {
  return {
    ...causeCandidate("evidence_insufficient", candidateVersionId),
    reason,
    explanation: "当前证据不足以支持一个可确认错因。",
    missingEvidence: ["可区分两种错因的作答证据"],
  };
}

function causeCandidate(
  state: XingceCauseCandidate["state"],
  candidateVersionId: string | null,
): XingceCauseCandidate {
  return {
    state,
    candidateVersionId,
    reason: null,
    causeCode: null,
    summary: null,
    improvement: null,
    question: null,
    rationale: null,
    explanation: null,
    missingEvidence: [],
    evidenceRefs: [],
    limitations: [],
    quotaState: "not_metered",
    entitlementPolicyVersion: "included-with-canonical-practice@v1",
  };
}

interface CapturedReflectionPage {
  data: Record<string, unknown>;
  onLoad(this: TestReflectionPage, options: Record<string, string | undefined>): void;
  onUnload(this: TestReflectionPage): void;
  refreshCauseCandidate(this: TestReflectionPage): Promise<void>;
  inputFollowupAnswer(this: TestReflectionPage, event: WechatMiniprogram.Input): void;
  submitCauseFollowup(this: TestReflectionPage): Promise<void>;
  rejectCause(this: TestReflectionPage): Promise<void>;
}

interface TestReflectionPage extends CapturedReflectionPage {
  data: Record<string, unknown>;
  setData(patch: Record<string, unknown>): void;
}

function instantiateReflectionPage(definition: CapturedReflectionPage): TestReflectionPage {
  return {
    ...definition,
    data: JSON.parse(JSON.stringify(definition.data)) as Record<string, unknown>,
    setData(patch) {
      Object.assign(this.data, patch);
    },
  };
}

function stubReflectionPage(
  wxStorage: ReturnType<typeof createMemoryWechatStorage>["wxStorage"],
  request: ReturnType<typeof vi.fn>,
  capture: (definition: CapturedReflectionPage) => void,
): void {
  vi.stubGlobal("Page", capture);
  vi.stubGlobal("getApp", () => ({
    globalData: { accessToken: "real-token", userId: "user-a" },
  }));
  vi.stubGlobal("wx", {
    ...wxStorage,
    request,
    showToast: vi.fn(() => Promise.resolve()),
    switchTab: vi.fn(() => Promise.resolve()),
    navigateBack: vi.fn(() => Promise.resolve()),
    redirectTo: vi.fn(() => Promise.resolve()),
    showModal: vi.fn(() => Promise.resolve({ confirm: true, cancel: false })),
    getRandomValues: () => Promise.resolve({ randomValues: new Uint8Array(16).fill(7).buffer }),
  });
  environment.dataMode = "api";
}

function inputEvent(value: string): WechatMiniprogram.Input {
  return { detail: { value } } as WechatMiniprogram.Input;
}
