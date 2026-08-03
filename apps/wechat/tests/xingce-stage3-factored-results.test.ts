import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  XingceCauseCandidate,
  XingceTaskSession,
  XingceVerificationOutcome,
} from "../miniprogram/api/xingce-learning";
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
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import reflectionTemplate from "../miniprogram/pages/reflection/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import reflectionStyles from "../miniprogram/pages/reflection/index.wxss?raw";

const nextCandidateId = "56000000-0000-4000-8000-000000000001";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.resetModules();
});

describe("stage 3 factored result presentation", () => {
  it("按真实结果分色，不把所有需复盘题统一显示成红色", () => {
    expect(reflectionTemplate).toContain(
      "summary-question-dot summary-question-dot--{{item.resultTone}}",
    );
    expect(reflectionTemplate).toContain(
      "summary-question-label summary-question-label--{{item.resultTone}}",
    );
    expect(reflectionTemplate).toContain(">{{item.resultLabel}}</view>");
    expect(reflectionTemplate).not.toContain("复盘·{{item.resultLabel}}");
    expect(reflectionStyles).toMatch(
      /\.summary-question-strip \{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*repeat\(6, minmax\(0, 1fr\)\);/u,
    );
    expect(reflectionStyles).toMatch(
      /@media \(min-width: 700px\) \{[^]*?\.summary-question-strip \{[^}]*grid-template-columns:\s*repeat\(10, minmax\(0, 1fr\)\);/u,
    );
    expect(reflectionStyles).not.toMatch(/\.summary-question-strip__item \{[^}]*flex:\s*1;/u);
    for (const tone of ["danger", "warning", "success", "neutral"]) {
      expect(reflectionStyles).toContain(`.summary-question-dot--${tone}`);
    }
    expect(reflectionStyles).not.toContain(".summary-question-dot--review");
  });

  it("把真会、猜对、不会、主动放弃和方法对计算错作为独立事实", () => {
    expect(presentXingceResult(submittedXingceSession("correct"))?.items[0]).toMatchObject({
      resultLabel: "真会",
      selectedAnswerText: "选项 B",
      correctAnswerText: "选项 A",
    });
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

  it("接受 30 题训练结果，不再用固定 3 题合同拦截交卷", () => {
    const session = submittedXingceSession("correct");
    const baseQuestion = session.bundle.questions[0];
    const baseAnswer = session.answers[0];
    const baseItem = session.result?.items[0];
    if (!baseQuestion || !baseAnswer || !baseItem || !session.result?.groupSummary) {
      throw new Error("fixture result is required");
    }
    session.bundle.questions = Array.from({ length: 30 }, (_, index) => ({
      ...baseQuestion,
      questionId: `q-${String(index + 1)}`,
    }));
    session.answers = session.bundle.questions.map((question) => ({
      ...baseAnswer,
      questionId: question.questionId,
    }));
    session.result.totalCount = 30;
    session.result.correctCount = 30;
    session.result.items = session.bundle.questions.map((question) => ({
      ...baseItem,
      questionId: question.questionId,
    }));
    session.result.groupSummary.genuineCorrect = 30;

    expect(presentXingceResult(session)?.group).toMatchObject({
      outcome: "attained",
      outcomeLabel: "30 题训练已达成",
    });
  });

  it("结果摘要只使用真实作答时长、超时和信心证据", () => {
    const session = submittedXingceSession("timeout");
    session.totalElapsedSeconds = 336;
    const result = presentXingceResult(session);
    expect(result?.metrics).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ code: "elapsed", value: "05:36" }),
        expect.objectContaining({ code: "overtime", value: "3", tone: "warning" }),
      ]),
    );
    // Figma 13:64：信心校准按 信心 × 对错 分桶，只展示出现过的桶。
    expect(result?.confidenceCalibration).toEqual(
      expect.arrayContaining([expect.objectContaining({ code: "confident_incorrect", value: 3 })]),
    );
    // Figma 13:23 的「改答」列必须存在，但没有动作事件流时只能展示真实空态。
    const changedMetric = result?.metrics.find((item) => item.code === "changed");
    expect(changedMetric?.label).toBe("改答");
    expect(changedMetric?.value).toBe("—");
  });

  it("逐题复盘只展示会话中真实存在的题干、材料和作答轨迹", () => {
    const session = submittedXingceSession("timeout");
    const firstQuestion = session.bundle.questions[0];
    const firstAnswer = session.answers[0];
    if (!firstQuestion || !firstAnswer) throw new Error("fixture question and answer are required");
    session.bundle.bundleType = "shared_material";
    session.bundle.materials = [
      {
        materialId: "material-1",
        title: "共享材料",
        text: "可核对的共享材料",
        html: null,
        assets: [],
        content: [],
        anchors: [],
      },
    ];
    session.bundle.questions[0] = {
      ...firstQuestion,
      materialId: "material-1",
      stemContent: [],
    };
    session.answers[0] = {
      ...firstAnswer,
      eliminatedOptionIds: ["C"],
      marked: true,
    };

    const item = presentXingceResult(session)?.items[0];
    expect(item?.stemContent).toEqual([
      expect.objectContaining({ type: "paragraph", text: "以下说法正确的是？" }),
    ]);
    expect(item?.material?.content).toEqual([
      expect.objectContaining({ type: "paragraph", text: "可核对的共享材料" }),
    ]);
    expect(item?.factChips.map((chip) => chip.label)).toEqual(
      expect.arrayContaining(["曾排除了 C", "标记存疑"]),
    );
    expect(item?.factChips.some((chip) => chip.label.includes("改答"))).toBe(false);
  });

  it.each([
    ["passed", "valid", "复测通过", "记录有效"],
    ["not_passed", "valid", "复测未通过", "记录有效"],
    ["disputed_or_invalidated", "pending_dispute", "复测结果待核验", "争议处理中"],
    ["disputed_or_invalidated", "confirmed_invalidated", "复测结果待核验", "记录已确认失效"],
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

  it("逐题候选 chip 纯展示：候选映射中文错因，证据不足显式标注不硬给", () => {
    const candidate: XingceCauseCandidate = {
      ...causeCandidate("candidate_ready", xingceIds.candidate),
      causeCode: "stem_misread",
      summary: "漏看了题干中的关键限定词",
      questionCandidates: [
        {
          questionId: "q-1",
          status: "candidate",
          causeCode: "calculation_error",
          confidence: "medium",
          evidenceRefs: ["assessment:1"],
          rationale: "草稿与改答记录支持计算失误。",
        },
        {
          questionId: "q-2",
          status: "evidence_insufficient",
          evidenceRefs: ["assessment:2"],
          rationale: "该题没有过程证据，不硬给错因。",
        },
        {
          questionId: "q-unmapped",
          status: "candidate",
          causeCode: "future_taxonomy_code",
          confidence: "high",
          evidenceRefs: ["assessment:3"],
          rationale: "未知编码原样展示，不在端上翻译或猜测。",
        },
      ],
    };
    const view = presentXingceCauseCandidate(candidate, [
      { questionId: "q-1", positionNumber: 1 },
      { questionId: "q-2", positionNumber: 2 },
    ]);
    expect(view.stateLabel).toBe("岸岸已形成本次判断");
    expect(view.stateDescription).toContain("直接按这个判断继续");
    expect(view.candidateVersionLabel).toBe("本次岸岸判断");
    expect(view.canConfirm).toBe(true);
    expect(view.canDecide).toBe(true);
    expect(view.questionCandidates).toEqual([
      {
        questionId: "q-1",
        positionLabel: "第 1 题",
        status: "candidate",
        causeLabel: "方法成立但计算、单位或抄写错误",
        confidenceLabel: "判断信心 中",
        rationale: "草稿与改答记录支持计算失误。",
      },
      {
        questionId: "q-2",
        positionLabel: "第 2 题",
        status: "evidence_insufficient",
        causeLabel: "证据不足",
        confidenceLabel: "",
        rationale: "该题没有过程证据，不硬给错因。",
      },
      {
        questionId: "q-unmapped",
        positionLabel: "本组题目",
        status: "candidate",
        causeLabel: "future_taxonomy_code",
        confidenceLabel: "判断信心 高",
        rationale: "未知编码原样展示，不在端上翻译或猜测。",
      },
    ]);

    // 老候选没有 questionCandidates 字段：保持显式空，不推断补全。
    expect(
      presentXingceCauseCandidate(causeCandidate("candidate_ready", xingceIds.candidate))
        .questionCandidates,
    ).toEqual([]);
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
    quotaState: "reserved",
    entitlementPolicyVersion: "metered-model-round@v1",
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
}

function inputEvent(value: string): WechatMiniprogram.Input {
  return { detail: { value } } as WechatMiniprogram.Input;
}
