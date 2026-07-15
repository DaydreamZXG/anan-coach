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
});
