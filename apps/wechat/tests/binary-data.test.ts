import { describe, expect, it } from "vitest";

import { normalizeArrayBuffer } from "../miniprogram/platform/binary-data";

describe("native binary data normalization", () => {
  it("keeps a local ArrayBuffer", () => {
    const source = new Uint8Array([1, 2, 3]).buffer;

    expect(normalizeArrayBuffer(source)).toBe(source);
  });

  it("copies a typed-array slice into a standalone ArrayBuffer", () => {
    const source = new Uint8Array([9, 1, 2, 3, 8]).subarray(1, 4);
    const normalized = normalizeArrayBuffer(source);

    expect(normalized).toBeInstanceOf(ArrayBuffer);
    expect([...new Uint8Array(normalized ?? new ArrayBuffer(0))]).toEqual([1, 2, 3]);
  });

  it("rejects text and arbitrary objects", () => {
    expect(normalizeArrayBuffer("not binary")).toBeNull();
    expect(normalizeArrayBuffer({ data: [1, 2, 3] })).toBeNull();
  });
});
