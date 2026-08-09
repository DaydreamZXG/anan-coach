import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import abilityBaselineStylesRaw from "../miniprogram/pages/onboarding/ability-baseline/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import entryStylesRaw from "../miniprogram/pages/onboarding/entry/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingStylesRaw from "../miniprogram/pages/onboarding/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import positionPreferenceStylesRaw from "../miniprogram/pages/onboarding/position-preference/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import preparationExperienceStylesRaw from "../miniprogram/pages/onboarding/preparation-experience/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import profileStylesRaw from "../miniprogram/pages/onboarding/profile/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import resultStylesRaw from "../miniprogram/pages/onboarding/result/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import workbenchNavStylesRaw from "../miniprogram/pages/onboarding/shared/components/workbench-nav/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import studyPreferenceStylesRaw from "../miniprogram/pages/onboarding/study-preference/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import privateKnowledgeDetailStylesRaw from "../miniprogram/pages/private-knowledge/detail/index.wxss?raw";

const scopedStyles = {
  abilityBaseline: String(abilityBaselineStylesRaw),
  entry: String(entryStylesRaw),
  onboarding: String(onboardingStylesRaw),
  positionPreference: String(positionPreferenceStylesRaw),
  preparationExperience: String(preparationExperienceStylesRaw),
  profile: String(profileStylesRaw),
  result: String(resultStylesRaw),
  studyPreference: String(studyPreferenceStylesRaw),
  workbenchNav: String(workbenchNavStylesRaw),
  privateKnowledgeDetail: String(privateKnowledgeDetailStylesRaw),
};

function mediaBlocks(source: string, condition: string): string {
  const marker = `@media ${condition}`;
  const blocks: string[] = [];
  let cursor = 0;

  while (cursor < source.length) {
    const start = source.indexOf(marker, cursor);
    if (start < 0) break;
    const openingBrace = source.indexOf("{", start + marker.length);
    if (openingBrace < 0) throw new Error(`Missing opening brace for ${marker}`);

    let depth = 1;
    let end = openingBrace + 1;
    while (end < source.length && depth > 0) {
      if (source[end] === "{") depth += 1;
      if (source[end] === "}") depth -= 1;
      end += 1;
    }
    if (depth !== 0) throw new Error(`Unclosed ${marker}`);
    blocks.push(source.slice(start, end));
    cursor = end;
  }

  return blocks.join("\n");
}

describe("residual responsive breakpoint contract", () => {
  it("removes legacy 700/768 medium thresholds from the scoped styles only", () => {
    for (const [name, styles] of Object.entries(scopedStyles)) {
      expect(styles, name).not.toMatch(/@media\s*\(min-width:\s*(?:700|768)px\)/u);
    }
  });

  it("activates mechanically centered onboarding layouts at medium width", () => {
    const medium = "(min-width: 600px)";
    const contracts: Array<[string, string, string[]]> = [
      ["entry", scopedStyles.entry, [".entry-content"]],
      [
        "onboarding",
        scopedStyles.onboarding,
        [
          ".onboarding-scroll-content.onboarding-scroll-content",
          ".eligibility-content",
          ".eligibility-dock",
        ],
      ],
      [
        "positionPreference",
        scopedStyles.positionPreference,
        [".preference-content", ".preference-dock"],
      ],
      [
        "preparationExperience",
        scopedStyles.preparationExperience,
        [".identity-content", ".identity-dock"],
      ],
      ["result", scopedStyles.result, [".result-content"]],
    ];

    for (const [name, styles, selectors] of contracts) {
      const mediumBlocks = mediaBlocks(styles, medium);
      expect(mediumBlocks, name).not.toBe("");
      for (const selector of selectors)
        expect(mediumBlocks, `${name}: ${selector}`).toContain(selector);
    }
  });

  it("keeps detail reading and the safely capped 720px sheet in the medium layer", () => {
    const mediumBlocks = mediaBlocks(scopedStyles.privateKnowledgeDetail, "(min-width: 600px)");

    expect(mediumBlocks).toContain(".detail-content");
    expect(mediumBlocks).toContain(".delete-sheet");
    expect(mediumBlocks).toContain("max(0px, calc((100vw - 720px) / 2))");
  });
});
