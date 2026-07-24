import { describe, expect, it } from "vitest";
import {
  abilityBaselineDraftFromCommand,
  buildAbilityBaselineCommand,
  emptyAbilityBaselineDraft,
  validateAbilityBaselineDraft,
} from "../miniprogram/features/onboarding/ability-baseline";

describe("onboarding ability baseline", () => {
  it("records an explicit unknown declaration without manufacturing a score", () => {
    const command = buildAbilityBaselineCommand(
      { ...emptyAbilityBaselineDraft, mode: "unknown" },
      0,
    );
    expect(command).toEqual({
      expectedBaselineVersion: 0,
      mode: "unknown",
      xingceScore: null,
      shenlunScore: null,
    });
  });

  it("accepts one or both self-reported score components", () => {
    expect(
      buildAbilityBaselineCommand(
        {
          mode: "reported_score",
          xingceScore: "65.5",
          shenlunScore: "",
        },
        2,
      ),
    ).toEqual({
      expectedBaselineVersion: 2,
      mode: "reported_score",
      xingceScore: 65.5,
      shenlunScore: null,
    });
  });

  it("rejects malformed or empty reported scores", () => {
    expect(
      validateAbilityBaselineDraft({
        mode: "reported_score",
        xingceScore: "",
        shenlunScore: "",
      }),
    ).toContain("至少填写");
    expect(
      validateAbilityBaselineDraft({
        mode: "reported_score",
        xingceScore: "101",
        shenlunScore: "",
      }),
    ).toContain("0–100");
    expect(() =>
      buildAbilityBaselineCommand(
        { mode: "unknown", xingceScore: "", shenlunScore: "" },
        -1,
      ),
    ).toThrow("expected version");
  });

  it("restores pending score text without changing the command meaning", () => {
    expect(
      abilityBaselineDraftFromCommand({
        expectedBaselineVersion: 1,
        mode: "reported_score",
        xingceScore: 64,
        shenlunScore: 58.5,
      }),
    ).toEqual({
      mode: "reported_score",
      xingceScore: "64",
      shenlunScore: "58.5",
    });
  });
});
