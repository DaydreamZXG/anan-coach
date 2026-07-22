import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../miniprogram/api/client";
import {
  XingceLearningApi,
  type XingceBriefingMode,
  type XingceTaskBriefing,
} from "../miniprogram/api/xingce-learning";
import { environment } from "../miniprogram/config/environment";
import { assertXingceTaskBriefing } from "../miniprogram/features/xingce/briefing/contracts";
import { apiSuccess } from "./work_packages/vj-02-real-xingce-wechat/harness";

const originalDataMode = environment.dataMode;

afterEach(() => {
  environment.dataMode = originalDataMode;
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("Xingce task briefing API and contract", () => {
  it("reads the owned briefing endpoint without creating a task session", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    vi.stubGlobal("wx", {
      request: vi.fn((options: WechatMiniprogram.RequestOption) => {
        requests.push(options);
        options.success?.(apiSuccess(briefing()));
      }),
    });
    const api = new XingceLearningApi(new ApiClient("https://api.example.test", "token"));

    await api.getTaskBriefing("plan/a");

    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({
      method: "GET",
      url: "https://api.example.test/api/v2/xingce/plan-tasks/plan%2Fa/briefing",
    });
    expect(requests[0]?.url).not.toContain("task-sessions");
  });

  it("rejects incomplete evidence and verification source contracts", () => {
    const missingMetric = briefing();
    missingMetric.completion.requiredMetrics = ["answer_verdict"];
    expect(() => assertXingceTaskBriefing(missingMetric, "plan-a")).toThrow("验收证据");

    const verification = briefing({
      mode: "verification",
      verificationStage: null,
      sourceMasteryVersionId: null,
    });
    expect(() => assertXingceTaskBriefing(verification, "plan-a")).toThrow("来源掌握版本");
  });
});

describe("C01 xingce task intro page", () => {
  it("shows the frozen three-question contract and starts only with the server briefing mode", async () => {
    const runtime = await createPage((options) => {
      expect(options.method).toBe("GET");
      options.success?.(apiSuccess(briefing({ mode: "targeted" })));
    }, "verification");

    expect(runtime.initialStatus).toBe("default");
    expect(runtime.requests).toHaveLength(1);
    expect(runtime.page.data).toMatchObject({ status: "ready", recovered: false });
    expect(runtime.page.data.view).toMatchObject({
      modeLabel: "针对训练",
      questionCountLabel: "3 题",
      targetTimeLabel: "6 分钟",
      perQuestionTimeLabel: "2 分钟/题",
    });
    expect(runtime.page.data.view?.evidenceItems.map((item) => item.label)).toEqual([
      "客观对错",
      "作答信心",
      "单题用时",
      "用户原话复盘",
      "用户确认的错因决定",
    ]);

    await runtime.page.startTask.call(runtime.page);

    expect(runtime.requests).toHaveLength(1);
    expect(runtime.navigateTo).toHaveBeenCalledWith({
      url: "/pages/practice/index?mode=targeted&planTaskId=plan-a",
    });
  });

  it("renders the service-owned verification stage and source mastery version", async () => {
    const runtime = await createPage((options) => {
      options.success?.(
        apiSuccess(
          briefing({
            mode: "verification",
            verificationStage: "delayed_review",
            sourceMasteryVersionId: "mastery-version-20260716",
          }),
        ),
      );
    }, "targeted");

    expect(runtime.page.data.view?.modeLabel).toBe("独立复测");
    expect(runtime.page.data.view?.verificationStageLabel).toBe("7–14 天延时验证");
    expect(runtime.page.data.view?.sourceMasteryLabel).toContain("mastery-");
    await runtime.page.startTask.call(runtime.page);
    expect(runtime.navigateTo).toHaveBeenCalledWith({
      url: "/pages/practice/index?mode=verification&planTaskId=plan-a",
    });
  });

  it("shows client-clock expiry without starting and treats a service 409 as authoritative", async () => {
    let requestNo = 0;
    const runtime = await createPage(
      (options) => {
        requestNo += 1;
        if (requestNo === 1) {
          options.success?.(apiSuccess(briefing({ validUntil: "2020-01-01T00:00:00Z" })));
          return;
        }
        options.success?.(
          apiFailure(409, "xingce_briefing_unavailable", "任务已过期", "briefing-409"),
        );
      },
      "targeted",
      { waitForReady: false },
    );
    await flushMicrotasks();

    expect(runtime.page.data.status).toBe("expired");
    expect(runtime.page.data.errorMessage).toContain("以最新结果为准");
    await runtime.page.startTask.call(runtime.page);
    expect(runtime.navigateTo).not.toHaveBeenCalled();

    runtime.page.retry.call(runtime.page);
    await flushMicrotasks();
    expect(runtime.page.data).toMatchObject({
      status: "expired",
      requestId: "briefing-409",
    });
    expect(runtime.page.data.errorMessage).toContain("该任务已过期");
  });

  it("recovers a failed read and marks the restored authority", async () => {
    let requestNo = 0;
    const runtime = await createPage(
      (options) => {
        requestNo += 1;
        if (requestNo === 1) {
          options.success?.(
            apiFailure(503, "briefing-temporary", "说明暂不可用", "briefing-first"),
          );
          return;
        }
        options.success?.(apiSuccess(briefing()));
      },
      "targeted",
      { waitForReady: false },
    );
    await flushMicrotasks();
    expect(runtime.page.data).toMatchObject({
      status: "error",
      requestId: "briefing-first",
    });

    runtime.page.retry.call(runtime.page);
    await flushMicrotasks();
    expect(runtime.page.data).toMatchObject({ status: "ready", recovered: true });
  });

  it("isolates late responses across account changes and unload", async () => {
    const releases: Array<() => void> = [];
    const runtime = await createPage(
      (options) => {
        if (authorization(options.header) === "Bearer token-b") {
          options.success?.(apiSuccess(briefing({ title: "用户 B 的任务" })));
          return;
        }
        releases.push(() => options.success?.(apiSuccess(briefing({ title: "旧账号迟到任务" }))));
      },
      "targeted",
      { waitForReady: false },
    );

    runtime.globalData.accessToken = "token-b";
    runtime.globalData.userId = "user-b";
    runtime.page.onShow.call(runtime.page);
    await flushMicrotasks();
    expect(runtime.page.data.view?.title).toBe("用户 B 的任务");
    expect(runtime.requests.at(-1)?.header).toMatchObject({ Authorization: "Bearer token-b" });

    releases[0]?.();
    await flushMicrotasks();
    expect(runtime.page.data.view?.title).toBe("用户 B 的任务");

    runtime.page.onUnload.call(runtime.page);
    const writesBeforeLate = runtime.setData.mock.calls.length;
    releases.at(-1)?.();
    await flushMicrotasks();
    expect(runtime.setData).toHaveBeenCalledTimes(writesBeforeLate);
  });

  it("rejects incomplete routes without issuing a network request", async () => {
    const runtime = await createPage(
      (options) => {
        options.success?.(apiSuccess(briefing()));
      },
      "targeted",
      { planTaskId: "", waitForReady: false },
    );
    await flushMicrotasks();
    expect(runtime.page.data.status).toBe("error");
    expect(runtime.requests).toHaveLength(0);
  });
});

interface CapturedIntroPage {
  data: Record<string, unknown>;
  onLoad(this: TestIntroPage, options: Record<string, string | undefined>): void;
  onShow(this: TestIntroPage): void;
  onUnload(this: TestIntroPage): void;
  retry(this: TestIntroPage): void;
  startTask(this: TestIntroPage): Promise<void>;
}

interface IntroPageData extends Record<string, unknown> {
  status: string;
  view: {
    title: string;
    modeLabel: string;
    questionCountLabel: string;
    targetTimeLabel: string;
    perQuestionTimeLabel: string;
    evidenceItems: Array<{ code: string; label: string }>;
    verificationStageLabel: string;
    sourceMasteryLabel: string;
  } | null;
  errorMessage: string;
  requestId: string;
  recovered: boolean;
}

interface TestIntroPage extends CapturedIntroPage {
  data: IntroPageData;
  setData(patch: Record<string, unknown>): void;
}

type RequestHandler = (options: WechatMiniprogram.RequestOption) => void;

async function createPage(
  handleRequest: RequestHandler,
  entryMode: XingceBriefingMode,
  options: { planTaskId?: string; waitForReady?: boolean } = {},
) {
  let captured: CapturedIntroPage | undefined;
  const requests: WechatMiniprogram.RequestOption[] = [];
  const globalData: { accessToken?: string; userId?: string } = {
    accessToken: "token-a",
    userId: "user-a",
  };
  const navigateTo = vi.fn(() => Promise.resolve());
  const request = vi.fn((requestOptions: WechatMiniprogram.RequestOption) => {
    requests.push(requestOptions);
    handleRequest(requestOptions);
  });
  vi.stubGlobal("Page", (definition: CapturedIntroPage) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({ globalData }));
  vi.stubGlobal("wx", {
    request,
    navigateTo,
    redirectTo: vi.fn(() => Promise.resolve()),
    switchTab: vi.fn(() => Promise.resolve()),
    getStorageSync: vi.fn(() => ""),
    removeStorageSync: vi.fn(),
  });
  environment.dataMode = "api";
  await import("../miniprogram/pages/xingce-task-intro/index");
  if (!captured) throw new Error("task intro page was not registered");
  const initialStatusValue: unknown = captured.data.status;
  const initialStatus = typeof initialStatusValue === "string" ? initialStatusValue : "";
  const setData = vi.fn(function (this: TestIntroPage, patch: Record<string, unknown>) {
    Object.assign(this.data, patch);
  });
  const page: TestIntroPage = {
    ...captured,
    data: JSON.parse(JSON.stringify(captured.data)) as IntroPageData,
    setData,
  };
  page.onLoad.call(page, {
    planTaskId: options.planTaskId ?? "plan-a",
    mode: entryMode,
  });
  if (options.waitForReady !== false) {
    await vi.waitFor(() => {
      expect(page.data.status).toBe("ready");
    });
  }
  return { page, requests, navigateTo, setData, globalData, initialStatus };
}

function briefing(overrides: Partial<XingceTaskBriefing> = {}): XingceTaskBriefing {
  return {
    contractVersion: "xingce-task-briefing@v1",
    planTaskId: "plan-a",
    planVersionId: "plan-version-20260716",
    taskDefinitionVersion: "a".repeat(64),
    mode: "targeted",
    title: "资料分析专项",
    reason: "到期证据优先，完成三题后进入复盘。",
    moduleName: "资料分析",
    questionCount: 3,
    timing: {
      behavior: "advisory",
      targetSeconds: 360,
      perQuestionTargetSeconds: 120,
      overtimeIsRecorded: true,
    },
    completion: {
      evidenceType: "objective_closure",
      requiredMetrics: [
        "answer_verdict",
        "confidence_evidence",
        "timing_evidence",
        "reflection",
        "cause_decision",
      ],
      stableMasteryRequiresDelayedVerification: true,
    },
    abandonment: {
      allowed: true,
      semantics: "active_abandon_is_not_automatic_error",
      requiresExplicitAction: true,
    },
    verificationStage: null,
    sourceMasteryVersionId: null,
    validUntil: "2099-07-16T12:00:00+08:00",
    ...overrides,
  };
}

function apiFailure(
  statusCode: number,
  code: string,
  message: string,
  requestId: string,
): WechatMiniprogram.RequestSuccessCallbackResult {
  return {
    statusCode,
    data: { code, message, requestId },
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  };
}

function authorization(header: unknown): string {
  if (typeof header !== "object" || header === null) return "";
  const value = (header as Record<string, unknown>).Authorization;
  return typeof value === "string" ? value : "";
}

async function flushMicrotasks(): Promise<void> {
  for (let index = 0; index < 16; index += 1) await Promise.resolve();
}
