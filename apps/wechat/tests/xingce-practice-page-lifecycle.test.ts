import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  XingceCheckpointCommand,
  XingceTaskSession,
} from "../miniprogram/api/xingce-learning";
import { environment } from "../miniprogram/config/environment";
import {
  activeXingceSession,
  apiSuccess,
  createMemoryWechatStorage,
  xingceIds,
} from "./work_packages/vj-02-real-xingce-wechat/harness";

const originalDataMode = environment.dataMode;

afterEach(() => {
  environment.dataMode = originalDataMode;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("xingce practice page lifecycle", () => {
  it("keeps the timer stopped after a hidden-page checkpoint refresh", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage();

    expect(vi.getTimerCount()).toBe(1);
    runtime.page.inputDraft.call(runtime.page, inputEvent("我需要重新检查排除理由。"));
    runtime.page.onHide.call(runtime.page);
    await flushMicrotasks();

    expect(runtime.requests.filter((item) => item.method === "PUT")).toHaveLength(1);
    expect(runtime.requests.filter((item) => item.method === "GET")).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(0);
    runtime.page.onUnload.call(runtime.page);
  });

  it("ignores a late checkpoint refresh after the page is unloaded", async () => {
    vi.useFakeTimers();
    let releaseRefresh: (() => void) | undefined;
    const runtime = await createPracticePage((options, session) => {
      if (options.method !== "GET") return false;
      releaseRefresh = () => options.success?.(apiSuccess(session));
      return true;
    });

    runtime.page.inputDraft.call(runtime.page, inputEvent("离页前保存这段草稿。"));
    runtime.page.onHide.call(runtime.page);
    await flushMicrotasks();
    expect(releaseRefresh).toBeTypeOf("function");

    runtime.page.onUnload.call(runtime.page);
    const writesBeforeLateResponse = runtime.setData.mock.calls.length;
    releaseRefresh?.();
    await flushMicrotasks();

    expect(runtime.setData).toHaveBeenCalledTimes(writesBeforeLateResponse);
    expect(vi.getTimerCount()).toBe(0);
  });
});

type RequestInterceptor = (
  options: WechatMiniprogram.RequestOption,
  session: XingceTaskSession,
) => boolean;

async function createPracticePage(intercept?: RequestInterceptor) {
  let captured: CapturedPracticePage | undefined;
  let session = activeXingceSession();
  const requests: WechatMiniprogram.RequestOption[] = [];
  const { wxStorage } = createMemoryWechatStorage();
  const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
    requests.push(options);
    if (options.method === "POST") {
      options.success?.(apiSuccess(session));
      return;
    }
    if (options.method === "PUT") {
      const command = options.data as XingceCheckpointCommand;
      session = activeXingceSession({
        revision: command.expectedRevision + 1,
        answers: command.answers,
      });
      session = {
        ...session,
        currentQuestionId: command.currentQuestionId,
        totalElapsedSeconds: command.totalElapsedSeconds,
      };
      options.success?.(apiSuccess(session));
      return;
    }
    if (intercept?.(options, session)) return;
    options.success?.(apiSuccess(session));
  });
  vi.stubGlobal("Page", (definition: CapturedPracticePage) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({
    globalData: { accessToken: "real-token", userId: "user-a" },
  }));
  vi.stubGlobal("wx", {
    ...wxStorage,
    request,
    getRandomValues: () => Promise.resolve({ randomValues: new Uint8Array(16).fill(9).buffer }),
    showToast: vi.fn(() => Promise.resolve()),
    showModal: vi.fn(() => Promise.resolve({ confirm: true, cancel: false })),
    navigateBack: vi.fn(() => Promise.resolve()),
    redirectTo: vi.fn(() => Promise.resolve()),
    switchTab: vi.fn(() => Promise.resolve()),
  });
  environment.dataMode = "api";
  await import("../miniprogram/pages/practice/index");
  if (!captured) throw new Error("practice page was not registered");
  const setData = vi.fn(function (this: TestPracticePage, patch: Record<string, unknown>) {
    Object.assign(this.data, patch);
  });
  const page: TestPracticePage = {
    ...captured,
    data: JSON.parse(JSON.stringify(captured.data)) as Record<string, unknown>,
    setData,
  };
  page.onLoad.call(page, { mode: "targeted", planTaskId: xingceIds.planTask });
  page.onShow.call(page);
  await flushMicrotasks();
  expect(page.data.status).toBe("ready");
  return { page, requests, setData };
}

interface CapturedPracticePage {
  data: Record<string, unknown>;
  onLoad(this: TestPracticePage, options: Record<string, string | undefined>): void;
  onShow(this: TestPracticePage): void;
  onHide(this: TestPracticePage): void;
  onUnload(this: TestPracticePage): void;
  loadTask(this: TestPracticePage): Promise<void>;
  inputDraft(this: TestPracticePage, event: WechatMiniprogram.Input): void;
}

interface TestPracticePage extends CapturedPracticePage {
  data: Record<string, unknown>;
  setData(patch: Record<string, unknown>): void;
}

function inputEvent(value: string): WechatMiniprogram.Input {
  return { detail: { value } } as WechatMiniprogram.Input;
}

async function flushMicrotasks(): Promise<void> {
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
}
