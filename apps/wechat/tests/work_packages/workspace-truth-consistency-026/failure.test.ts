import { describe, expect, it } from "vitest";

import { ApiClientError } from "../../../miniprogram/api/client";
import { loadAbilityGoal } from "../../../miniprogram/pages/ability/features/goal-load";

describe("workspace truth consistency failure isolation", () => {
  it("keeps missing, authentication and upstream failures semantically distinct", async () => {
    await expect(
      loadAbilityGoal(() => Promise.reject(new ApiClientError("not found", 404, "req-404"))),
    ).resolves.toEqual({ kind: "missing" });

    await expect(
      loadAbilityGoal(() => Promise.reject(new ApiClientError("temporary", 503, "req-503"))),
    ).resolves.toEqual({ kind: "failed", message: "temporary", requestId: "req-503" });

    const authentication = new ApiClientError("expired", 401, "req-401");
    await expect(loadAbilityGoal(() => Promise.reject(authentication))).rejects.toBe(
      authentication,
    );
  });
});
