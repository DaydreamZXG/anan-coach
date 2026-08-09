import { afterEach, describe, expect, it, vi } from "vitest";
import { EssayTaskPageRuntimeRegistry } from "../miniprogram/pages/essay-task/features/page-runtime";

const session = {
  userId: "user-a",
  accessToken: "token-a",
  generation: "generation-a",
};

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("essay task page-instance runtime", () => {
  it("isolates mutable task, review, timing, and effect state for two page instances", () => {
    stubCurrentSession();
    const registry = new EssayTaskPageRuntimeRegistry<object>();
    const pageA = {};
    const pageB = {};
    const runtimeA = registry.attach(pageA);
    const runtimeB = registry.attach(pageB);
    registry.bindEffects(pageA, runtimeA, session);
    registry.bindEffects(pageB, runtimeB, session);

    runtimeA.activeTaskVersionId = "task-a";
    runtimeA.activeUserId = "user-a";
    runtimeA.activeReviewJobId = "job-a";
    runtimeA.pageVisible = true;
    runtimeB.activeTaskVersionId = "task-b";
    runtimeB.activeUserId = "user-b";
    runtimeB.activeReviewJobId = "job-b";

    const effectA = registry.beginEffect(pageA, runtimeA, "workspace");
    const effectB = registry.beginEffect(pageB, runtimeB, "workspace");

    expect(runtimeA).not.toBe(runtimeB);
    expect(registry.get(pageA)).toBe(runtimeA);
    expect(registry.get(pageB)).toBe(runtimeB);
    expect(runtimeA).toMatchObject({
      activeTaskVersionId: "task-a",
      activeUserId: "user-a",
      activeReviewJobId: "job-a",
      pageVisible: true,
    });
    expect(runtimeB).toMatchObject({
      activeTaskVersionId: "task-b",
      activeUserId: "user-b",
      activeReviewJobId: "job-b",
      pageVisible: false,
    });
    expect(registry.isEffectCurrent(pageA, runtimeA, effectA)).toBe(true);
    expect(registry.isEffectCurrent(pageB, runtimeB, effectB)).toBe(true);
  });

  it("invalidates an unloaded page without invalidating another page effect", () => {
    stubCurrentSession();
    const registry = new EssayTaskPageRuntimeRegistry<object>();
    const pageA = {};
    const pageB = {};
    const runtimeA = registry.attach(pageA);
    const runtimeB = registry.attach(pageB);
    registry.bindEffects(pageA, runtimeA, session);
    registry.bindEffects(pageB, runtimeB, session);
    const effectA = registry.beginEffect(pageA, runtimeA, "workspace");
    const effectB = registry.beginEffect(pageB, runtimeB, "workspace");

    registry.detach(pageA);

    expect(runtimeA.disposed).toBe(true);
    expect(runtimeA.api).toBeNull();
    expect(registry.get(pageA)).toBeUndefined();
    expect(registry.isEffectCurrent(pageA, runtimeA, effectA)).toBe(false);
    expect(registry.isEffectCurrent(pageB, runtimeB, effectB)).toBe(true);
    expect(registry.owns(pageB, runtimeB)).toBe(true);
  });

  it("cancels only the detached instance timers and rejects its late effect callback", () => {
    vi.useFakeTimers();
    stubCurrentSession();
    const registry = new EssayTaskPageRuntimeRegistry<object>();
    const pageA = {};
    const pageB = {};
    const runtimeA = registry.attach(pageA);
    const runtimeB = registry.attach(pageB);
    registry.bindEffects(pageA, runtimeA, session);
    registry.bindEffects(pageB, runtimeB, session);
    const timerA = vi.fn();
    const timerB = vi.fn();
    const effectA = registry.beginEffect(pageA, runtimeA, "workspace");
    const effectB = registry.beginEffect(pageB, runtimeB, "workspace");
    runtimeA.timerHandle = setInterval(timerA, 1_000);
    runtimeA.reviewPollHandle = setTimeout(timerA, 2_000);
    runtimeB.timerHandle = setInterval(timerB, 1_000);

    registry.detach(pageA);
    vi.advanceTimersByTime(2_000);

    expect(timerA).not.toHaveBeenCalled();
    expect(timerB).toHaveBeenCalledTimes(2);
    expect(runtimeA.timerHandle).toBeUndefined();
    expect(runtimeA.reviewPollHandle).toBeUndefined();
    expect(registry.isEffectCurrent(pageA, runtimeA, effectA)).toBe(false);
    expect(registry.isEffectCurrent(pageB, runtimeB, effectB)).toBe(true);
  });

  it("disposes a prior runtime when the same page instance is attached again", () => {
    stubCurrentSession();
    const registry = new EssayTaskPageRuntimeRegistry<object>();
    const page = {};
    const first = registry.attach(page);
    registry.bindEffects(page, first, session);
    const firstEffect = registry.beginEffect(page, first, "workspace");
    first.activeTaskVersionId = "stale-task";

    const second = registry.attach(page);

    expect(first.disposed).toBe(true);
    expect(registry.isEffectCurrent(page, first, firstEffect)).toBe(false);
    expect(second.disposed).toBe(false);
    expect(second.activeTaskVersionId).toBe("");
    expect(registry.get(page)).toBe(second);
  });
});

function stubCurrentSession(): void {
  vi.stubGlobal("getApp", () => ({ globalData: { ...session } }));
}
