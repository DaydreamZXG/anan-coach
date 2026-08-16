import { describe, expect, it } from "vitest";
import { pointExtractionGuide } from "../../../miniprogram/pages/essay-task/features/forms";
import { essayWorkspace } from "./fixture";

describe("essay task closure 034 replay path", () => {
  it("replays the same prior reading without upgrading it to coach judgement", () => {
    const restored = JSON.parse(JSON.stringify(essayWorkspace())) as ReturnType<
      typeof essayWorkspace
    >;
    const first = pointExtractionGuide(restored);
    const second = pointExtractionGuide(restored);

    expect(second).toEqual(first);
    expect(JSON.stringify(second)).not.toContain("正确");
    expect(JSON.stringify(second)).not.toContain("评分");
  });
});
