import { afterEach, describe, expect, it, vi } from "vitest";

import type { XingceTaskSession } from "../miniprogram/xingce/shared/api/xingce-learning";
import {
  presentXingceImmediateVerification,
  presentXingceReflectionClosure,
} from "../miniprogram/pages/practice/shared/features/xingce/real_attempt/presentation";
import {
  apiSuccess,
  completedXingceSession,
  createMemoryWechatStorage,
  xingceIds,
} from "./work_packages/vj-02-real-xingce-wechat/harness";

const verificationTaskId = "81000000-0000-4000-8000-000000000001";
const verificationUrl = `/xingce/xingce-task-intro/index?mode=verification&planTaskId=${verificationTaskId}`;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("PF-001 accepted correction projection", () => {
  it("confirm 只呈现已确认候选的一条纠偏和一个动作", () => {
    const session = confirmedCandidateSession();

    expect(presentXingceReflectionClosure(session)).toEqual({
      acceptedCorrection: {
        source: "confirmed_candidate",
        correction: "题干方向识别错误",
        action: "先圈出题目问法再看选项。",
      },
      statusTitle: "这次只改一个动作",
      statusDescription: "先按已确认的动作做一遍，再用独立新题验证，不把一次答对当成已经掌握。",
    });
  });

  it("replace 只采用 replacementCause 和用户 strategyNote，不回显被替换候选", () => {
    const session = completedXingceSession("replace");
    const review = requiredReview(session);
    if (!review.reflection || !review.causeDecision) {
      throw new Error("fixture completion requires reflection and decision");
    }
    review.aiCandidate.summary = "这条候选已经被用户改写，不能继续展示";
    review.aiCandidate.improvement = "候选提供的动作也不能继续展示";
    review.causeDecision.replacementCause = "我没有先确认题目问的是正确项还是错误项";
    review.reflection.strategyNote = "读题后先用自己的话复述问题方向";

    const closure = presentXingceReflectionClosure(session);
    expect(closure.acceptedCorrection).toEqual({
      source: "user_replacement",
      correction: "我没有先确认题目问的是正确项还是错误项",
      action: "读题后先用自己的话复述问题方向",
    });
    expect(JSON.stringify(closure)).not.toContain("这条候选已经被用户改写");
    expect(JSON.stringify(closure)).not.toContain("候选提供的动作");
  });

  it("replace 未保存用户动作时不拿候选 improvement 补写", () => {
    const session = completedXingceSession("replace");
    const review = requiredReview(session);
    if (!review.reflection) throw new Error("fixture completion requires reflection");
    review.reflection.strategyNote = null;
    review.aiCandidate.improvement = "不能被端上拿来补写的候选动作";

    expect(presentXingceReflectionClosure(session).acceptedCorrection).toBeNull();
  });

  it("reject 和 defer 都不把候选包装成确认纠偏", () => {
    const rejected = completedXingceSession("reject");
    const deferred = completedXingceSession("reject");
    const deferredDecision = requiredReview(deferred).causeDecision;
    if (!deferredDecision) throw new Error("fixture completion requires decision");
    deferredDecision.decision = "defer";

    expect(presentXingceReflectionClosure(rejected)).toMatchObject({
      acceptedCorrection: null,
      statusTitle: "这次没有形成确认纠偏",
    });
    expect(presentXingceReflectionClosure(deferred)).toMatchObject({
      acceptedCorrection: null,
      statusTitle: "这次没有形成确认纠偏",
    });
  });

  it("即使异常结算声称 confirm，证据不足候选也不会成为确认纠偏", () => {
    const session = confirmedCandidateSession();
    const review = requiredReview(session);
    review.aiCandidate.state = "evidence_insufficient";
    review.aiCandidate.summary = "不应展示的证据不足候选";
    review.aiCandidate.improvement = "不应展示的候选动作";

    expect(presentXingceReflectionClosure(session)).toMatchObject({
      acceptedCorrection: null,
      statusTitle: "这次没有形成确认纠偏",
    });
  });

  it("confirm 的结算版本与当前候选不一致时不展示确认纠偏", () => {
    const session = confirmedCandidateSession();
    const decision = requiredReview(session).causeDecision;
    if (!decision) throw new Error("fixture completion requires decision");
    decision.candidateVersionId = "55000000-0000-4000-8000-000000000099";

    expect(presentXingceReflectionClosure(session).acceptedCorrection).toBeNull();
  });
});

