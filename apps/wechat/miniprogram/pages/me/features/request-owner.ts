import { sameSessionOwner, type ActiveAuthSession } from "../../../auth/session";

export interface MePageRequestLease {
  readonly lane: string;
  readonly pageGeneration: number;
  readonly requestGeneration: number;
  readonly owner: ActiveAuthSession;
}

export class MePageRequestOwner {
  readonly owner: ActiveAuthSession;

  private visible = false;
  private disposed = false;
  private pageGeneration = 0;
  private requestGeneration = 0;
  private readonly latestByLane = new Map<string, number>();

  constructor(session: ActiveAuthSession) {
    this.owner = sessionOwner(session);
  }

  show(current: ActiveAuthSession | undefined): boolean {
    if (this.disposed) return false;
    if (!sameSessionOwner(current, this.owner)) {
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

  begin(lane: string, current: ActiveAuthSession | undefined): MePageRequestLease | null {
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

  owns(lease: MePageRequestLease | null, current: ActiveAuthSession | undefined): boolean {
    return (
      lease !== null &&
      this.active(current) &&
      sameSessionOwner(lease.owner, this.owner) &&
      lease.pageGeneration === this.pageGeneration &&
      this.latestByLane.get(lease.lane) === lease.requestGeneration
    );
  }

  active(current: ActiveAuthSession | undefined): boolean {
    return this.visible && !this.disposed && sameSessionOwner(current, this.owner);
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
