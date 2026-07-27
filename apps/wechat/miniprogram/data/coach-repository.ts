import type { TodayDashboard } from "../api/generated/types.gen";
import { ApiClient } from "../api/client";
import { PlanningApi } from "../api/planning";
import { environment } from "../config/environment";

export interface CoachRepository {
  getToday(): Promise<TodayDashboard>;
}

class ApiCoachRepository implements CoachRepository {
  private readonly planning: PlanningApi;

  constructor(private readonly client: ApiClient) {
    this.planning = new PlanningApi(client);
  }

  async getToday(): Promise<TodayDashboard> {
    return (await this.planning.getToday()).data;
  }
}

export function createCoachRepository(accessToken?: string): CoachRepository {
  const token = accessToken?.trim();
  if (!token) throw new CoachAuthenticationRequiredError();
  return new ApiCoachRepository(new ApiClient(environment.apiBaseUrl, token));
}

export class CoachAuthenticationRequiredError extends Error {
  readonly code = "authentication_required";

  constructor() {
    super("Authenticated coach data requires a valid access token");
    this.name = "CoachAuthenticationRequiredError";
  }
}
