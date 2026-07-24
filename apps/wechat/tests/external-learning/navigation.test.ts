import { describe, expect, it } from "vitest";
import {
  answerIngestionUrl,
  captureImportUrl,
  learningPacketsUrl,
  registeredNavigationTarget,
  routes,
} from "../../miniprogram/app/navigation/routes";
import {
  authenticatedReturnTarget,
  loginReturnTarget,
  loginUrlForReturn,
} from "../../miniprogram/app/navigation/auth-return";

describe("external learning navigation", () => {
  it("registers all three pages and builds exact typed query routes", () => {
    expect(registeredNavigationTarget(routes.captureImport)).toEqual({
      mode: "navigateTo",
      url: routes.captureImport,
    });
    expect(registeredNavigationTarget(routes.learningPackets)).toEqual({
      mode: "navigateTo",
      url: routes.learningPackets,
    });
    expect(registeredNavigationTarget(routes.answerIngestion)).toEqual({
      mode: "navigateTo",
      url: routes.answerIngestion,
    });
    expect(captureImportUrl("import/1")).toBe("/pages/capture-import/index?importId=import%2F1");
    expect(learningPacketsUrl({ contentVersionId: "content/1" })).toBe(
      "/pages/learning-packets/index?contentVersionId=content%2F1",
    );
    expect(answerIngestionUrl({ packetVersionId: "packet/1" })).toBe(
      "/pages/answer-ingestion/index?packetVersionId=packet%2F1",
    );
    expect(answerIngestionUrl({ ingestionId: "ingestion/1" })).toBe(
      "/pages/answer-ingestion/index?ingestionId=ingestion%2F1",
    );
    expect(() =>
      answerIngestionUrl({ packetVersionId: "packet-1", ingestionId: "ingestion-1" }),
    ).toThrow(/not both/u);
  });

  it("preserves only allowlisted external-learning destinations across login", () => {
    const capture = captureImportUrl("import-1");
    const packets = learningPacketsUrl({ contentVersionId: "content-version-1" });
    const answer = answerIngestionUrl({ packetVersionId: "packet-1" });
    expect(authenticatedReturnTarget(capture)).toEqual({ mode: "navigateTo", url: capture });
    expect(authenticatedReturnTarget(packets)).toEqual({ mode: "navigateTo", url: packets });
    expect(loginReturnTarget(encodeURIComponent(answer))).toEqual({
      mode: "navigateTo",
      url: answer,
    });
    expect(loginUrlForReturn(routes.learningPackets)).toBe(
      "/pages/login/index?returnTo=%2Fpages%2Flearning-packets%2Findex",
    );
  });

  it("rejects extra, duplicate, ambiguous, and oversized route parameters", () => {
    expect(authenticatedReturnTarget(`${routes.captureImport}?unexpected=1`)).toBeNull();
    expect(
      authenticatedReturnTarget(`${routes.captureImport}?importId=one&importId=two`),
    ).toBeNull();
    expect(
      authenticatedReturnTarget(
        `${routes.answerIngestion}?packetVersionId=packet-1&ingestionId=ingestion-1`,
      ),
    ).toBeNull();
    expect(
      authenticatedReturnTarget(`${routes.captureImport}?importId=${"a".repeat(201)}`),
    ).toBeNull();
    expect(authenticatedReturnTarget(`${routes.learningPackets}?unexpected=1`)).toBeNull();
  });
});
