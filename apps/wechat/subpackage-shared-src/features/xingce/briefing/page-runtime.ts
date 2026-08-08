import type {
  XingceBriefingMode,
  XingceLearningApi,
  XingceTaskBriefing,
} from "../../../api/xingce-learning";

export interface XingceBriefingPageRuntime {
  api: XingceLearningApi;
  ownerUserId: string;
  planTaskId: string;
  requestedMode: XingceBriefingMode;
  briefing: XingceTaskBriefing | null;
  requestEpoch: number;
  requestInFlight: boolean;
  disposed: boolean;
  hadLoadFailure: boolean;
}

export class XingceBriefingPageRuntimeRegistry<Page extends object> {
  private readonly runtimes = new WeakMap<Page, XingceBriefingPageRuntime>();

  attach(page: Page, runtime: XingceBriefingPageRuntime): XingceBriefingPageRuntime {
    const current = this.runtimes.get(page);
    if (current) this.dispose(current);
    this.runtimes.set(page, runtime);
    return runtime;
  }

  get(page: Page): XingceBriefingPageRuntime | undefined {
    return this.runtimes.get(page);
  }

  owns(page: Page, runtime: XingceBriefingPageRuntime, epoch?: number): boolean {
    return (
      this.runtimes.get(page) === runtime &&
      !runtime.disposed &&
      (epoch === undefined || runtime.requestEpoch === epoch)
    );
  }

  detach(page: Page): void {
    const runtime = this.runtimes.get(page);
    if (runtime) this.dispose(runtime);
    this.runtimes.delete(page);
  }

  private dispose(runtime: XingceBriefingPageRuntime): void {
    runtime.disposed = true;
    runtime.requestEpoch += 1;
    runtime.requestInFlight = false;
    runtime.briefing = null;
  }
}
