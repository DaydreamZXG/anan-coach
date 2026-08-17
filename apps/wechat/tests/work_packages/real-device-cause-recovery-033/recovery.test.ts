import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import reflectionTemplate from "../../../miniprogram/pages/reflection/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import practiceStyles from "../../../miniprogram/pages/practice/index.wxss?raw";

function asText(value: unknown): string {
  if (typeof value !== "string") throw new Error("Raw mini-program asset must be text.");
  return value;
}

const reflection = asText(reflectionTemplate);
const styles = asText(practiceStyles);

describe("real-device wait and large-phone recovery", () => {
  it("lets the learner leave a durable background job and refresh later", () => {
    expect(reflection).toContain("作答和草稿证据已经存好");
    expect(reflection).toContain("可以先离开");
    expect(reflection).toContain('bindtap="refreshCauseCandidate">刷新进度</button>');
    expect(reflection).not.toContain("别走开");
    expect(reflection).not.toContain("重新检查一次");
  });

  it("enables two columns only on genuinely wide landscape surfaces", () => {
    expect(styles).toContain("@media (min-width: 1024px) and (orientation: landscape)");
    const wideLayout = styles.match(
      /@media \(min-width: 1024px\) and \(orientation: landscape\) \{[\s\S]*?grid-template-columns: minmax\(340px, 0\.95fr\) minmax\(0, 1\.2fr\);/u,
    );
    expect(wideLayout).not.toBeNull();
    expect(styles).not.toMatch(
      /@media \(min-width: 1024px\) \{[\s\S]*?grid-template-columns: minmax\(340px, 0\.95fr\)/u,
    );
  });
});