describe("PF-001 immediate verification target", () => {
  it("只选择 completion plan 内 ready 的行测 objective_verification 任务", () => {
    const session = withVerificationTask(completedXingceSession("replace"));

    expect(presentXingceImmediateVerification(session)).toEqual({
      mode: "navigateTo",
      url: verificationUrl,
    });
  });

  it("reject、defer、证据不足和无确认版本即使带复测任务也安全回退", () => {
    const rejected = withVerificationTask(completedXingceSession("reject"));
    const deferred = withVerificationTask(completedXingceSession("reject"));
    const deferredDecision = requiredReview(deferred).causeDecision;
    if (!deferredDecision) throw new Error("fixture completion requires decision");
    deferredDecision.decision = "defer";

    const insufficient = withVerificationTask(confirmedCandidateSession());
    requiredReview(insufficient).aiCandidate.state = "evidence_insufficient";

    const unconfirmed = withVerificationTask(confirmedCandidateSession());
    const unconfirmedDecision = requiredReview(unconfirmed).causeDecision;
    if (!unconfirmedDecision) throw new Error("fixture completion requires decision");
    unconfirmedDecision.confirmedCauseVersionId = null;

    for (const session of [rejected, deferred, insufficient, unconfirmed]) {
      expect(presentXingceImmediateVerification(session)).toBeNull();
    }
  });

  it.each([
    ["owner", "other_owner"],
    ["entityType", "xingce_attempt_review_version"],
    ["role", "due_verification"],
    ["entityId", "61000000-0000-4000-8000-000000000099"],
    ["versionId", "61000000-0000-4000-8000-000000000099"],
  ] as const)("决策绑定字段 %s 不精确时不打开复测", (field, value) => {
    const session = withVerificationTask(confirmedCandidateSession());
    const reference = requiredFirstTask(session).evidenceRefs[0];
    if (!reference) throw new Error("fixture decision evidence is required");
    reference[field] = value;

    expect(presentXingceImmediateVerification(session)).toBeNull();
  });

  it("决策绑定缺失或重复时不猜测", () => {
    const missing = withVerificationTask(confirmedCandidateSession());
    requiredFirstTask(missing).evidenceRefs = [];
    expect(presentXingceImmediateVerification(missing)).toBeNull();

    const duplicated = withVerificationTask(confirmedCandidateSession());
    const reference = requiredFirstTask(duplicated).evidenceRefs[0];
    if (!reference) throw new Error("fixture decision evidence is required");
    requiredFirstTask(duplicated).evidenceRefs.push({ ...reference });
    expect(presentXingceImmediateVerification(duplicated)).toBeNull();
  });

  it("多个到期复测只打开绑定当前 decisionVersionId 的任务", () => {
    const session = withVerificationTask(confirmedCandidateSession());
    if (!session.completion) throw new Error("fixture completion is required");
    const current = requiredFirstTask(session);
    const oldTaskId = "81000000-0000-4000-8000-000000000002";
    const oldDecisionVersionId = "61000000-0000-4000-8000-000000000002";
    session.completion.plan.tasks = [
      {
        ...current,
        id: oldTaskId,
        startUrl: `/xingce/xingce-task-intro/index?mode=verification&planTaskId=${oldTaskId}`,
        evidenceRefs: [verificationDecisionEvidence(oldDecisionVersionId)],
      },
      current,
    ];

    expect(presentXingceImmediateVerification(session)).toEqual({
      mode: "navigateTo",
      url: verificationUrl,
    });
  });

  it("没有合格复测任务时返回空，让页面回退今日计划", () => {
    const session = completedXingceSession("replace");
    expect(presentXingceImmediateVerification(session)).toBeNull();

    const wrongStage = withVerificationTask(completedXingceSession("replace"));
    const task = requiredFirstTask(wrongStage);
    task.stage = "objective_practice";
    expect(presentXingceImmediateVerification(wrongStage)).toBeNull();

    const inProgress = withVerificationTask(completedXingceSession("replace"));
    requiredFirstTask(inProgress).status = "in_progress";
    expect(presentXingceImmediateVerification(inProgress)).toBeNull();
  });

  it.each([
    "https://evil.example/xingce/xingce-task-intro/index?mode=verification",
    `/pages/practice/index?mode=verification&planTaskId=${verificationTaskId}`,
    `/xingce/xingce-task-intro/index?mode=verification&planTaskId=${verificationTaskId}&next=https%3A%2F%2Fevil.example`,
    "/xingce/xingce-task-intro/index?mode=verification&planTaskId=another-owned-task",
    `/xingce/xingce-task-intro/index?mode=targeted&planTaskId=${verificationTaskId}`,
  ])("拒绝恶意或越权 startUrl：%s", (startUrl) => {
    const session = withVerificationTask(completedXingceSession("replace"));
    requiredFirstTask(session).startUrl = startUrl;
    expect(presentXingceImmediateVerification(session)).toBeNull();
  });
});

