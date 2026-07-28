import type { QuestionFavoritesApi } from "../../../api/question-favorites";
import type { ActiveAuthSession } from "../../../auth/session";

export interface QuestionFavoritesPageRuntime {
  api: QuestionFavoritesApi;
  ownerSession: ActiveAuthSession;
  nextCursor: string | null;
  requestEpoch: number;
  requestInFlight: boolean;
  mutationEpoch: number;
  mutationInFlight: Set<string>;
  refreshAfterRequest: boolean;
  visible: boolean;
  disposed: boolean;
}

export class QuestionFavoritesPageRuntimeRegistry<TPage extends object> {
  private readonly values = new WeakMap<TPage, QuestionFavoritesPageRuntime>();

  attach(
    page: TPage,
    input: Pick<QuestionFavoritesPageRuntime, "api" | "ownerSession">,
  ): QuestionFavoritesPageRuntime {
    this.detach(page);
    const runtime: QuestionFavoritesPageRuntime = {
      ...input,
      nextCursor: null,
      requestEpoch: 0,
      requestInFlight: false,
      mutationEpoch: 0,
      mutationInFlight: new Set(),
      refreshAfterRequest: false,
      visible: true,
      disposed: false,
    };
    this.values.set(page, runtime);
    return runtime;
  }

  get(page: TPage): QuestionFavoritesPageRuntime | undefined {
    return this.values.get(page);
  }

  attached(page: TPage, runtime: QuestionFavoritesPageRuntime): boolean {
    return this.values.get(page) === runtime && !runtime.disposed;
  }

  owns(page: TPage, runtime: QuestionFavoritesPageRuntime, requestEpoch?: number): boolean {
    return (
      this.values.get(page) === runtime &&
      !runtime.disposed &&
      runtime.visible &&
      (requestEpoch === undefined || runtime.requestEpoch === requestEpoch)
    );
  }

  detach(page: TPage): void {
    const runtime = this.values.get(page);
    if (!runtime) return;
    runtime.disposed = true;
    runtime.visible = false;
    runtime.requestEpoch += 1;
    runtime.mutationEpoch += 1;
    runtime.mutationInFlight.clear();
    this.values.delete(page);
  }
}
