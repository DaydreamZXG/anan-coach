import { sameSessionOwner, type ActiveAuthSession } from "../../../../auth/session";

export interface PrivateKnowledgeRequestLease {
  readonly lane: string;
  readonly pageGeneration: number;
  readonly requestGeneration: number;
  readonly owner: ActiveAuthSession;
}

interface PendingDelay {
  timer: ReturnType<typeof setTimeout>;
  resolve: (current: boolean) => void;
}

export class PrivateKnowledgeRequestOwner {
  readonly owner: ActiveAuthSession;

  private visible = false;
  private disposed = false;
  private pageGeneration = 0;
  private requestGeneration = 0;
  private readonly latestByLane = new Map<string, number>();
  private readonly delays = new Set<PendingDelay>();

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

  begin(lane: string, current: ActiveAuthSession | undefined): PrivateKnowledgeRequestLease | null {
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

  owns(
    lease: PrivateKnowledgeRequestLease | null,
    current: ActiveAuthSession | undefined,
  ): boolean {
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

  delay(
    milliseconds: number,
    lease: PrivateKnowledgeRequestLease | null,
    current: ActiveAuthSession | undefined,
  ): Promise<boolean> {
    if (!this.owns(lease, current)) return Promise.resolve(false);
    return new Promise((resolve) => {
      const pending: PendingDelay = {
        timer: setTimeout(
          () => {
            this.delays.delete(pending);
            resolve(this.owns(lease, current));
          },
          Math.max(0, milliseconds),
        ),
        resolve,
      };
      this.delays.add(pending);
    });
  }

  private invalidate(): void {
    this.pageGeneration += 1;
    this.latestByLane.clear();
    for (const pending of this.delays) {
      clearTimeout(pending.timer);
      pending.resolve(false);
    }
    this.delays.clear();
  }
}

function sessionOwner(session: ActiveAuthSession): ActiveAuthSession {
  return {
    userId: session.userId,
    accessToken: session.accessToken,
    generation: session.generation,
  };
}
