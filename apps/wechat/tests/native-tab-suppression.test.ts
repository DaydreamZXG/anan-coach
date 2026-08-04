import { describe, expect, it } from "vitest";
// @ts-expect-error Vite exposes mini-program page logic through its raw asset loader.
import todayLogic from "../miniprogram/pages/today/index.ts?raw";
// @ts-expect-error Vite exposes mini-program page logic through its raw asset loader.
import abilityLogic from "../miniprogram/pages/ability/index.ts?raw";
// @ts-expect-error Vite exposes mini-program page logic through its raw asset loader.
import ananLogic from "../miniprogram/pages/anan/index.ts?raw";
// @ts-expect-error Vite exposes mini-program page logic through its raw asset loader.
import meLogic from "../miniprogram/pages/me/index.ts?raw";
// @ts-expect-error Vite exposes mini-program platform logic through its raw asset loader.
import tabPolicy from "../miniprogram/platform/tab-bar.ts?raw";

describe("主工作台只展示产品悬浮 Tab", () => {
  it("四个主 Tab 每次显示都压住微信原生 Tab", () => {
    for (const logic of [todayLogic, abilityLogic, ananLogic, meLogic]) {
      expect(logic).toContain("suppressNativeTabBar();");
      expect(logic).not.toContain("wx.showTabBar");
    }
    expect(tabPolicy).toContain("wx.hideTabBar");
    expect(tabPolicy).not.toContain("wx.showTabBar");
  });
});
