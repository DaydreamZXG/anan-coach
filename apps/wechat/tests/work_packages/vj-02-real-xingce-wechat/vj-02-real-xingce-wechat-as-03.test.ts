import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../miniprogram/api/client";
import type { ApiClientError } from "../../../miniprogram/api/client";
import { XingceLearningApi } from "../../../miniprogram/api/xingce-learning";
import {
  checkpointCommand,
  mergePendingDraft,
  runtimeFromXingceSession,
} from "../../../miniprogram/features/xingce/real_attempt/controller";
import {
  loadPendingXingceCheckpoint,
  loadPendingXingceSubmit,
  storePendingXingceCheckpoint,
  storePendingXingceSubmit,
} from "../../../miniprogram/features/xingce/real_attempt/pending-store";
import { reduceTaskRuntime } from "../../../miniprogram/task-runtime/state/runtime";
import { activeXingceSession, answer, createMemoryWechatStorage, xingceIds } from "./harness";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("VJ-02 real xingce WeChat AS-03", () => {
  it("keeps one idempotent command after network uncertainty and isolates it by account", async () => {
    const { wxStorage } = createMemoryWechatStorage();
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      options.fail?.({
        errMsg: "request:fail timeout",
        errno: 600004,
        exception: { reasons: [], retryCount: 0 },
        useHttpDNS: false,
      });
    });
    vi.stubGlobal("wx", { ...wxStorage, request });
    const pending = {
      kind: "submit" as const,
      planTaskId: xingceIds.planTask,
      sessionId: xingceIds.session,
      idempotencyKey: "xingce-submit-command-stable",
      command: { expectedRevision: 4, totalElapsedSeconds: 42 },
    };
    storePendingXingceSubmit("user-a", pending);
    const api = new XingceLearningApi(new ApiClient("https://api.example.test", "token-a"));

    await expect(
      api.submitAttempt(pending.sessionId, pending.command, pending.idempotencyKey),
    ).rejects.toMatchObject({ statusCode: 0 } satisfies Partial<ApiClientError>);

    expect(loadPendingXingceSubmit("user-a", xingceIds.session)).toEqual(pending);
    expect(loadPendingXingceSubmit("user-b", xingceIds.session)).toBeNull();
    expect(request).toHaveBeenCalledOnce();
  });

  it("preserves the owned local draft while rebasing a stale CAS revision on server state", () => {
    const original = activeXingceSession({
      answers: [answer("q-1", ["A"])],
      revision: 2,
    });
    let local = runtimeFromXingceSession(original);
    local = reduceTaskRuntime(local, { type: "ANSWER_SELECTED", optionIds: ["B"] });
    local = reduceTaskRuntime(local, { type: "DRAFT_CHANGED", text: "排除 A 后选择 B" });
    local = reduceTaskRuntime(local, { type: "CONFIDENCE_SET", confidence: "low" });

    const authoritative = activeXingceSession({
      answers: [answer("q-1", ["A"])],
      revision: 5,
    });
    const merged = mergePendingDraft(authoritative, local);

    expect(merged.revision).toBe(5);
    expect(merged.syncState).toBe("error");
    expect(merged.attempts["q-1"]).toMatchObject({
      selectedOptionIds: ["B"],
      draftText: "排除 A 后选择 B",
      confidence: "low",
    });
    expect(checkpointCommand(merged).expectedRevision).toBe(5);
  });

  it("restores a server checkpoint after exit and retains the exact pending CAS command", () => {
    const { wxStorage } = createMemoryWechatStorage();
    vi.stubGlobal("wx", wxStorage);
    const restored = activeXingceSession({
      answers: [answer("q-1", ["C"]), answer("q-2", [], true)],
      revision: 7,
    });
    const runtime = runtimeFromXingceSession(restored);
    const command = checkpointCommand(runtime);
    const pending = {
      kind: "checkpoint" as const,
      planTaskId: xingceIds.planTask,
      sessionId: xingceIds.session,
      idempotencyKey: "xingce-checkpoint-command-stable",
      command,
    };
    storePendingXingceCheckpoint("user-a", pending);

    expect(runtime.attempts["q-1"]?.selectedOptionIds).toEqual(["C"]);
    expect(runtime.attempts["q-2"]?.skipped).toBe(true);
    expect(loadPendingXingceCheckpoint("user-a", xingceIds.session)).toEqual(pending);
    expect(loadPendingXingceCheckpoint("user-b", xingceIds.session)).toBeNull();
  });
});
