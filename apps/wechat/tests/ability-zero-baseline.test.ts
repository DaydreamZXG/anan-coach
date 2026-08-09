import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program page logic through its raw asset loader.
import abilityLogic from "../miniprogram/pages/ability/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import abilityTemplate from "../miniprogram/pages/ability/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import abilityStyles from "../miniprogram/pages/ability/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program component logic through its raw asset loader.
import radarLogic from "../miniprogram/design-system/reporting/ability-radar/index.ts?raw";
// @ts-expect-error Vite exposes the shared module baseline through its raw asset loader.
import baselinesSource from "../miniprogram/domain/module-baselines.ts?raw";

describe("ability zero baseline", () => {
  it("labels the preparation gap against the stable line and explains its evidence", () => {
    expect(abilityTemplate).toContain("距稳妥线");
    expect(abilityLogic).toContain("stableLineGapLabel");
    expect(abilityLogic).toContain("还差 ${value} 分");
    expect(abilityLogic).toContain("goalBoundaryLabel: gap.evidenceSummary");
    expect(abilityLogic).toContain("estimate?.total?.recommended");
    expect(abilityTemplate).toContain("当前水平");
    expect(abilityTemplate).toContain("查看怎么算的");
    expect(abilityTemplate).toContain("showGoalEvidence ? '收起' : '查看怎么算的'");
    expect(abilityTemplate).toContain('wx:if="{{goalAvailable && showGoalEvidence}}"');
    expect(abilityTemplate).not.toContain("当前差距");
  });

  it("keeps the total and two subject scores on an intentional balanced grid", () => {
    expect(abilityStyles).toMatch(
      /\.overview-row\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*minmax\(0, 1fr\) minmax\(0, 1fr\);/su,
    );
    expect(abilityStyles).toMatch(
      /\.overview-scores\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\);/su,
    );
    expect(abilityStyles).toContain(".overview-score + .overview-score");
  });

  it("keeps the six canonical xingce axes visible before evidence exists", () => {
    for (const moduleName of [
      "政治理论",
      "常识判断",
      "言语理解与表达",
      "数量关系",
      "判断推理",
      "资料分析",
    ]) {
      expect(baselinesSource).toContain(`moduleKey: "${moduleName}"`);
    }
    expect(abilityLogic).toContain("XINGCE_MODULE_BASELINE");
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
      expect(baselinesSource).toContain(`displayName: "${dimensionName}"`);
    }
    expect(abilityLogic).toContain("ESSAY_MODULE_BASELINE");
    expect(abilityTemplate).toContain('disabled="{{!item.hasEvidence}}"');
    expect(abilityTemplate).not.toContain("写下第一篇申论，这里就开始认识你");
  });
});
