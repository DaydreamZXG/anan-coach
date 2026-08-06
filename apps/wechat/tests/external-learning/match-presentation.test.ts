import { describe, expect, it } from "vitest";

import { presentMatchCandidates } from "../../miniprogram/pages/capture-import/features/external-learning/match-presentation";

describe("external-learning match presentation", () => {
  it("把候选分数格式化为百分比，并将真实证据引用整理为可读摘要", () => {
    expect(
      presentMatchCandidates([
        {
          contentVersionId: "content-version-1",
          matchKind: "exact",
          score: 0.986,
          evidenceRefs: ["fingerprint:1", "content-version:question-v42"],
        },
      ]),
    ).toEqual([
      expect.objectContaining({
        scoreLabel: "99%",
        evidenceSummaries: ["题面指纹 · 1", "题库版本 · question-v42"],
      }),
    ]);
  });
});
