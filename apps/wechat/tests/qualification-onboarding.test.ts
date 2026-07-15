import { describe, expect, it } from "vitest";
import {
  qualificationDraftFromProfile,
  qualificationFactsFingerprint,
  qualificationFactsFromDraft,
  validateQualificationStep,
  type QualificationDraft,
} from "../miniprogram/features/qualification/onboarding";

function draft(changes: Partial<QualificationDraft> = {}): QualificationDraft {
  return {
    education: "本科",
    major: "法学",
    freshGraduate: "true",
    politicalStatus: "群众",
    grassrootsYears: 0,
    hukou: "",
    ...changes,
  };
}

describe("qualification onboarding", () => {
  it("blocks each incomplete hard-condition step", () => {
    expect(validateQualificationStep(draft({ education: "" }), 0)).toBe("请选择最高学历。");
    expect(validateQualificationStep(draft({ major: "法" }), 0)).toContain("完整专业名称");
    expect(validateQualificationStep(draft({ freshGraduate: "" }), 1)).toContain("应届");
    expect(validateQualificationStep(draft({ politicalStatus: "" }), 1)).toContain("政治面貌");
    expect(validateQualificationStep(draft({ grassrootsYears: -1 }), 2)).toContain("基层");
  });

  it("produces only canonical qualification facts and omits blank hukou", () => {
    expect(qualificationFactsFromDraft(draft())).toEqual([
      { key: "education", value: "本科" },
      { key: "major", value: "法学" },
      { key: "fresh_graduate", value: true },
      { key: "political_status", value: "群众" },
      { key: "grassroots_years", value: 0 },
    ]);
  });

  it("normalizes user-entered strings without changing their meaning", () => {
    expect(
      qualificationFactsFromDraft(draft({ major: "  法学  ", hukou: "  江西省赣州市  " })),
    ).toContainEqual({ key: "hukou", value: "江西省赣州市" });
  });

  it("restores the current profile and recognizes an unchanged retry", () => {
    const restored = qualificationDraftFromProfile({
      profileVersion: 4,
      facts: qualificationFactsFromDraft(draft({ hukou: "江西省赣州市" })).map((fact, index) => ({
        ...fact,
        factId: `fact-${String(index)}`,
        verificationStatus: "reported" as const,
        effectiveAt: "2026-07-13T00:00:00Z",
      })),
    });

    expect(restored).toEqual(draft({ hukou: "江西省赣州市" }));
    expect(qualificationFactsFingerprint(qualificationFactsFromDraft(restored))).toBe(
      qualificationFactsFingerprint([...qualificationFactsFromDraft(restored)].reverse()),
    );
  });
});
