export interface DisposableController {
  dispose(): void;
}

export class ExternalLearningPageRuntimeRegistry<
  TPage extends object,
  TController extends DisposableController,
> {
  private readonly controllers = new WeakMap<TPage, TController>();

  attach(page: TPage, controller: TController): TController {
    this.detach(page);
    this.controllers.set(page, controller);
    return controller;
  }

  get(page: TPage): TController | undefined {
    return this.controllers.get(page);
  }

  owns(page: TPage, controller: TController): boolean {
    return this.controllers.get(page) === controller;
  }

  detach(page: TPage): void {
    const controller = this.controllers.get(page);
    if (!controller) return;
    controller.dispose();
    this.controllers.delete(page);
  }
}
