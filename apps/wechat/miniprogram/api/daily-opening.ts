import type { ApiEnvelope } from "../domain/contracts";
import type {
  CoffeeDecisionCommand,
  CoffeeDecisionResult,
  DailyOpeningView,
  ImpressionCommand,
  ImpressionResult,
  OpeningPreferenceCommand,
  OpeningPreferenceView,
  WeatherLocationCommand,
  WeatherLocationView,
} from "./generated/types.gen";
import type { ApiClient } from "./client";

const ROOT = "/api/v2/plans/daily-opening";

export class DailyOpeningApi {
  constructor(private readonly client: ApiClient) {}

  get(): Promise<ApiEnvelope<DailyOpeningView>> {
    return this.client.get<DailyOpeningView>(ROOT);
  }

  getPreference(): Promise<ApiEnvelope<OpeningPreferenceView>> {
    return this.client.get<OpeningPreferenceView>(`${ROOT}/preferences`);
  }

  updatePreference(
    command: OpeningPreferenceCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<OpeningPreferenceView>> {
    return this.client.put<OpeningPreferenceView>(`${ROOT}/preferences`, command, {
      idempotencyKey,
    });
  }

  updateWeatherLocation(
    command: WeatherLocationCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<WeatherLocationView>> {
    return this.client.put<WeatherLocationView>(`${ROOT}/weather-location`, command, {
      idempotencyKey,
    });
  }

  recordImpression(
    command: ImpressionCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<ImpressionResult>> {
    return this.client.post<ImpressionResult>(`${ROOT}/impressions`, command, {
      idempotencyKey,
    });
  }

  recordCoffeeDecision(
    command: CoffeeDecisionCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<CoffeeDecisionResult>> {
    return this.client.post<CoffeeDecisionResult>(`${ROOT}/coffee-decisions`, command, {
      idempotencyKey,
    });
  }
}
