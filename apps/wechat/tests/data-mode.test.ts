import { afterEach, describe, expect, it } from "vitest";
import { configureForWechatEnvVersion, environment } from "../miniprogram/config/environment";
import { createCoachRepository } from "../miniprogram/data/coach-repository";

const originalMode = environment.dataMode;
const originalChannel = environment.releaseChannel;

afterEach(() => {
  environment.dataMode = originalMode;
  environment.releaseChannel = originalChannel;
});

describe("repository data mode", () => {
  it("refuses unauthenticated access even if a caller mutates the legacy mode flag", () => {
    environment.dataMode = "prototype";
    environment.releaseChannel = "development";

    expect(() => createCoachRepository()).toThrow(
      "Authenticated coach data requires a valid access token",
    );
  });

  it("forces develop, trial and release builds onto API data", () => {
    expect(configureForWechatEnvVersion("develop")).toMatchObject({
      dataMode: "api",
      releaseChannel: "development",
    });
    expect(configureForWechatEnvVersion("trial")).toMatchObject({
      dataMode: "api",
      releaseChannel: "staging",
    });
    expect(configureForWechatEnvVersion("release")).toMatchObject({
      dataMode: "api",
      releaseChannel: "production",
    });
  });

  it("requires a non-blank token for the API repository", () => {
    expect(() => createCoachRepository("   ")).toThrow(
      "Authenticated coach data requires a valid access token",
    );
    expect(createCoachRepository("token")).toBeDefined();
  });
});
