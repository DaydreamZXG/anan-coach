import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceLifecycleRaw from "../../xingce-practice-page-lifecycle.test.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockLifecycleRaw from "../../stage2-mock-exam.test.ts?raw";

describe("direct draft test contract 032 stale path", () => {
  it("keeps lifecycle release assertions after replacing the surface gate", () => {
    const practice = String(practiceLifecycleRaw);
    const mock = String(mockLifecycleRaw);
    expect(practice).toContain("runtime.page.nextQuestion.call(runtime.page)");
    expect(practice).toContain("runtime.page.onHide.call(runtime.page)");
    expect(practice).toContain("runtime.page.onUnload.call(runtime.page)");
    expect(mock).toContain("harness.page.nextQuestion.call(harness.page)");
    expect(mock).toContain("harness.page.onHide.call(harness.page)");
    expect(mock).toContain("harness.page.onUnload.call(harness.page)");
  });
});
