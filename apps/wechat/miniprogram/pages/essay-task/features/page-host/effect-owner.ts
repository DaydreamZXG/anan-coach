import { sameSessionOwner, type ActiveAuthSession } from "../../../../auth/session";

export type EssayTaskEffectLane =
  | "workspace"
  | "submission"
  | "review-poll"
  | "voice"
  | "handwriting";

export type EssayTaskVisibilityPolicy = "visible-only" | "background-settle";

export const essayTaskEffectPolicies: Readonly<
  Record<EssayTaskEffectLane, EssayTaskVisibilityPolicy>
> = {
  workspace: "visible-only",
  submission: "background-settle",
  "review-poll": "visible-only",
  voice: "visible-only",
  handwriting: "visible-only",
};

export interface EssayTaskEffectLease {
  readonly lane: EssayTaskEffectLane;
  readonly laneGeneration: number;
  readonly owner: ActiveAuthSession;
  readonly page: object;
  readonly visibilityPolicy: EssayTaskVisibilityPolicy;
}

export class EssayTaskEffectOwner {
  readonly owner: ActiveAuthSession;

  private visible = false;
  private disposed = false;
  private readonly laneGenerations = new Map<EssayTaskEffectLane, number>();
  private readonly activeLaneGenerations = new Map<EssayTaskEffectLane, number>();

  constructor(
    private readonly page: object,
    session: ActiveAuthSession,
  ) {
    this.owner = sessionOwner(session);
  }

  show(page: object, current: ActiveAuthSession | undefined): boolean {
    if (!this.matches(page, current)) {
      this.unload();
      return false;
    }
    this.visible = true;
    return true;
  }

  hide(): Set<EssayTaskEffectLane> {
    if (this.disposed) return new Set();
    this.visible = false;
    const invalidated = new Set<EssayTaskEffectLane>();
    for (const lane of essayTaskEffectLanes) {
      if (essayTaskEffectPolicies[lane] !== "visible-only") continue;
      if (this.invalidate(lane)) invalidated.add(lane);
    }
    return invalidated;
  }

  unload(): Set<EssayTaskEffectLane> {
    if (this.disposed) return new Set();
    this.visible = false;
    this.disposed = true;
    return this.invalidateAll();
  }

  begin(
    page: object,
    lane: EssayTaskEffectLane,
    current: ActiveAuthSession | undefined,
  ): EssayTaskEffectLease | null {
    if (!this.visible || !this.matches(page, current)) return null;
    const laneGeneration = (this.laneGenerations.get(lane) ?? 0) + 1;
    this.laneGenerations.set(lane, laneGeneration);
    this.activeLaneGenerations.set(lane, laneGeneration);
    return {
      lane,
      laneGeneration,
      owner: this.owner,
      page: this.page,
      visibilityPolicy: essayTaskEffectPolicies[lane],
    };
  }

  owns(
    page: object,
    lease: EssayTaskEffectLease | null,
    current: ActiveAuthSession | undefined,
  ): boolean {
    return (
      lease !== null &&
      this.matches(page, current) &&
      lease.page === this.page &&
      sameSessionOwner(lease.owner, this.owner) &&
      this.activeLaneGenerations.get(lease.lane) === lease.laneGeneration &&
      (lease.visibilityPolicy === "background-settle" || this.visible)
    );
  }

  hasActive(lane: EssayTaskEffectLane): boolean {
    return !this.disposed && this.activeLaneGenerations.has(lane);
  }

  invalidate(lane: EssayTaskEffectLane): boolean {
    const active = this.activeLaneGenerations.delete(lane);
    this.laneGenerations.set(lane, (this.laneGenerations.get(lane) ?? 0) + 1);
    return active;
  }

  invalidateAll(): Set<EssayTaskEffectLane> {
    const invalidated = new Set(this.activeLaneGenerations.keys());
    for (const lane of essayTaskEffectLanes) this.invalidate(lane);
    return invalidated;
  }

  private matches(page: object, current: ActiveAuthSession | undefined): boolean {
    return !this.disposed && page === this.page && sameSessionOwner(current, this.owner);
  }
}

const essayTaskEffectLanes: readonly EssayTaskEffectLane[] = [
  "workspace",
  "submission",
  "review-poll",
  "voice",
  "handwriting",
];

function sessionOwner(session: ActiveAuthSession): ActiveAuthSession {
  return {
    userId: session.userId,
    accessToken: session.accessToken,
    generation: session.generation,
  };
}
