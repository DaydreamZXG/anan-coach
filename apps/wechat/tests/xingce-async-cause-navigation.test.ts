import { afterEach, describe, expect, it, vi } from "vitest";

import type { XingceTaskSession } from "../miniprogram/api/xingce-learning";
import {
  apiSuccess,
  createMemoryWechatStorage,
  reflectedXingceSession,
  submittedXingceSession,
  xingceIds,
} from "./work_packages/vj-02-real-xingce-wechat/harness";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("async cause navigation", () => {
  it("提交复盘后立即回到今日，错因在后台继续整理", async () => {
    let captured: CapturedReflectionPage | undefined;
    const { wxStorage } = createMemoryWechatStorage();
    const writes: WechatMiniprogram.RequestOption[] = [];
    const showToast = vi.fn(() => Promise.resolve());
    const switchTab = vi.fn(() => Promise.resolve());
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      if (options.method === "GET") {
        options.success?.(apiSuccess(submittedXingceSession("incorrect")));
        return;
      }
      writes.push(options);
      options.success?.(apiSuccess(processingSession()));
    });
    stubPage(wxStorage, request, showToast, switchTab, (definition) => {
      captured = definition;
    });
    await import("../miniprogram/pages/reflection/index");
    if (!captured) throw new Error("reflection page was not registered");
    const page = instantiate(captured);

    page.onLoad.call(page, { sessionId: xingceIds.session, reviewId: xingceIds.review });
    await vi.waitFor(() => {
      expect(page.data.status).toBe("ready");
    });
    page.startReflection.call(page);
    await page.submitReflection.call(page);

    expect(writes).toHaveLength(1);
    expect(page.data.phase).toBe("cause_processing");
    expect(showToast).toHaveBeenCalledWith({ title: "岸岸会在后台整理", icon: "none" });
    expect(switchTab).toHaveBeenCalledWith({ url: "/pages/today/index" });
    page.onUnload.call(page);
  });

  it("切回今日失败也不把已成功的复盘写入重报成失败", async () => {
    let captured: CapturedReflectionPage | undefined;
    const { wxStorage } = createMemoryWechatStorage();
    const writes: WechatMiniprogram.RequestOption[] = [];
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      if (options.method === "GET") {
        options.success?.(apiSuccess(submittedXingceSession("incorrect")));
        return;
      }
      writes.push(options);
      options.success?.(apiSuccess(processingSession()));
    });
    stubPage(
      wxStorage,
      request,
      vi.fn(() => Promise.resolve()),
      vi.fn(() => {
        throw new Error("navigation unavailable");
      }),
      (definition) => {
        captured = definition;
      },
    );
    await import("../miniprogram/pages/reflection/index");
    if (!captured) throw new Error("reflection page was not registered");
    const page = instantiate(captured);

    page.onLoad.call(page, { sessionId: xingceIds.session, reviewId: xingceIds.review });
    await vi.waitFor(() => {
      expect(page.data.status).toBe("ready");
    });
    page.startReflection.call(page);
    await expect(page.submitReflection.call(page)).resolves.toBeUndefined();

    expect(writes).toHaveLength(1);
    expect(page.data.phase).toBe("cause_processing");
    expect(page.data.errorMessage).toBe("");
    page.onUnload.call(page);
  });
});

function processingSession(): XingceTaskSession {
  const session = reflectedXingceSession();
  if (!session.review) throw new Error("fixture review is required");
  session.state = "cause_candidate_processing";
  session.review.state = "cause_candidate_pending";
  session.review.aiCandidate = {
    ...session.review.aiCandidate,
    state: "processing",
    candidateVersionId: null,
    causeCode: null,
    summary: null,
    improvement: null,
    rationale: null,
    evidenceRefs: [],
    quotaState: "reserved",
  };
  return session;
}

interface CapturedReflectionPage {
  data: Record<string, unknown>;
  onLoad(this: TestPage, options: Record<string, string | undefined>): void;
  onUnload(this: TestPage): void;
  startReflection(this: TestPage): void;
  submitReflection(this: TestPage): Promise<void>;
}

interface TestPage extends CapturedReflectionPage {
  setData(patch: Record<string, unknown>): void;
}

function instantiate(definition: CapturedReflectionPage): TestPage {
  return {
    ...definition,
    data: JSON.parse(JSON.stringify(definition.data)) as Record<string, unknown>,
    setData(patch) {
      Object.assign(this.data, patch);
    },
  };
}

function stubPage(
  wxStorage: ReturnType<typeof createMemoryWechatStorage>["wxStorage"],
  request: ReturnType<typeof vi.fn>,
  showToast: ReturnType<typeof vi.fn>,
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
    showToast,
    switchTab,
    navigateBack: vi.fn(() => Promise.resolve()),
    redirectTo: vi.fn(() => Promise.resolve()),
    showModal: vi.fn(() => Promise.resolve({ confirm: true, cancel: false })),
    getRandomValues: () => Promise.resolve({ randomValues: new Uint8Array(16).fill(7).buffer }),
  });
}
