import { afterEach, describe, expect, it, vi } from "vitest";
import { prototypeToday } from "../miniprogram/data/prototype-data";
import {
  capacityCommandFingerprint,
  capacityDraftFromVersion,
  capacityScaleLabel,
  capacityWritePolicy,
  createCapacityIdempotencyKey,
  formatCapacityIdempotencyKey,
  prepareCapacityCommand,
} from "../miniprogram/pages/today/capacity";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("today capacity command", () => {
  it("creates a version-aware command from all four user inputs", () => {
    const draft = capacityDraftFromVersion(prototypeToday.capacity);
    const prepared = prepareCapacityCommand(
      { ...draft, availableMinutes: "65", energy: 2, stress: 4, confidenceLevel: 3 },
      prototypeToday.capacity.capacityVersionId,
    );

    expect(prepared).toEqual({
      command: {
        basedOnCapacityVersionId: prototypeToday.capacity.capacityVersionId,
        availableMinutes: 65,
        energy: 2,
        stress: 4,
        confidenceLevel: 3,
      },
      error: "",
    });
    if (!prepared.command) throw new Error("expected a valid capacity command");
    expect(capacityCommandFingerprint(prepared.command)).toContain(
      prototypeToday.capacity.capacityVersionId,
    );
  });

  it.each(["", "12.5", "-1", "1441", "12m"])(
    "rejects invalid available minutes %s before writing",
    (availableMinutes) => {
      const prepared = prepareCapacityCommand(
        { ...capacityDraftFromVersion(prototypeToday.capacity), availableMinutes },
        prototypeToday.capacity.capacityVersionId,
      );

      expect(prepared.command).toBeNull();
      expect(prepared.error).not.toBe("");
    },
  );

  it("rejects invalid scales and missing immutable base versions", () => {
    const draft = capacityDraftFromVersion(prototypeToday.capacity);

    expect(prepareCapacityCommand({ ...draft, energy: 0 }, "capacity-v1").error).toContain("精力");
    expect(prepareCapacityCommand(draft, "").error).toContain("版本");
  });

  it("never enables writes for prototype data or unauthenticated API data", () => {
    expect(capacityWritePolicy("prototype", "token")).toMatchObject({ allowed: false });
    expect(capacityWritePolicy("prototype", "token").notice).toContain("不会伪造");
    expect(capacityWritePolicy("api")).toMatchObject({ allowed: false });
    expect(capacityWritePolicy("api").notice).toContain("登录");
    expect(capacityWritePolicy("api", "token")).toMatchObject({ allowed: true });
  });

  it("describes the current energy, stress, and confidence scales", () => {
    expect(capacityScaleLabel("energy", 1)).toBe("极低");
    expect(capacityScaleLabel("stress", 5)).toBe("很高");
    expect(capacityScaleLabel("confidence", 4)).toBe("把握较高");
  });

  it("formats a cryptographically sourced UUID v4 idempotency key", async () => {
    const entropy = new Uint8Array(16);
    expect(formatCapacityIdempotencyKey(entropy.buffer)).toBe(
      "capacity-00000000-0000-4000-8000-000000000000",
    );
    const getRandomValues = vi.fn().mockResolvedValue({ randomValues: entropy.buffer });
    vi.stubGlobal("wx", { getRandomValues });

    await expect(createCapacityIdempotencyKey()).resolves.toMatch(
      /^capacity-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(getRandomValues).toHaveBeenCalledWith({ length: 16 });
  });
});
