import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../miniprogram/api/client";
import { XingceLearningApi } from "../../../miniprogram/xingce/shared/api/xingce-learning";
import { registeredTaskNavigationTarget } from "../../../miniprogram/app/navigation/routes";
import { resolveXingcePlanTaskId } from "../../../miniprogram/features/xingce/real_attempt/contracts";
import { activeXingceSession, apiSuccess, todayWithXingceTask, xingceIds } from "./harness";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("VJ-02 real xingce WeChat AS-01", () => {
  it("opens the registered real Today task and starts/restores the server session by planTaskId", async () => {
    const startUrl = `/pages/practice/index?mode=targeted&planTaskId=${xingceIds.planTask}`;
    expect(registeredTaskNavigationTarget(startUrl)).toEqual({
      mode: "navigateTo",
      url: startUrl,
    });
    expect(resolveXingcePlanTaskId(todayWithXingceTask(), "targeted", xingceIds.planTask)).toBe(
      xingceIds.planTask,
    );

    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe("https://api.example.test/api/v2/xingce/task-sessions");
      expect(options.method).toBe("POST");
      expect(options.data).toEqual({ planTaskId: xingceIds.planTask });
      expect(options.header).toMatchObject({
        Authorization: "Bearer real-user-token",
        "Idempotency-Key": "xingce-start-command-1",
      });
      options.success?.(apiSuccess(activeXingceSession()));
    });
    vi.stubGlobal("wx", { request });
    const api = new XingceLearningApi(new ApiClient("https://api.example.test", "real-user-token"));

    const first = await api.startSession(xingceIds.planTask, "xingce-start-command-1");
    const replay = await api.startSession(xingceIds.planTask, "xingce-start-command-1");

    expect(first.data.sessionId).toBe(xingceIds.session);
    expect(replay.data.sessionId).toBe(first.data.sessionId);
    expect(first.data.bundle.questions[0]?.stem).toBe("以下说法正确的是？");
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("refuses to guess when Today has no unique mode-and-startUrl match", () => {
    const dashboard = todayWithXingceTask();
    const firstTask = dashboard.tasks[0];
    if (!firstTask) throw new Error("fixture task is required");
    const duplicate: typeof dashboard = {
      ...dashboard,
      tasks: [...dashboard.tasks, { ...firstTask, id: `${xingceIds.planTask}-duplicate` }],
    };
    expect(() => resolveXingcePlanTaskId(duplicate, "targeted")).toThrow("多个");
    expect(() => resolveXingcePlanTaskId(dashboard, "baseline")).toThrow("没有");
  });
});
