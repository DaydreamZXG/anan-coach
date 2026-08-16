import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const pageDirectory = resolve(process.cwd(), "miniprogram/plans/retake");

describe("retake page recovery", () => {
  it("does not offer a dead retry loop for a non-retryable experience gate", () => {
    const markup = readFileSync(resolve(pageDirectory, "index.wxml"), "utf8");
    const source = readFileSync(resolve(pageDirectory, "index.ts"), "utf8");

    expect(markup).toContain('wx:if="{{state.error.retryable}}"');
    expect(markup).toContain("state.error.code === 'retake_experience_required'");
    expect(markup).toContain('label="去确认备考经历"');
    expect(markup).toContain('bind:action="editPreparationExperience"');
    expect(source).toContain("routes.onboardingPreparationExperience");
  });
});
