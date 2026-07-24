import { describe, expect, it, vi } from "vitest";
import { ExternalLearningPageRuntimeRegistry } from "../../miniprogram/features/external-learning/page-runtime";

describe("external learning page runtime ownership", () => {
  it("disposes a replaced controller and isolates two page instances", () => {
    const registry = new ExternalLearningPageRuntimeRegistry<object, { dispose(): void }>();
    const pageA = {};
    const pageB = {};
    const first = { dispose: vi.fn() };
    const replacement = { dispose: vi.fn() };
    const other = { dispose: vi.fn() };

    registry.attach(pageA, first);
    registry.attach(pageB, other);
    registry.attach(pageA, replacement);

    expect(first.dispose).toHaveBeenCalledOnce();
    expect(registry.owns(pageA, replacement)).toBe(true);
    expect(registry.owns(pageB, other)).toBe(true);
    registry.detach(pageA);
    expect(replacement.dispose).toHaveBeenCalledOnce();
    expect(other.dispose).not.toHaveBeenCalled();
  });
});
