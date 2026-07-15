import type { ApiEnvelope } from "../domain/contracts";
import type {
  DailyCapacityCommand,
  DailyCapacityCommandResult,
  TaskExecutionCommand,
  TaskExecutionCommandResult,
  TodayDashboard,
} from "./generated/types.gen";
import type { ApiClient } from "./client";

export class PlanningApi {
  constructor(private readonly client: ApiClient) {}

  getToday(): Promise<ApiEnvelope<TodayDashboard>> {
    return this.client.get<TodayDashboard>("/api/v2/plans/today");
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
