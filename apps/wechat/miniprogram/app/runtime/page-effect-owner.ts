import { sameSessionOwner, type ActiveAuthSession } from "../../auth/session";

export interface PageEffectLease {
  readonly lane: string;
  readonly pageGeneration: number;
  readonly requestGeneration: number;
  readonly owner: ActiveAuthSession;
}

export class PageEffectOwner {
  readonly owner: ActiveAuthSession;

  private visible = false;
  private disposed = false;
  private pageGeneration = 0;
  private requestGeneration = 0;
  private readonly latestByLane = new Map<string, number>();

  constructor(session: ActiveAuthSession) {
    this.owner = sessionOwner(session);
  }

  matches(current: ActiveAuthSession | undefined): boolean {
    return !this.disposed && sameSessionOwner(current, this.owner);
  }

  show(current: ActiveAuthSession | undefined): boolean {
    if (!this.matches(current)) {
      this.unload();
      return false;
    }
    this.visible = true;
    this.invalidate();
    return true;
  }

  hide(): void {
    if (this.disposed) return;
    this.visible = false;
    this.invalidate();
  }

  unload(): void {
    if (this.disposed) return;
    this.visible = false;
    this.disposed = true;
    this.invalidate();
  }

  begin(lane: string, current: ActiveAuthSession | undefined): PageEffectLease | null {
    if (!lane || !this.active(current)) return null;
    this.requestGeneration += 1;
    this.latestByLane.set(lane, this.requestGeneration);
    return {
      lane,
      pageGeneration: this.pageGeneration,
      requestGeneration: this.requestGeneration,
      owner: this.owner,
    };
  }

  owns(lease: PageEffectLease | null, current: ActiveAuthSession | undefined): boolean {
    return (
      lease !== null &&
      this.active(current) &&
      sameSessionOwner(lease.owner, this.owner) &&
      lease.pageGeneration === this.pageGeneration &&
      this.latestByLane.get(lease.lane) === lease.requestGeneration
    );
  }

  active(current: ActiveAuthSession | undefined): boolean {
    return this.visible && this.matches(current);
  }

  private invalidate(): void {
    this.pageGeneration += 1;
    this.latestByLane.clear();
  }
}

function sessionOwner(session: ActiveAuthSession): ActiveAuthSession {
  return {
    userId: session.userId,
    accessToken: session.accessToken,
    generation: session.generation,
  };
}
