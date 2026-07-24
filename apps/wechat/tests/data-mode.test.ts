import { afterEach, describe, expect, it } from "vitest";
import { configureForWechatEnvVersion, environment } from "../miniprogram/config/environment";
import { createCoachRepository } from "../miniprogram/data/coach-repository";

const originalChannel = environment.releaseChannel;

afterEach(() => {
  environment.releaseChannel = originalChannel;
});

describe("repository environment", () => {
  it("refuses unauthenticated access", () => {
    expect(() => createCoachRepository()).toThrow(
      "Authenticated coach data requires a valid access token",
    );
  });

  it("maps WeChat environment versions without exposing an alternate data mode", () => {
    expect(configureForWechatEnvVersion("develop")).toMatchObject({
      releaseChannel: "development",
    });
    expect(configureForWechatEnvVersion("trial")).toMatchObject({
      releaseChannel: "staging",
    });
    const release = configureForWechatEnvVersion("release");
    expect(release).toMatchObject({ releaseChannel: "production" });
    expect(release).not.toHaveProperty("dataMode");
  });

  it("requires a non-blank token for the API repository", () => {
    expect(() => createCoachRepository("   ")).toThrow(
      "Authenticated coach data requires a valid access token",
    );
    expect(createCoachRepository("token")).toBeDefined();
  });
});
