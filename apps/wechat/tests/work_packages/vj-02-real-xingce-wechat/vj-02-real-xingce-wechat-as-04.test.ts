import { afterEach, describe, expect, it, vi } from "vitest";

import { environment } from "../../../miniprogram/config/environment";
import {
  apiSuccess,
  completedXingceSession,
  createMemoryWechatStorage,
  reflectedXingceSession,
  submittedXingceSession,
  xingceIds,
} from "./harness";

const originalDataMode = environment.dataMode;

afterEach(() => {
  environment.dataMode = originalDataMode;
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("VJ-02 real xingce WeChat AS-04", () => {
  it("保留用户原话，展示岸岸候选，并绑定候选版本替换错因", async () => {
    let captured: CapturedReflectionPage | undefined;
    const { wxStorage } = createMemoryWechatStorage();
    const showToast = vi.fn<(options: WechatMiniprogram.ShowToastOption) => Promise<void>>(() =>
      Promise.resolve(),
    );
    const switchTab = vi.fn(() => Promise.resolve());
    const writes: WechatMiniprogram.RequestOption[] = [];
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      if (options.method === "GET") {
        options.success?.(apiSuccess(submittedXingceSession("incorrect")));
        return;
      }
      writes.push(options);
      if (options.url.endsWith("/reflection")) {
        options.success?.(apiSuccess(reflectedXingceSession("incorrect")));
        return;
      }
      options.success?.(apiSuccess(completedXingceSession("replace")));
    });
    vi.stubGlobal("Page", (definition: CapturedReflectionPage) => {
      captured = definition;
    });
    vi.stubGlobal("getApp", () => ({
      globalData: { accessToken: "real-token", userId: "user-a" },
    }));
    vi.stubGlobal("wx", {
      ...wxStorage,
      request,
      showToast,
      switchTab,
      navigateBack: vi.fn(() => Promise.resolve()),
      redirectTo: vi.fn(() => Promise.resolve()),
      showModal: vi.fn(() => Promise.resolve({ confirm: true, cancel: false })),
      getRandomValues: () => Promise.resolve({ randomValues: new Uint8Array(16).fill(7).buffer }),
    });
    environment.dataMode = "api";
    await import("../../../miniprogram/pages/reflection/index");
    if (!captured) throw new Error("reflection page was not registered");
    const page = instantiateReflectionPage(captured);

    page.onLoad.call(page, { sessionId: xingceIds.session, reviewId: xingceIds.review });
    await vi.waitFor(() => {
      expect(page.data.status).toBe("ready");
    });
    expect(page.data.phase).toBe("reflection");
    expect(page.data.result).toMatchObject({ correctCount: 0, totalCount: 3 });

    await page.submitReflection.call(page);
    expect(showToast.mock.calls[0]?.[0].title).toContain("真实想法");
    expect(writes).toHaveLength(0);

    page.inputRawText.call(page, inputEvent("我把‘不符合’看成了‘符合’。"));
    page.toggleTag.call(page, tapEvent("misread"));
    await page.submitReflection.call(page);

    expect(page.data.phase).toBe("cause_decision");
    expect(writes[0]?.method).toBe("PUT");
    const reflectionData: unknown = writes[0]?.data;
    expect(reflectionData).toEqual({
      expectedRevision: 1,
      rawText: "我把‘不符合’看成了‘符合’。",
      tags: ["misread"],
      itemInterpretations: [],
    });
    const reflectionHeaders: unknown = writes[0]?.header;
    expect(idempotencyHeader(reflectionHeaders)).toMatch(/^xingce-reflection-/u);
    expect(reflectedXingceSession().review?.aiCandidate).toMatchObject({
      state: "candidate_ready",
      candidateVersionId: xingceIds.candidate,
      summary: "题干方向识别错误",
      quotaState: "not_metered",
    });

    page.inputReplacementCause.call(page, inputEvent("题干方向识别错误"));
    await page.replaceCause.call(page);
    expect(page.data.phase).toBe("completed");
    expect(writes[1]).toMatchObject({
      method: "PUT",
      data: {
        expectedRevision: 2,
        candidateVersionId: xingceIds.candidate,
        decision: "replace",
        replacementCause: "题干方向识别错误",
      },
    });

    page.returnToday.call(page);
    expect(switchTab).toHaveBeenCalledWith({ url: "/pages/today/index" });
    page.onUnload.call(page);
  });

  it.each(["timeout", "guessed_correct"] as const)(
    "%s results retain the mandatory review and explicit reject decision path",
    (resultCode) => {
      const submitted = submittedXingceSession(resultCode);
      expect(submitted.state).toBe("reflection_required");
      expect(submitted.review).toMatchObject({
        state: "reflection_required",
        aiCandidate: { state: "unavailable", quotaState: "not_metered" },
      });
      const rejected = completedXingceSession("reject");
      expect(rejected.review?.causeDecision).toEqual(
        expect.objectContaining({
          candidateVersionId: xingceIds.candidate,
          decision: "reject",
          replacementCause: null,
        }),
      );
      expect(rejected.completion?.receiptId).toBe(xingceIds.receipt);
    },
  );
});

interface CapturedReflectionPage {
  data: Record<string, unknown>;
  onLoad(this: TestReflectionPage, options: Record<string, string | undefined>): void;
  onUnload(this: TestReflectionPage): void;
  submitReflection(this: TestReflectionPage): Promise<void>;
  inputRawText(this: TestReflectionPage, event: WechatMiniprogram.Input): void;
  toggleTag(this: TestReflectionPage, event: WechatMiniprogram.BaseEvent): void;
  inputReplacementCause(this: TestReflectionPage, event: WechatMiniprogram.Input): void;
  replaceCause(this: TestReflectionPage): Promise<void>;
  returnToday(this: TestReflectionPage): void;
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

function inputEvent(value: string): WechatMiniprogram.Input {
  return { detail: { value } } as WechatMiniprogram.Input;
}

function tapEvent(code: string): WechatMiniprogram.BaseEvent {
  return { currentTarget: { dataset: { code } } } as unknown as WechatMiniprogram.BaseEvent;
}

function idempotencyHeader(value: unknown): string {
  if (typeof value !== "object" || value === null) return "";
  const header = (value as Record<string, unknown>)["Idempotency-Key"];
  return typeof header === "string" ? header : "";
}
