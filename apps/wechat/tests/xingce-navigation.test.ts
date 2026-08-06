import { describe, expect, it } from "vitest";
import {
  registeredNavigationTarget,
  registeredTaskNavigationTarget,
  routes,
} from "../miniprogram/app/navigation/routes";

describe("real xingce task navigation", () => {
  it("registers the real practice page with a required mode and optional plan task", () => {
    expect(routes.practice).toBe("/pages/practice/index");
    expect(registeredTaskNavigationTarget("/pages/practice/index?mode=practice")).toEqual({
      mode: "navigateTo",
      url: "/pages/practice/index?mode=practice",
    });
    expect(
      registeredTaskNavigationTarget(
        "/pages/practice/index?mode=targeted&planTaskId=plan-task-2026-07-15",
      ),
    ).toEqual({
      mode: "navigateTo",
      url: "/pages/practice/index?mode=targeted&planTaskId=plan-task-2026-07-15",
    });
    expect(registeredTaskNavigationTarget("/pages/practice/index?mode=verification")).toEqual({
      mode: "navigateTo",
      url: "/pages/practice/index?mode=verification",
    });
  });

  it("rejects practice navigation without one valid mode", () => {
    expect(registeredTaskNavigationTarget("/pages/practice/index")).toBeNull();
    expect(registeredTaskNavigationTarget("/pages/practice/index?mode=")).toBeNull();
    expect(registeredTaskNavigationTarget("/pages/practice/index?mode=prototype")).toBeNull();
    expect(
      registeredTaskNavigationTarget("/pages/practice/index?mode=practice&mode=baseline"),
    ).toBeNull();
  });

  it("rejects empty, ambiguous and path-like plan task IDs", () => {
    expect(
      registeredTaskNavigationTarget("/pages/practice/index?mode=practice&planTaskId="),
    ).toBeNull();
    expect(
      registeredTaskNavigationTarget(
        "/pages/practice/index?mode=practice&planTaskId=plan%2Foutside",
      ),
    ).toBeNull();
    expect(
      registeredTaskNavigationTarget(
        "/pages/practice/index?mode=practice&planTaskId=plan%20outside",
      ),
    ).toBeNull();
    expect(
      registeredTaskNavigationTarget(
        "/pages/practice/index?mode=practice&planTaskId=first&planTaskId=second",
      ),
    ).toBeNull();
  });

  it("returns null instead of throwing for malformed encoded query input", () => {
    expect(() =>
      registeredTaskNavigationTarget("/pages/practice/index?mode=%E0%A4%A"),
    ).not.toThrow();
    expect(registeredTaskNavigationTarget("/pages/practice/index?mode=%E0%A4%A")).toBeNull();
  });

  it("does not mistake a merely registered page for a validated task target", () => {
    expect(registeredNavigationTarget("/pages/practice/index?mode=practice")).toMatchObject({
      mode: "navigateTo",
    });
    expect(registeredTaskNavigationTarget("/pages/reflection/index")).toBeNull();
    expect(
      registeredTaskNavigationTarget("/pages/practice-prototype/index?mode=practice"),
    ).toBeNull();
  });

  it("validates reflection tasks by session and review instead of requiring a plan task query", () => {
    const url =
      "/pages/reflection/index?sessionId=92a7f463-8c4f-416a-84c8-448386fc174a&reviewId=7efb10ba-d8a7-4fec-80dd-13243006c2b7";

    expect(registeredTaskNavigationTarget(url, "1385aa76-666b-48d8-b6d0-63a913237792")).toEqual({
      mode: "navigateTo",
      url,
    });
    expect(
      registeredTaskNavigationTarget(
        `${url}&planTaskId=1385aa76-666b-48d8-b6d0-63a913237792`,
        "1385aa76-666b-48d8-b6d0-63a913237792",
      ),
    ).toBeNull();
  });

  it("registers C08 as an authenticated non-task destination", () => {
    expect(routes.xingceMastery).toBe("/xingce/xingce-mastery/index");
    expect(registeredNavigationTarget(routes.xingceMastery)).toEqual({
      mode: "navigateTo",
      url: "/xingce/xingce-mastery/index",
    });
    expect(registeredTaskNavigationTarget(routes.xingceMastery)).toBeNull();
    expect(routes.xingceFavorites).toBe("/xingce/xingce-mastery/favorites/index");
    expect(registeredNavigationTarget(routes.xingceFavorites)).toEqual({
      mode: "navigateTo",
      url: "/xingce/xingce-mastery/favorites/index",
    });
    expect(registeredTaskNavigationTarget(routes.xingceFavorites)).toBeNull();
  });

  it("registers C01 only with one explicit plan task and briefing mode", () => {
    const url = "/xingce/xingce-task-intro/index?planTaskId=plan-task-2026-07-16&mode=targeted";
    expect(routes.xingceTaskIntro).toBe("/xingce/xingce-task-intro/index");
    expect(registeredTaskNavigationTarget(url)).toEqual({ mode: "navigateTo", url });
    expect(registeredTaskNavigationTarget(routes.xingceTaskIntro)).toBeNull();
    expect(
      registeredTaskNavigationTarget(
        "/xingce/xingce-task-intro/index?planTaskId=plan-task&mode=practice",
      ),
    ).toBeNull();
    expect(
      registeredTaskNavigationTarget(
        "/xingce/xingce-task-intro/index?planTaskId=plan-task&mode=verification&extra=1",
      ),
    ).toBeNull();
    expect(
      registeredTaskNavigationTarget(
        "/xingce/xingce-task-intro/index?planTaskId=first&planTaskId=second&mode=baseline",
      ),
    ).toBeNull();
  });
});
