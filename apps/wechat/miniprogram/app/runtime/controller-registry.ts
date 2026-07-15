export interface PageControllerRuntime<TController> {
  controller: TController | null;
  disposed: boolean;
}

export interface DisposableController {
  dispose(): void;
}

export class PageControllerRegistry<
  TPage extends object,
  TController extends DisposableController,
> {
  private readonly runtimes = new WeakMap<TPage, PageControllerRuntime<TController>>();

  attach(page: TPage): PageControllerRuntime<TController> {
    this.detach(page);
    const runtime: PageControllerRuntime<TController> = {
      controller: null,
      disposed: false,
    };
    this.runtimes.set(page, runtime);
    return runtime;
  }

  get(page: TPage): PageControllerRuntime<TController> | undefined {
    return this.runtimes.get(page);
  }

  owns(page: TPage, runtime: PageControllerRuntime<TController>): boolean {
    return this.runtimes.get(page) === runtime && !runtime.disposed;
  }

  detach(page: TPage): void {
    const runtime = this.runtimes.get(page);
    if (!runtime) return;
    runtime.disposed = true;
    runtime.controller?.dispose();
    runtime.controller = null;
    this.runtimes.delete(page);
  }
}
