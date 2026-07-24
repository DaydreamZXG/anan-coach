import { describe, expect, it } from "vitest";
import { sha256Hex } from "../../miniprogram/api/sha256";

describe("mini program SHA-256", () => {
  it("matches the standard empty and abc vectors", () => {
    expect(sha256Hex(new ArrayBuffer(0))).toBe(
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    );
    expect(sha256Hex(new Uint8Array([0x61, 0x62, 0x63]).buffer)).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });
});
