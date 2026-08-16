import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const memoryPage = readFileSync("miniprogram/account/memory/index.wxml", "utf8");
const memoryRuntime = readFileSync("miniprogram/account/memory/index.ts", "utf8");
const errorPattern = readFileSync("miniprogram/reports/error-pattern/index.ts", "utf8");
const mastery = readFileSync("miniprogram/xingce/xingce-mastery/index.ts", "utf8");

describe("岸岸长期认识与跨天复测", () => {
  it("在同一认识页展示可编辑明示记忆、发现与复测入口", () => {
    expect(memoryPage).toContain("岸岸对我的了解");
    expect(memoryPage).toContain("岸岸从学习里发现的");
    expect(memoryPage).toContain("待复测");
    expect(memoryRuntime).toContain("getCoachUnderstanding");
    expect(memoryRuntime).toContain("openDiscovery");
    expect(memoryRuntime).toContain("openRetest");
    expect(memoryRuntime).not.toContain("maximumLongTermItems");
  });

  it("不准确判断形成复核请求，并统一深链到原掌握度队列", () => {
    expect(errorPattern).toContain("requestCoachRecheck");
    expect(readFileSync("miniprogram/reports/error-pattern/index.wxml", "utf8")).toContain(
      "这条判断不准确",
    );
    expect(errorPattern).toContain("bucket=due_verification");
    expect(mastery).toContain("options.bucket");
  });
});