describe("PF-001 reflection completion page", () => {
  it("重复点击只导航一次，并且不会为即时复测新增后端或模型请求", async () => {
    let captured: CapturedReflectionPage | undefined;
    const { wxStorage } = createMemoryWechatStorage();
    const session = withVerificationTask(confirmedCandidateSession());
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      options.success?.(apiSuccess(session));
    });
    let finishNavigation: (() => void) | undefined;
    const navigateTo = vi.fn(
      () =>
        new Promise<WechatMiniprogram.GeneralCallbackResult>((resolve) => {
          finishNavigation = () => {
            resolve({ errMsg: "navigateTo:ok" });
          };
        }),
    );
    const switchTab = vi.fn(() => Promise.resolve({ errMsg: "switchTab:ok" }));
    stubReflectionPage(wxStorage, request, navigateTo, switchTab, (definition) => {
      captured = definition;
    });
    await import("../miniprogram/pages/reflection/index");
    if (!captured) throw new Error("reflection page was not registered");
    const page = instantiateReflectionPage(captured);

    page.onLoad.call(page, { sessionId: xingceIds.session, reviewId: xingceIds.review });
    await vi.waitFor(() => {
      expect(page.data.phase).toBe("completed");
      expect(page.data.hasImmediateVerification).toBe(true);
    });
    const requestCountBeforeNavigation = request.mock.calls.length;

    const first = page.startImmediateVerification.call(page);
    const repeated = page.startImmediateVerification.call(page);
    expect(navigateTo).toHaveBeenCalledTimes(1);
    expect(navigateTo).toHaveBeenCalledWith({ url: verificationUrl });
    expect(page.data.verificationNavigationLocked).toBe(true);
    expect(request).toHaveBeenCalledTimes(requestCountBeforeNavigation);
    expect(switchTab).not.toHaveBeenCalled();

    finishNavigation?.();
    await Promise.all([first, repeated]);
    expect(request).toHaveBeenCalledTimes(requestCountBeforeNavigation);
    page.onUnload.call(page);
  });

  it("无合格任务时保留返回今日计划的安全回退", async () => {
    let captured: CapturedReflectionPage | undefined;
    const { wxStorage } = createMemoryWechatStorage();
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      options.success?.(apiSuccess(confirmedCandidateSession()));
    });
    const navigateTo = vi.fn(() => Promise.resolve({ errMsg: "navigateTo:ok" }));
    const switchTab = vi.fn(() => Promise.resolve({ errMsg: "switchTab:ok" }));
    stubReflectionPage(wxStorage, request, navigateTo, switchTab, (definition) => {
      captured = definition;
    });
    await import("../miniprogram/pages/reflection/index");
    if (!captured) throw new Error("reflection page was not registered");
    const page = instantiateReflectionPage(captured);

    page.onLoad.call(page, { sessionId: xingceIds.session, reviewId: xingceIds.review });
    await vi.waitFor(() => {
      expect(page.data.phase).toBe("completed");
      expect(page.data.hasImmediateVerification).toBe(false);
    });
    const requestCountBeforeNavigation = request.mock.calls.length;

    await page.startImmediateVerification.call(page);
    expect(navigateTo).not.toHaveBeenCalled();
    expect(switchTab).toHaveBeenCalledWith({ url: "/pages/today/index" });
    expect(request).toHaveBeenCalledTimes(requestCountBeforeNavigation);
    page.onUnload.call(page);
  });
});

