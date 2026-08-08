export interface OwnedRuntime {
  ownerUserId: string;
  requestEpoch: number;
  disposed: boolean;
}

export class CommunityPageRuntimeRegistry<Page extends object, Runtime extends OwnedRuntime> {
  private readonly runtimes = new WeakMap<Page, Runtime>();

  attach(page: Page, runtime: Runtime): Runtime {
    this.detach(page);
    this.runtimes.set(page, runtime);
    return runtime;
  }

  get(page: Page): Runtime | undefined {
    return this.runtimes.get(page);
  }

  owns(page: Page, runtime: Runtime, epoch?: number): boolean {
    return (
      !runtime.disposed &&
      this.runtimes.get(page) === runtime &&
      (epoch === undefined || runtime.requestEpoch === epoch)
    );
  }

  detach(page: Page): void {
    const runtime = this.runtimes.get(page);
    if (runtime) {
      runtime.disposed = true;
      runtime.requestEpoch += 1;
      this.runtimes.delete(page);
    }
  }
}
