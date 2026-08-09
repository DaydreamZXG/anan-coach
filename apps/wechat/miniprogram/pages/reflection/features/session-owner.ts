import { sameSessionOwner, type ActiveAuthSession } from "../../../auth/session";

export interface ReflectionActivityCleanup {
  clearCausePoll(): void;
  stopRecorder(): void;
  stopVoicePreview(): void;
  stopSpeechPreview(): void;
  stopReplay(): void;
}

/**
 * Exact owner for every asynchronous reflection effect.
 *
 * Presentation metadata is intentionally excluded. A refreshed token or a new
 * authentication generation invalidates the old page even when userId is the same.
 */
export class ReflectionSessionLifecycle {
  readonly owner: ActiveAuthSession;

  private visible = true;
  private disposed = false;
  private initialShowPending = true;
  private requestGeneration = 0;
  private mediaGeneration = 0;

  constructor(session: ActiveAuthSession) {
    this.owner = sessionOwner(session);
  }

  show(current: ActiveAuthSession | undefined): boolean {
    if (this.disposed) return false;
    if (!sameSessionOwner(current, this.owner)) {
      this.visible = false;
      this.disposed = true;
      this.invalidateEffects();
      return false;
    }
    if (this.initialShowPending) {
      this.initialShowPending = false;
      this.visible = true;
      return true;
    }
    this.visible = true;
    this.invalidateEffects();
    return true;
  }

  hide(cleanup: ReflectionActivityCleanup): void {
    if (this.disposed) return;
    this.initialShowPending = false;
    this.visible = false;
    this.invalidateEffects();
    runCleanup(cleanup);
  }

  unload(cleanup: ReflectionActivityCleanup): void {
    if (this.disposed) return;
    this.initialShowPending = false;
    this.visible = false;
    this.disposed = true;
    this.invalidateEffects();
    runCleanup(cleanup);
  }

  invalidate(cleanup: ReflectionActivityCleanup): void {
    if (!this.disposed) {
      this.visible = false;
      this.disposed = true;
      this.invalidateEffects();
    }
    runCleanup(cleanup);
  }

  beginRequest(current: ActiveAuthSession | undefined): number | null {
    if (!this.active(current)) return null;
    this.requestGeneration += 1;
    return this.requestGeneration;
  }

  ownsRequest(generation: number | null, current: ActiveAuthSession | undefined): boolean {
    return generation !== null && this.active(current) && generation === this.requestGeneration;
  }

  beginMedia(current: ActiveAuthSession | undefined): number | null {
    if (!this.active(current)) return null;
    this.mediaGeneration += 1;
    return this.mediaGeneration;
  }

  ownsMedia(generation: number | null, current: ActiveAuthSession | undefined): boolean {
    return generation !== null && this.active(current) && generation === this.mediaGeneration;
  }

  active(current: ActiveAuthSession | undefined): boolean {
    return this.visible && !this.disposed && sameSessionOwner(current, this.owner);
  }

  ownerMatches(current: ActiveAuthSession | undefined): boolean {
    return sameSessionOwner(current, this.owner);
  }

  isDisposed(): boolean {
    return this.disposed;
  }

  private invalidateEffects(): void {
    this.requestGeneration += 1;
    this.mediaGeneration += 1;
  }
}

function sessionOwner(session: ActiveAuthSession): ActiveAuthSession {
  return {
    userId: session.userId,
    accessToken: session.accessToken,
    generation: session.generation,
  };
}

function runCleanup(cleanup: ReflectionActivityCleanup): void {
  cleanup.clearCausePoll();
  cleanup.stopRecorder();
  cleanup.stopVoicePreview();
  cleanup.stopSpeechPreview();
  cleanup.stopReplay();
}