function confirmedCandidateSession(): XingceTaskSession {
  const session = completedXingceSession("replace");
  const review = requiredReview(session);
  const decision = review.causeDecision;
  if (!decision) throw new Error("fixture completion requires decision");
  decision.decision = "confirm";
  decision.replacementCause = null;
  decision.candidateVersionId = xingceIds.candidate;
  decision.confirmedCauseVersionId = xingceIds.decision;
  return session;
}

function withVerificationTask(session: XingceTaskSession): XingceTaskSession {
  if (!session.completion) throw new Error("fixture completion is required");
  const existing = session.completion.plan.tasks[0];
  if (!existing) throw new Error("fixture Today task is required");
  const decisionVersionId = requiredReview(session).causeDecision?.decisionVersionId;
  if (!decisionVersionId) throw new Error("fixture cause decision is required");
  session.completion.plan.tasks = [
    {
      ...existing,
      id: verificationTaskId,
      track: "xingce",
      stage: "objective_verification",
      kind: "verification",
      status: "ready",
      title: "即时独立复测",
      startUrl: verificationUrl,
      evidenceRefs: [verificationDecisionEvidence(decisionVersionId)],
    },
  ];
  return session;
}

function verificationDecisionEvidence(decisionVersionId: string) {
  return {
    owner: "objective_learning",
    entityType: "xingce_cause_decision_version",
    entityId: decisionVersionId,
    versionId: decisionVersionId,
    role: "immediate_verification_source",
  };
}

function requiredReview(session: XingceTaskSession): NonNullable<XingceTaskSession["review"]> {
  if (!session.review) throw new Error("fixture review is required");
  return session.review;
}

function requiredFirstTask(
  session: XingceTaskSession,
): NonNullable<XingceTaskSession["completion"]>["plan"]["tasks"][number] {
  const task = session.completion?.plan.tasks[0];
  if (!task) throw new Error("fixture Today task is required");
  return task;
}

interface CapturedReflectionPage {
  data: Record<string, unknown>;
  onLoad(this: TestReflectionPage, options: Record<string, string | undefined>): void;
  onUnload(this: TestReflectionPage): void;
  startImmediateVerification(this: TestReflectionPage): Promise<void>;
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
  navigateTo: ReturnType<typeof vi.fn>,
  switchTab: ReturnType<typeof vi.fn>,
  capture: (definition: CapturedReflectionPage) => void,
): void {
  vi.stubGlobal("Page", capture);
  vi.stubGlobal("getApp", () => ({
    globalData: { accessToken: "real-token", userId: "user-a" },
  }));
  vi.stubGlobal("wx", {
    ...wxStorage,
    request,
    navigateTo,
    switchTab,
    showToast: vi.fn(() => Promise.resolve()),
    navigateBack: vi.fn(() => Promise.resolve()),
    redirectTo: vi.fn(() => Promise.resolve()),
    showModal: vi.fn(() => Promise.resolve({ confirm: true, cancel: false })),
    getRandomValues: () => Promise.resolve({ randomValues: new Uint8Array(16).fill(7).buffer }),
  });
}
