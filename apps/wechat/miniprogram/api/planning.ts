import type { ApiEnvelope } from "../domain/contracts";
import type {
  DailyCapacityCommand,
  DailyCapacityCommandResult,
  DailyCapacityPreview,
  DailySettlementView,
  PlanBasisView,
  PlanChangeHistory,
  TaskExecutionCommand,
  TaskExecutionCommandResult,
  TodayDashboard,
  WeeklyPlanVersion,
} from "./generated/types.gen";
import type { ApiClient } from "./client";

export class PlanningApi {
  constructor(private readonly client: ApiClient) {}

  getToday(): Promise<ApiEnvelope<TodayDashboard>> {
    return this.client.get<TodayDashboard>("/api/v2/plans/today");
  }

  getTodayBasis(): Promise<ApiEnvelope<PlanBasisView>> {
    return this.client.get<PlanBasisView>("/api/v2/plans/today/basis");
  }

  getTodaySettlement(): Promise<ApiEnvelope<DailySettlementView>> {
    return this.client.get<DailySettlementView>("/api/v2/plans/today/settlement");
  }

  getWeek(): Promise<ApiEnvelope<WeeklyPlanVersion>> {
    return this.client.get<WeeklyPlanVersion>("/api/v2/plans/week");
  }

  getHistory(): Promise<ApiEnvelope<PlanChangeHistory>> {
    return this.client.get<PlanChangeHistory>("/api/v2/plans/history");
  }

  previewTodayCapacity(command: DailyCapacityCommand): Promise<ApiEnvelope<DailyCapacityPreview>> {
    return this.client.post<DailyCapacityPreview>("/api/v2/plans/today/capacity/preview", command);
  }

  setTodayCapacity(
    command: DailyCapacityCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<DailyCapacityCommandResult>> {
    return this.client.put<DailyCapacityCommandResult>("/api/v2/plans/today/capacity", command, {
      idempotencyKey,
    });
  }

  recordTaskExecution(
    taskId: string,
    command: TaskExecutionCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<TaskExecutionCommandResult>> {
    return this.client.put<TaskExecutionCommandResult>(
      `/api/v2/plans/tasks/${encodeURIComponent(taskId)}/execution`,
      command,
      { idempotencyKey },
    );
  }
}
