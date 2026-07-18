import { describe, expect, it, vi } from "vitest";
import {
  PRODUCT_FONT_FAMILY,
  ProductFontFaceManager,
  isOwnedHttpsAssetUrl,
} from "../miniprogram/platform/product-font";

const ownedOrigin = "https://assets.xingguoxian.cn";
const regularUrl = `${ownedOrigin}/fonts/maple-mono-cn-regular.woff`;
const boldUrl = `${ownedOrigin}/fonts/maple-mono-cn-bold.woff`;

describe("Maple Mono CN product font", () => {
  it("requires both selected weights before attempting a load", async () => {
    const loader = vi.fn();
    const manager = createManager(loader);

    const state = await manager.load({
      mapleMonoCnRegularUrl: regularUrl,
      mapleMonoCnBoldUrl: null,
      ownedHttpsOrigins: [ownedOrigin],
    });

    expect(loader).not.toHaveBeenCalled();
    expect(state).toMatchObject({
      family: PRODUCT_FONT_FAMILY,
      status: "unconfigured",
      configured: false,
      attempt: 0,
    });
  });

  it("rejects HTTP and third-party font sources before invoking WeChat", async () => {
    const loader = vi.fn();
    const manager = createManager(loader);

    expect(isOwnedHttpsAssetUrl("http://assets.xingguoxian.cn/maple.woff", [ownedOrigin])).toBe(
      false,
    );
    const state = await manager.load({
      mapleMonoCnRegularUrl: "https://cdn.example.com/maple.woff",
      mapleMonoCnBoldUrl: boldUrl,
      ownedHttpsOrigins: [ownedOrigin],
    });

    expect(loader).not.toHaveBeenCalled();
    expect(state).toMatchObject({
      status: "invalid_configuration",
      configured: true,
      faces: {
        regular: { resultStatus: "asset_url_rejected" },
        bold: { resultStatus: "asset_url_rejected" },
      },
    });
  });

  it("loads Regular 400 and Bold 700 globally under one family", async () => {
    const loadOptions: WechatMiniprogram.LoadFontFaceOption[] = [];
    const manager = createManager((options) => loadOptions.push(options));
    const observed: string[] = [];
    const unsubscribe = manager.subscribe((state) => observed.push(state.status));

    const pending = manager.load(config());

    expect(manager.snapshot().status).toBe("loading");
    expect(loadOptions).toHaveLength(2);
    expect(loadOptions[0]).toMatchObject({
      family: PRODUCT_FONT_FAMILY,
      source: `url("${regularUrl}")`,
      global: true,
      scopes: ["webview", "native"],
      desc: { style: "normal", weight: "400" },
    });
    expect(loadOptions[1]).toMatchObject({
      family: PRODUCT_FONT_FAMILY,
      source: `url("${boldUrl}")`,
      desc: { style: "normal", weight: "700" },
    });
    loadOptions[0]?.success?.({ status: "loaded" });
    loadOptions[1]?.success?.({ status: "loaded" });
    const state = await pending;
    unsubscribe();

    expect(state).toMatchObject({
      status: "loaded",
      attempt: 1,
      faces: {
        regular: { status: "loaded", weight: "400" },
        bold: { status: "loaded", weight: "700" },
      },
    });
    expect(observed).toEqual(["unconfigured", "loading", "loaded"]);
  });

  it("deduplicates an in-flight two-face load", async () => {
    const loadOptions: WechatMiniprogram.LoadFontFaceOption[] = [];
    const manager = createManager((options) => loadOptions.push(options));

    const first = manager.load(config());
    const second = manager.load(config());
    expect(first).toBe(second);
    expect(loadOptions).toHaveLength(2);

    loadOptions[0]?.success?.({ status: "loaded" });
    loadOptions[1]?.fail?.({ status: "download_failed" });

    await expect(first).resolves.toMatchObject({
      status: "failed",
      configured: true,
      attempt: 1,
      faces: {
        regular: { status: "loaded" },
        bold: { status: "failed", resultStatus: "download_failed" },
      },
    });
  });

  it("retries only the failed face and records the retry trigger", async () => {
    const attempts: WechatMiniprogram.LoadFontFaceOption[] = [];
    const manager = createManager((options) => attempts.push(options));

    const initial = manager.load(config(), "app_launch");
    attempts[0]?.success?.({ status: "loaded" });
    attempts[1]?.fail?.({ status: "network_error" });
    await expect(initial).resolves.toMatchObject({
      status: "failed",
      attempt: 1,
      lastTrigger: "app_launch",
    });

    const retried = manager.retry(config(), "network_reconnected");
    expect(attempts).toHaveLength(3);
    expect(attempts[2]?.desc?.weight).toBe("700");
    attempts[2]?.success?.({ status: "loaded" });
    await expect(retried).resolves.toMatchObject({
      status: "loaded",
      attempt: 2,
      lastTrigger: "network_reconnected",
    });

    await expect(manager.retry(config(), "app_show_retry")).resolves.toMatchObject({
      status: "loaded",
      attempt: 2,
      lastTrigger: "network_reconnected",
    });
    expect(attempts).toHaveLength(3);
  });
});

function config() {
  return {
    mapleMonoCnRegularUrl: regularUrl,
    mapleMonoCnBoldUrl: boldUrl,
    ownedHttpsOrigins: [ownedOrigin],
  };
}

function createManager(loader: (options: WechatMiniprogram.LoadFontFaceOption) => void) {
  return new ProductFontFaceManager(loader, () => new Date("2026-07-17T00:00:00.000Z"));
}
