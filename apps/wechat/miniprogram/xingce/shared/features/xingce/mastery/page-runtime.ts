import type { XingceLearningApi, XingceMasteryQueueBucket } from "../../../api/xingce-learning";

export interface XingceMasteryPageRuntime {
  api: XingceLearningApi;
  ownerUserId: string;
  bucket: XingceMasteryQueueBucket;
  nextCursor: string | null;
  requestEpoch: number;
  requestInFlight: boolean;
  loadingMore: boolean;
  autoSelectNonEmpty: boolean;
  disposed: boolean;
}

export class XingceMasteryPageRuntimeRegistry<Page extends object> {
  private readonly runtimes = new WeakMap<Page, XingceMasteryPageRuntime>();

  attach(page: Page, runtime: XingceMasteryPageRuntime): XingceMasteryPageRuntime {
    const current = this.runtimes.get(page);
    if (current) this.dispose(current);
    this.runtimes.set(page, runtime);
    return runtime;
  }

  get(page: Page): XingceMasteryPageRuntime | undefined {
    return this.runtimes.get(page);
  }

  owns(page: Page, runtime: XingceMasteryPageRuntime, epoch?: number): boolean {
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

  private dispose(runtime: XingceMasteryPageRuntime): void {
    runtime.disposed = true;
    runtime.requestEpoch += 1;
    runtime.requestInFlight = false;
    runtime.loadingMore = false;
  }
}
