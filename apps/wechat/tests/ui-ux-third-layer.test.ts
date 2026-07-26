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
import privateKnowledgeTemplate from "../miniprogram/pages/private-knowledge/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import privateKnowledgeDetailTemplate from "../miniprogram/pages/private-knowledge/detail/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import todayTemplate from "../miniprogram/pages/today/index.wxml?raw";

function normalizeRpxCalc(source: unknown): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source.replace(/calc\((-?\d+(?:\.\d+)?) \* var\(--rpx\)\)/gu, "$1rpx");
}

const sheetStyles = normalizeRpxCalc(sheetStylesRaw);

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
    expect(ananLogic).toContain('stateLabel: "学习记录同步失败"');
  });

  it("keeps empty training and settings actions routed to real destinations", () => {
    expect(practiceTemplate).toContain('bindtap="goToday"');
    expect(practiceTemplate).toContain("去今日计划");
    expect(abilityTemplate).toContain('bindtap="openPlanBasis"');
    expect(meTemplate).toContain('bindtap="openAuthorizationSettings"');
    expect(meLogic).toContain("await wx.openSetting()");
    expect(meTemplate).not.toContain("申论记录");
  });

  it("keeps logout explicit and bound to the existing account workflow", () => {
    expect(meTemplate).toContain('bindtap="requestLogout"');
    expect(meLogic).toContain('title: "退出当前设备？"');
    expect(meLogic).toContain("await new AccountApi");
    expect(meLogic).toContain('wx.reLaunch({ url: "/pages/login/index" })');
  });
});
