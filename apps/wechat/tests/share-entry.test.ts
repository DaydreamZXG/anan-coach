import { readFileSync } from "node:fs";

import { describe, expect, it, vi } from "vitest";

const showShareMenu = vi.fn();

vi.stubGlobal("wx", {
  showShareMenu,
});

import {
  appShareMessage,
  appTimelineShare,
  enableAppShareMenu,
} from "../miniprogram/platform/share";

describe("public app sharing", () => {
  it("shares a public entry without current learning data", () => {
    expect(appShareMessage()).toEqual({
      title: "和岸岸一起，把备考计划落到今天",
      path: "/pages/today/index?from=share",
      imageUrl: "/assets/icons/anan/logo-design-file.png",
    });
    expect(appTimelineShare().query).toBe("from=timeline");
    expect(JSON.stringify(appShareMessage())).not.toMatch(/question|score|user|session/i);
  });

  it("enables native sharing on every main tab and exposes a visible profile action", () => {
    enableAppShareMenu();
    expect(showShareMenu).toHaveBeenCalledWith({
      menus: ["shareAppMessage", "shareTimeline"],
    });

    for (const page of ["today", "ability", "anan", "me"]) {
      const source = readFileSync(`miniprogram/pages/${page}/index.ts`, "utf8");
      expect(source).toContain("onShareAppMessage()");
      expect(source).toContain("onShareTimeline()");
    }
    const profile = readFileSync("miniprogram/pages/me/index.wxml", "utf8");
    expect(profile).toContain('open-type="share"');
    expect(profile).toContain("只分享小程序，不带出你的学习资料");
  });
});
