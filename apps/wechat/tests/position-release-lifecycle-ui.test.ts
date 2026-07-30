import { describe, expect, it } from "vitest";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import compareTemplate from "../miniprogram/pages/positions/compare/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import detailTemplate from "../miniprogram/pages/positions/detail/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import goalTemplate from "../miniprogram/pages/positions/goal/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import workspaceTemplate from "../miniprogram/pages/positions/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import searchTemplate from "../miniprogram/pages/positions/search/index.wxml?raw";

const releaseUnavailableTemplates = [
  workspaceTemplate,
  searchTemplate,
  detailTemplate,
  compareTemplate,
  goalTemplate,
];

describe("position release lifecycle UI contract", () => {
  it("renders the server-classified lifecycle instead of claiming every phase is unpublished", () => {
    for (const template of releaseUnavailableTemplates) {
      expect(template).toContain('title="{{');
      expect(template).toContain('.error.title}}"');
      expect(template).toContain('.error.message}}"');
      expect(template).not.toContain("本年度岗位尚未正式发布");
      expect(template).not.toContain("正式职位表发布后再来查看");
    }
  });

  it("keeps a real ability-analysis action available outside an open window", () => {
    for (const template of releaseUnavailableTemplates) {
      expect(template).toContain('bind:action="openPreparation"');
    }
    expect(workspaceTemplate).toContain('action-text="查看能力分析"');
    expect(searchTemplate).toContain('action-text="查看能力分析"');
    expect(detailTemplate).toContain('action-text="继续备考"');
    expect(compareTemplate).toContain('action-text="继续备考"');
    expect(goalTemplate).toContain('action-text="继续备考"');
  });
});
