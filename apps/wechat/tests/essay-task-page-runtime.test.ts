import { afterEach, describe, expect, it, vi } from "vitest";
import { EssayTaskPageRuntimeRegistry } from "../miniprogram/features/essay-task/page-runtime";

afterEach(() => {
  vi.useRealTimers();
});

describe("essay task page-instance runtime", () => {
  it("isolates mutable task, review, timing, and request state for two page instances", () => {
    const registry = new EssayTaskPageRuntimeRegistry<object>();
    const pageA = {};
    const pageB = {};
    const runtimeA = registry.attach(pageA);
    const runtimeB = registry.attach(pageB);

    runtimeA.activeTaskVersionId = "task-a";
    runtimeA.activeUserId = "user-a";
    runtimeA.activeReviewJobId = "job-a";
    runtimeA.pageVisible = true;
    runtimeB.activeTaskVersionId = "task-b";
    runtimeB.activeUserId = "user-b";
    runtimeB.activeReviewJobId = "job-b";

    const requestA = registry.beginRequest(runtimeA);
    const requestB = registry.beginRequest(runtimeB);

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
    expect(registry.isCurrent(pageA, runtimeA, requestA)).toBe(true);
    expect(registry.isCurrent(pageB, runtimeB, requestB)).toBe(true);
  });

  it("invalidates an unloaded page without invalidating another page request", () => {
    const registry = new EssayTaskPageRuntimeRegistry<object>();
    const pageA = {};
    const pageB = {};
    const runtimeA = registry.attach(pageA);
    const runtimeB = registry.attach(pageB);
    const requestA = registry.beginRequest(runtimeA);
    const requestB = registry.beginRequest(runtimeB);

    registry.detach(pageA);

    expect(runtimeA.disposed).toBe(true);
    expect(runtimeA.api).toBeNull();
    expect(registry.get(pageA)).toBeUndefined();
    expect(registry.isCurrent(pageA, runtimeA, requestA)).toBe(false);
    expect(registry.isCurrent(pageB, runtimeB, requestB)).toBe(true);
    expect(registry.owns(pageB, runtimeB)).toBe(true);
  });

  it("cancels only the detached instance timers and rejects its late callback epoch", () => {
    vi.useFakeTimers();
    const registry = new EssayTaskPageRuntimeRegistry<object>();
    const pageA = {};
    const pageB = {};
    const runtimeA = registry.attach(pageA);
    const runtimeB = registry.attach(pageB);
    const timerA = vi.fn();
    const timerB = vi.fn();
    const requestA = registry.beginRequest(runtimeA);
    const requestB = registry.beginRequest(runtimeB);
    runtimeA.timerHandle = setInterval(timerA, 1_000);
    runtimeA.reviewPollHandle = setTimeout(timerA, 2_000);
    runtimeB.timerHandle = setInterval(timerB, 1_000);

    registry.detach(pageA);
    vi.advanceTimersByTime(2_000);

    expect(timerA).not.toHaveBeenCalled();
    expect(timerB).toHaveBeenCalledTimes(2);
    expect(runtimeA.timerHandle).toBeUndefined();
    expect(runtimeA.reviewPollHandle).toBeUndefined();
    expect(registry.isCurrent(pageA, runtimeA, requestA)).toBe(false);
    expect(registry.isCurrent(pageB, runtimeB, requestB)).toBe(true);
  });

  it("disposes a prior runtime when the same page instance is attached again", () => {
    const registry = new EssayTaskPageRuntimeRegistry<object>();
    const page = {};
    const first = registry.attach(page);
    const firstRequest = registry.beginRequest(first);
    first.activeTaskVersionId = "stale-task";

    const second = registry.attach(page);

    expect(first.disposed).toBe(true);
    expect(registry.isCurrent(page, first, firstRequest)).toBe(false);
    expect(second.disposed).toBe(false);
    expect(second.activeTaskVersionId).toBe("");
    expect(registry.get(page)).toBe(second);
  });
});
