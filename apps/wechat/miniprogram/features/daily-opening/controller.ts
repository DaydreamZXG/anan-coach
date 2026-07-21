import { ApiClientError } from "../../api/client";
import type {
  DailyOpeningView,
  ImpressionCommand,
  OpeningPreferenceCommand,
  WeatherLocationCommand,
} from "../../api/generated/types.gen";
import type { DisposableController } from "../../app/runtime/controller-registry";

export type DailyOpeningStatus = "idle" | "loading" | "ready" | "hidden" | "error";

export interface DailyOpeningState {
  status: DailyOpeningStatus;
  view: DailyOpeningView | null;
  errorMessage: string;
  writing: boolean;
}

export interface DailyOpeningDependencies {
  accessToken(): string | undefined;
  get(accessToken: string): Promise<DailyOpeningView>;
  recordImpression(
    accessToken: string,
    command: ImpressionCommand,
    idempotencyKey: string,
  ): Promise<void>;
  updateWeatherLocation(
    accessToken: string,
    command: WeatherLocationCommand,
    idempotencyKey: string,
  ): Promise<void>;
  updatePreference(
    accessToken: string,
    command: OpeningPreferenceCommand,
    idempotencyKey: string,
  ): Promise<void>;
  declineCoffee(accessToken: string, idempotencyKey: string): Promise<void>;
  createIdempotencyKey(): Promise<string>;
  publish(state: DailyOpeningState): void;
}

const initialState: DailyOpeningState = {
  status: "idle",
  view: null,
  errorMessage: "",
  writing: false,
};

export class DailyOpeningController implements DisposableController {
  private state = initialState;
  private loadInFlight: Promise<void> | null = null;
  private generation = 0;
  private disposed = false;
  private impressionKey: string | null = null;

  constructor(private readonly dependencies: DailyOpeningDependencies) {
    this.publish(initialState);
  }

  load(): Promise<void> {
    if (this.disposed) return Promise.resolve();
    if (this.loadInFlight) return this.loadInFlight;
    const generation = ++this.generation;
    const operation = this.runLoad(generation).finally(() => {
      if (this.loadInFlight === operation) this.loadInFlight = null;
    });
    this.loadInFlight = operation;
    return operation;
  }

  async recordDisplayed(): Promise<void> {
    const token = this.dependencies.accessToken();
    const view = this.state.view;
    if (
      !token ||
      !view ||
      this.state.status !== "ready" ||
      view.displayState === "location_required" ||
      this.impressionKey
    )
      return;
    const command: ImpressionCommand = {
      planVersionId: view.planVersionId,
      preferenceVersionId: view.preference.preferenceVersionId,
      locationVersionId: view.locationVersionId ?? null,
      weatherFingerprint: view.weatherFingerprint ?? null,
      coffeeState: view.coffeeState,
    };
    const key = await this.dependencies.createIdempotencyKey();
    if (this.disposed || this.state.view !== view) return;
    this.impressionKey = key;
    try {
      await this.dependencies.recordImpression(token, command, key);
    } catch {
      this.impressionKey = null;
    }
  }

  async chooseWeatherLocation(command: WeatherLocationCommand): Promise<void> {
    await this.write(async (token, key) => {
      await this.dependencies.updateWeatherLocation(token, command, key);
      await this.reloadAfterWrite();
    });
  }

  async declineCoffee(): Promise<void> {
    await this.write(async (token, key) => {
      await this.dependencies.declineCoffee(token, key);
      const view = this.state.view;
      if (!view) return;
      this.publish({
        ...this.state,
        view: {
          ...view,
          coffeeState: "today_declined",
          coffee: { state: "today_declined", showPrompt: false, prompt: "" },
        },
        writing: false,
      });
    });
  }

  async disableCoffee(): Promise<void> {
    const preference = this.state.view?.preference;
    if (!preference) return;
    await this.write(async (token, key) => {
      await this.dependencies.updatePreference(
        token,
        {
          enabled: preference.enabled,
          weatherEnabled: preference.weatherEnabled,
          coffeePromptEnabled: false,
        },
        key,
      );
      const view = this.state.view;
      if (!view) return;
      this.publish({
        ...this.state,
        view: {
          ...view,
          coffeeState: "disabled",
          coffee: { state: "disabled", showPrompt: false, prompt: "" },
          preference: { ...view.preference, coffeePromptEnabled: false },
        },
        writing: false,
      });
    });
  }

  dispose(): void {
    this.disposed = true;
    this.generation += 1;
    this.loadInFlight = null;
  }

  private async runLoad(generation: number): Promise<void> {
    const token = this.dependencies.accessToken();
    if (!token) {
      this.publish({ ...initialState, status: "hidden" });
      return;
    }
    this.publish({ ...this.state, status: "loading", errorMessage: "" });
    try {
      const view = await this.dependencies.get(token);
      if (!this.isCurrent(generation)) return;
      if (view.displayState === "already_seen" || view.displayState === "disabled") {
        this.publish({ status: "hidden", view, errorMessage: "", writing: false });
        return;
      }
      this.impressionKey = null;
      this.publish({ status: "ready", view, errorMessage: "", writing: false });
    } catch (error) {
      if (!this.isCurrent(generation)) return;
      this.publish({
        status: "error",
        view: null,
        errorMessage:
          error instanceof ApiClientError
            ? `${error.message}（请求 ${error.requestId}）`
            : "每日开场没有加载成功。",
        writing: false,
      });
    }
  }

  private async reloadAfterWrite(): Promise<void> {
    this.loadInFlight = null;
    await this.runLoad(++this.generation);
  }

  private async write(
    action: (accessToken: string, idempotencyKey: string) => Promise<void>,
  ): Promise<void> {
    const token = this.dependencies.accessToken();
    if (!token || this.state.writing || this.disposed) return;
    this.publish({ ...this.state, writing: true, errorMessage: "" });
    try {
      const key = await this.dependencies.createIdempotencyKey();
      await action(token, key);
    } catch (error) {
      this.publish({
        ...this.state,
        writing: false,
        errorMessage:
          error instanceof ApiClientError
            ? `${error.message}（请求 ${error.requestId}）`
            : "操作没有写入，请重试。",
      });
    }
  }

  private isCurrent(generation: number): boolean {
    return !this.disposed && generation === this.generation;
  }

  private publish(state: DailyOpeningState): void {
    this.state = state;
    this.dependencies.publish(state);
  }
}
