import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program component logic through its raw asset loader.
import sheetLogic from "../miniprogram/design-system/primitives/ui-sheet/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import sheetTemplate from "../miniprogram/design-system/primitives/ui-sheet/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import sheetStylesRaw from "../miniprogram/design-system/primitives/ui-sheet/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import abilityTemplate from "../miniprogram/pages/ability/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import ananTemplate from "../miniprogram/pages/anan/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program logic through its raw asset loader.
import ananLogic from "../miniprogram/pages/anan/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import meTemplate from "../miniprogram/pages/me/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program logic through its raw asset loader.
import meLogic from "../miniprogram/pages/me/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import practiceTemplate from "../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import essayEvidenceTemplate from "../miniprogram/essay/essay-evidence/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import essayEvidenceStylesRaw from "../miniprogram/essay/essay-evidence/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import privateKnowledgeTemplate from "../miniprogram/pages/private-knowledge/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import privateKnowledgeDetailTemplate from "../miniprogram/pages/private-knowledge/detail/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import todayTemplate from "../miniprogram/pages/today/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import todayStylesRaw from "../miniprogram/pages/today/index.wxss?raw";

function normalizeRpxCalc(source: unknown): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source.replace(/calc\((-?\d+(?:\.\d+)?) \* var\(--rpx\)\)/gu, "$1rpx");
}

const sheetStyles = normalizeRpxCalc(sheetStylesRaw);
const essayEvidenceTemplateText = normalizeRpxCalc(essayEvidenceTemplate);
const essayEvidenceStyles = normalizeRpxCalc(essayEvidenceStylesRaw);
const todayStyles = normalizeRpxCalc(todayStylesRaw);

describe("third-layer UI and UX contracts", () => {
  it("keeps ui-sheet mounted through its exit transition", () => {
    expect(sheetLogic).toContain("rendered: false");
    expect(sheetLogic).toContain("shown: false");
    expect(sheetLogic).toContain("onPanelTransitionEnd");
    expect(sheetTemplate).toContain('wx:if="{{rendered}}"');
    expect(sheetTemplate).toContain('bindtransitionend="onPanelTransitionEnd"');
    expect(sheetStyles).toContain("transition: opacity var(--motion-base) var(--ease-in-out)");
    expect(sheetStyles).toContain("transform var(--motion-base) var(--ease-out)");
  });

  it("keeps recoverable errors local to the affected product surface", () => {
    expect(meTemplate).toContain("qualificationContextStatus === 'error'");
    expect(meTemplate).toContain("studyContextStatus === 'error'");
    expect(meTemplate).toContain("targetContextStatus === 'error'");
    expect(privateKnowledgeTemplate).toContain("searchStatus === 'error'");
    expect(privateKnowledgeTemplate).toContain("没有找到“{{searchedQuery}}”");
    expect(privateKnowledgeTemplate).toContain("问题编号 · {{searchRequestId}}");
    expect(privateKnowledgeDetailTemplate).toContain('request-id="{{requestId}}"');
    expect(todayTemplate).toContain("openingStatus === 'error'");
    expect(todayTemplate).toContain('bindtap="loadDailyOpening"');
    expect(ananTemplate).toContain("state-{{semanticState}}");
    expect(ananLogic).toContain('stateLabel: "学习记录没读出来"');
  });

  it("keeps empty training and settings actions routed to real destinations", () => {
    expect(practiceTemplate).toContain('bindtap="goToday"');
    expect(practiceTemplate).toContain("去今日计划");
    expect(abilityTemplate).toContain('bindtap="openPlanBasis"');
    expect(meTemplate).toContain('bindtap="openAuthorizationSettings"');
    expect(meLogic).toContain("await wx.openSetting()");
    expect(meTemplate).not.toContain("申论记录");
  });

  it("wraps the essay evidence title only at complete Chinese phrases", () => {
    const phrases = Array.from(
      essayEvidenceTemplateText.matchAll(
        /<text class="essay-evidence-title__phrase">([^<]+)<\/text>/gu,
      ),
      (match) => match[1] ?? "",
    );

    expect(phrases).toEqual(["为什么这样评，", "逐条说清楚"]);
    expect(essayEvidenceTemplateText).toContain('class="workbench-title essay-evidence-title"');
    expect(essayEvidenceStyles).toMatch(
      /\.essay-evidence-title\s*\{[^}]*display:\s*flex;[^}]*flex-wrap:\s*wrap;/su,
    );
    expect(essayEvidenceStyles).toMatch(
      /\.essay-evidence-title__phrase\s*\{[^}]*flex:\s*0 0 auto;[^}]*white-space:\s*nowrap;/su,
    );

    const titleWidthRpx = 650;
    const titleFontRpx = 48;
    const phraseWidthsAtLargeText = phrases.map(
      (phrase) => Array.from(phrase).length * titleFontRpx * 1.3,
    );
    expect(Math.max(...phraseWidthsAtLargeText)).toBeLessThan(titleWidthRpx);
    expect(phrases.join("").length * titleFontRpx).toBeLessThan(titleWidthRpx);
    expect(phrases.join("").length * titleFontRpx * 1.3).toBeGreaterThan(titleWidthRpx);
  });

  it("keeps logout explicit and bound to the existing account workflow", () => {
    expect(meTemplate).toContain('bindtap="requestLogout"');
    expect(meLogic).toContain('title: "退出当前设备？"');
    expect(meLogic).toContain("await new AccountApi");
    expect(meLogic).toContain('wx.reLaunch({ url: "/pages/login/index" })');
  });

  it("presents today's secondary destinations as clear, non-duplicated actions", () => {
    expect(todayTemplate).toContain("结束今天并复盘");
    expect(todayTemplate).toContain("双轨进度");
    expect(todayTemplate).toContain("行测与申论每天都保留状态");
    expect(todayTemplate).toContain("计划依据");
    expect(todayTemplate).toContain("周计划");
    expect(todayTemplate).not.toContain("每日结算");
    expect(todayStyles).toMatch(
      /\.settlement-button\s*\{[^}]*background:\s*var\(--ink\);[^}]*color:\s*var\(--color-surface\);/su,
    );
    expect(todayStyles).toMatch(
      /\.tool-link\s*\{[^}]*flex:\s*1;[^}]*border:[^;]+;[^}]*background:\s*var\(--color-surface\);/su,
    );
  });

  it("keeps breathing room between the primary task and the follow-up queue", () => {
    expect(todayStyles).toMatch(/\.other-tasks\s*\{[^}]*margin-top:\s*var\(--figma-space-16\);/su);
    expect(todayStyles).toMatch(
      /\.settlement-button\s*\{[^}]*margin-top:\s*var\(--figma-space-16\);/su,
    );
  });

  it("reserves the two-column workbench for landscape tablets", () => {
    expect(todayTemplate).toContain('class="secondary-tools__title">学习工具</view>');
    expect(todayTemplate).toContain('class="secondary-tools__meta">模考 · 复盘 · 计划</view>');
    expect(todayTemplate).toContain('class="track-section today-col--track"');
    expect(todayStyles).toContain("@media (min-width: 1024px) and (orientation: landscape)");
    expect(todayStyles).toMatch(
      /@media \(min-width: 1024px\) and \(orientation: landscape\)[\s\S]*?\.today-content\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*minmax\(0, 1fr\) 320px;/u,
    );
    expect(todayStyles).toMatch(
      /\.today-col--track \.track-grid\s*\{[^}]*flex-direction:\s*column;/u,
    );
  });
});
