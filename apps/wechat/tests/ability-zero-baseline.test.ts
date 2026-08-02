import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program page logic through its raw asset loader.
import abilityLogic from "../miniprogram/pages/ability/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import abilityTemplate from "../miniprogram/pages/ability/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program component logic through its raw asset loader.
import radarLogic from "../miniprogram/design-system/reporting/ability-radar/index.ts?raw";

describe("ability zero baseline", () => {
  it("keeps the six canonical xingce axes visible before evidence exists", () => {
    for (const moduleName of [
      "政治理论",
      "常识判断",
      "言语理解与表达",
      "数量关系",
      "判断推理",
      "资料分析",
    ]) {
      expect(abilityLogic).toContain(`displayName: "${moduleName}"`);
    }
    expect(abilityLogic).toContain('metricLabel: "0% · 0 次作答"');
    expect(abilityLogic).toContain('readinessLabel: "待采样"');
    expect(abilityTemplate).toContain('<ability-radar points="{{radarPoints}}" />');
    expect(abilityTemplate).not.toContain("三个模块有了真实作答");
  });

  it("marks an all-zero radar without pretending it has measured ability", () => {
    expect(radarLogic).toContain("points.every((point) => point.value === 0)");
    expect(radarLogic).toContain('context.fillText("0"');
    expect(abilityTemplate).toContain("暂无真实作答，当前以 0 为基线");
  });

  it("keeps the canonical essay dimensions visible before the first essay", () => {
    for (const dimensionName of [
      "找点与覆盖",
      "概括与归纳",
      "结构组织",
      "表达准确性",
      "格式规范",
      "材料依据",
    ]) {
      expect(abilityLogic).toContain(`displayName: "${dimensionName}"`);
    }
    expect(abilityLogic).toContain("const EMPTY_ESSAY_MODULES");
    expect(abilityTemplate).toContain('disabled="{{!item.hasEvidence}}"');
    expect(abilityTemplate).not.toContain("写下第一篇申论，这里就开始认识你");
  });
});
