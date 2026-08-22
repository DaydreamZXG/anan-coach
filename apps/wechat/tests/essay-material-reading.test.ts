import { describe, expect, it } from "vitest";

import { materialTextParagraphs } from "../miniprogram/essay/shared/features/essay-task/material-paragraphs";
// @ts-expect-error JavaScript visual fixtures intentionally run in Node and do not ship declarations.
import { visualScenarios } from "../e2e/visual-scenarios.mjs";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import template from "../miniprogram/essay/essay-material/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import styles from "../miniprogram/essay/essay-material/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import source from "../miniprogram/essay/essay-material/index.ts?raw";

describe("申论材料阅读排版", () => {
  it("正文不再渲染装饰性段落序号，并保留中文阅读缩进", () => {
    expect(template).not.toContain("material-paragraph__number");
    expect(template).not.toContain("paragraphNumber");
    expect(styles).not.toContain(".material-paragraph__number");
    expect(template).toContain("{{item.displayText}}");
    expect(styles).not.toContain("text-indent");
  });

  it("单份定向材料只显示材料号，不伪装成材料号除以段数", () => {
    expect(source).toContain("if (directory.length <= 1) return `材料 ${String(materialOrder)}`;");
    expect(source).not.toContain("paragraphNumber");
    expect(source).not.toContain(" / ${String(directory.length)}");
  });

  it("视觉回归材料使用模板实际渲染字段，不能把空白材料当成通过", () => {
    const scenario = (
      visualScenarios as Array<{ id: string; data?: { segments?: unknown[] } }>
    ).find((item) => item.id === "f10-essay-material-map");
    const segments = scenario?.data?.segments ?? [];

    expect(segments.length).toBeGreaterThan(0);
    for (const segment of segments) {
      const item = segment as {
        text?: unknown;
        displayText?: unknown;
        displayParagraphId?: unknown;
        showAnnotation?: unknown;
      };
      expect(item.displayText).toBe(item.text);
      expect(item.displayParagraphId).toBeTruthy();
      expect(typeof item.showAnnotation).toBe("boolean");
    }
  });

  it("只纠正已核对的历史材料错字，不泛化替换正常的‘几平’", () => {
    const [paragraph] = materialTextParagraphs(
      "抽水马桶、淋浴系统大理石台面等几平是标配，掉色的相子成了废品。院子还有十几平方米。",
      "segment-a",
    );

    expect(paragraph?.text).toBe(
      "抽水马桶、淋浴系统、大理石台面等几乎是标配，掉色的箱子成了废品。院子还有十几平方米。",
    );
  });
});
