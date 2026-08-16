import { describe, expect, it } from "vitest";

import { arrayPropertyItems } from "../subpackage-shared-src/task-runtime/component-property";

describe("WeChat component property contracts", () => {
  it("keeps ordinary arrays unchanged", () => {
    const items = [{ id: "first" }, { id: "second" }];

    expect(arrayPropertyItems(items)).toBe(items);
  });

  it("restores the numeric-keyed object emitted at a custom-component boundary", () => {
    expect(
      arrayPropertyItems({
        0: { id: "first" },
        1: { id: "second" },
      }),
    ).toEqual([{ id: "first" }, { id: "second" }]);
  });

  it.each([
    null,
    "not-an-array",
    { label: "ordinary object" },
    { 0: "first", 2: "gap" },
    { 0: "first", extra: "mixed" },
  ])("rejects values that are not a contiguous array representation", (value) => {
    expect(arrayPropertyItems(value)).toEqual([]);
  });
});
