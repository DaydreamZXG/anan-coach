import { describe, expect, it, vi } from "vitest";
import {
  DATA_FONT_FAMILY,
  DataFontFaceManager,
  isOwnedHttpsAssetUrl,
} from "../miniprogram/platform/data-font";

const ownedOrigin = "https://assets.xingguoxian.cn";

describe("Maple Mono data font", () => {
  it("stays explicitly unconfigured without claiming the font is loaded", async () => {
    const loader = vi.fn();
    const manager = createManager(loader);

    const state = await manager.load({
      mapleMonoUrl: null,
      ownedHttpsOrigins: [ownedOrigin],
    });

    expect(loader).not.toHaveBeenCalled();
    expect(state).toMatchObject({
      family: DATA_FONT_FAMILY,
      status: "unconfigured",
      configured: false,
      sourceUrl: null,
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
      mapleMonoUrl: "https://cdn.example.com/maple.woff",
      ownedHttpsOrigins: [ownedOrigin],
    });

    expect(loader).not.toHaveBeenCalled();
    expect(state).toMatchObject({
      status: "invalid_configuration",
      configured: true,
      resultStatus: "asset_url_rejected",
    });
  });

  it("loads an owned HTTPS asset globally and publishes observable state", async () => {
    let loadOptions: WechatMiniprogram.LoadFontFaceOption | undefined;
    const loader = vi.fn((options: WechatMiniprogram.LoadFontFaceOption) => {
      loadOptions = options;
    });
    const manager = createManager(loader);
    const observed: string[] = [];
    const unsubscribe = manager.subscribe((state) => observed.push(state.status));

    const pending = manager.load({
      mapleMonoUrl: `${ownedOrigin}/fonts/maple-mono-data.woff`,
      ownedHttpsOrigins: [ownedOrigin],
    });

    expect(manager.snapshot().status).toBe("loading");
    expect(loadOptions).toMatchObject({
      family: DATA_FONT_FAMILY,
      source: `url("${ownedOrigin}/fonts/maple-mono-data.woff")`,
      global: true,
      scopes: ["webview", "native"],
    });
    loadOptions?.success?.({ status: "loaded" });
    const state = await pending;
    unsubscribe();

    expect(state).toMatchObject({ status: "loaded", attempt: 1, resultStatus: "loaded" });
    expect(observed).toEqual(["unconfigured", "loading", "loaded"]);
  });

  it("deduplicates an in-flight load and exposes a real failure", async () => {
    let loadOptions: WechatMiniprogram.LoadFontFaceOption | undefined;
    const loader = vi.fn((options: WechatMiniprogram.LoadFontFaceOption) => {
      loadOptions = options;
    });
    const manager = createManager(loader);
    const config = {
      mapleMonoUrl: `${ownedOrigin}/fonts/maple-mono-data.woff`,
      ownedHttpsOrigins: [ownedOrigin],
    };

    const first = manager.load(config);
    const second = manager.load(config);
    expect(first).toBe(second);
    expect(loader).toHaveBeenCalledOnce();

    loadOptions?.fail?.({ status: "download_failed" });

    await expect(first).resolves.toMatchObject({
      status: "failed",
      configured: true,
      attempt: 1,
      resultStatus: "download_failed",
    });
  });

  it("retries a failed lifecycle load and records the retry trigger", async () => {
    const attempts: WechatMiniprogram.LoadFontFaceOption[] = [];
    const manager = createManager((options) => attempts.push(options));
    const config = {
      mapleMonoUrl: `${ownedOrigin}/fonts/maple-mono-data.woff`,
      ownedHttpsOrigins: [ownedOrigin],
    };

    const initial = manager.load(config, "app_launch");
    attempts[0]?.fail?.({ status: "network_error" });
    await expect(initial).resolves.toMatchObject({
      status: "failed",
      attempt: 1,
      lastTrigger: "app_launch",
    });

    const retried = manager.retry(config, "network_reconnected");
    expect(attempts).toHaveLength(2);
    attempts[1]?.success?.({ status: "loaded" });
    await expect(retried).resolves.toMatchObject({
      status: "loaded",
      attempt: 2,
      lastTrigger: "network_reconnected",
    });

    await expect(manager.retry(config, "app_show_retry")).resolves.toMatchObject({
      status: "loaded",
      attempt: 2,
      lastTrigger: "network_reconnected",
    });
    expect(attempts).toHaveLength(2);
  });
});

function createManager(loader: (options: WechatMiniprogram.LoadFontFaceOption) => void) {
  return new DataFontFaceManager(loader, () => new Date("2026-07-13T00:00:00.000Z"));
}
