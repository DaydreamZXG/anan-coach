import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import source from "../miniprogram/pages/private-knowledge/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import template from "../miniprogram/pages/private-knowledge/index.wxml?raw";

describe("private knowledge import recovery", () => {
  it("keeps the selected local file and retries it without making the user reselect", () => {
    expect(source).toContain("retryFiles.set(this, file)");
    expect(source).toContain("retryImport");
    expect(source).toContain("retryFiles.get(this)");
    expect(template).toContain('bindtap="retryImport"');
    expect(template).toContain("不用重新选一遍");
  });
});
