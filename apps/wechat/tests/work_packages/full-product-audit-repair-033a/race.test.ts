import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import captureControllerRaw from "../../../miniprogram/pages/capture-import/features/external-learning/capture-controller.ts?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import todaySourceRaw from "../../../miniprogram/pages/today/index.ts?raw";
import { MePageRequestOwner } from "../../../miniprogram/pages/me/features/request-owner";

const session = {
  userId: "user-a",
  accessToken: "token-a",
  generation: "generation-a",
};

describe("full product audit repair 033a race path", () => {
  it("keeps only the newest request owner per visible surface", () => {
    const owner = new MePageRequestOwner(session);
    owner.show(session);
    const first = owner.begin("profile", session);
    const second = owner.begin("profile", session);

    expect(owner.owns(first, session)).toBe(false);
    expect(owner.owns(second, session)).toBe(true);
  });

  it("blocks rapid duplicate task and recovery commands while one is active", () => {
    const today = String(todaySourceRaw);
    expect(today).toContain("if (!taskId || page.data.taskOpeningId) return");
    expect(today).toContain('page.setData({ taskOpeningId: task.id, taskNavigationError: "" })');

    const capture = String(captureControllerRaw);
    expect(capture).toContain("if (!this.canEdit() || this.commandPreparing) return");
    expect(capture).toContain('if (!this.isActive() || this.state.status !== "error") return');
  });
});
