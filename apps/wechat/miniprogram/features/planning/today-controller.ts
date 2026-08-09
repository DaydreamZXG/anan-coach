import { ApiClientError } from "../../api/client";
import type {
  DailyCapacityCommand,
  DailyCapacityCommandResult,
  OnboardingProgress,
  TodayDashboard,
} from "../../api/generated/types.gen";
import type { AuthSession } from "../../auth/session";

export type TodayCapacitySubmission = "idle" | "submitting" | "success" | "error" | "conflict";

export interface TodayControllerState {
  status: "loading" | "ready" | "error" | "redirecting";
  dashboard: TodayDashboard | null;
  errorMessage: string;
  capacitySubmission: TodayCapacitySubmission;
  capacityMessage: string;
  capacityConflictLocked: boolean;
}

export interface TodayControllerDependencies {
  currentSession(): AuthSession | undefined;
  getProgress(accessToken: string): Promise<OnboardingProgress>;
  getToday(accessToken: string): Promise<TodayDashboard>;
  setCapacity(
    accessToken: string,
    command: DailyCapacityCommand,
    idempotencyKey: string,
  ): Promise<DailyCapacityCommandResult>;
  createIdempotencyKey(): Promise<string>;
  clearSession(): void;
  reauthenticate(): Promise<void>;
  navigateOnboarding(nextStep: Exclude<OnboardingProgress["nextStep"], "workspace">): Promise<void>;
  publish(state: TodayControllerState): void;
}

interface CapacityAttempt {
  fingerprint: string;
  idempotencyKey: string;
}

const initialState: TodayControllerState = {
  status: "loading",
  dashboard: null,
  errorMessage: "",
  capacitySubmission: "idle",
  capacityMessage: "",
  capacityConflictLocked: false,
};

export class TodayController {
  private state: TodayControllerState = initialState;
  private capacityAttempt: CapacityAttempt | null = null;
  private conflictedCapacityVersionId: string | null = null;
  private dashboardOwnerSessionKey: string | null = null;
  private progressVerifiedSessionKey: string | null = null;
  private loadInFlight: Promise<void> | null = null;
  private loadInFlightSessionKey: string | null = null;
  private generation = 0;
  private disposed = false;

  constructor(private readonly dependencies: TodayControllerDependencies) {
    this.publish(initialState);
  }

  load(): Promise<void> {
    if (this.disposed) return Promise.resolve();
    const session = this.dependencies.currentSession();
    const sessionKey = authSessionKey(session);
    if (this.loadInFlight && this.loadInFlightSessionKey === sessionKey) {
      return this.loadInFlight;
    }
    if (this.loadInFlight) {
      this.generation += 1;
      this.loadInFlight = null;
      this.loadInFlightSessionKey = null;
    }
    const generation = ++this.generation;
    const operation = this.runLoad(generation, session).finally(() => {
      if (this.loadInFlight === operation) {
        this.loadInFlight = null;
        this.loadInFlightSessionKey = null;
      }
    });
    this.loadInFlight = operation;
    this.loadInFlightSessionKey = sessionKey;
    return operation;
  }

  async submitCapacity(command: DailyCapacityCommand): Promise<void> {
    if (this.disposed || this.state.capacitySubmission === "submitting") return;
    if (this.state.capacityConflictLocked) return;
    const generation = this.generation;
    const session = this.dependencies.currentSession();
    if (!session) {
      await this.routeToRealAuthentication();
      return;
    }
    const dashboard = this.state.dashboard;
    const sessionKey = authSessionKey(session);
    if (
      !dashboard ||
      this.dashboardOwnerSessionKey !== sessionKey ||
      command.basedOnCapacityVersionId !== dashboard.capacity.capacityVersionId
    ) {
      const ownedDashboard = this.dashboardOwnerSessionKey === sessionKey ? dashboard : null;
      this.publish({
        ...initialState,
        status: ownedDashboard ? this.state.status : "loading",
        dashboard: ownedDashboard,
        capacitySubmission: "error",
        capacityMessage: "今天的安排刚刚变过，别急，重新生成一份再确认。",
      });
      return;
    }

    this.publish({
      ...this.state,
      capacitySubmission: "submitting",
      capacityMessage: "正在按新的可用时间重新安排今天…",
    });
    const fingerprint = capacityFingerprint(command);
    let attempt = this.capacityAttempt?.fingerprint === fingerprint ? this.capacityAttempt : null;
    if (!attempt) {
      try {
        attempt = {
          fingerprint,
          idempotencyKey: await this.dependencies.createIdempotencyKey(),
        };
      } catch {
        if (this.isRequestCurrent(generation, session)) {
          this.publish({
            ...this.state,
            capacitySubmission: "error",
            capacityMessage: "这次调整还没存上，别急，再试一次。",
          });
        }
        return;
      }
    }
    if (!this.isRequestCurrent(generation, session)) return;
    this.capacityAttempt = attempt;
    this.publish({
      ...this.state,
      capacitySubmission: "submitting",
      capacityMessage: "正在重新安排今天的任务…",
    });

    try {
      const result = await this.dependencies.setCapacity(
        session.accessToken,
        command,
        attempt.idempotencyKey,
      );
      if (!this.isRequestCurrent(generation, session)) return;
      this.capacityAttempt = null;
      this.conflictedCapacityVersionId = null;
      this.dashboardOwnerSessionKey = sessionKey;
      this.publish({
        status: "ready",
        dashboard: result.plan,
        errorMessage: "",
        capacitySubmission: "success",
        capacityMessage: "可用时间定好啦，今天的任务已经重新排好。",
        capacityConflictLocked: false,
      });
    } catch (error) {
      if (!this.isRequestCurrent(generation, session)) return;
      if (isAuthenticationFailure(error)) {
        await this.routeToRealAuthentication();
        return;
      }
      if (error instanceof ApiClientError && error.statusCode === 409) {
        this.capacityAttempt = null;
        this.conflictedCapacityVersionId = dashboard.capacity.capacityVersionId;
        this.publish({
          ...this.state,
          status: "ready",
          dashboard,
          capacitySubmission: "conflict",
          capacityMessage: "今天的安排刚在别的地方更新过，刷新一下再改就好。",
          capacityConflictLocked: true,
        });
        return;
      }
      this.publish({
        ...this.state,
        status: "ready",
        dashboard,
        capacitySubmission: "error",
        capacityMessage:
          error instanceof ApiClientError
            ? `${error.message}（问题编号 ${error.requestId}）`
            : "这次调整还没存上，别急，再试一次。",
        capacityConflictLocked: false,
      });
    }
  }

  dispose(): void {
    this.disposed = true;
    this.generation += 1;
    this.loadInFlight = null;
    this.loadInFlightSessionKey = null;
  }

  acceptLatestDashboard(dashboard: TodayDashboard): void {
    if (this.disposed) return;
    const session = this.dependencies.currentSession();
    const sessionKey = authSessionKey(session);
    if (!session || this.progressVerifiedSessionKey !== sessionKey) return;
    const conflictResolved =
      this.conflictedCapacityVersionId === null ||
      dashboard.capacity.capacityVersionId !== this.conflictedCapacityVersionId;
    this.dashboardOwnerSessionKey = sessionKey;
    if (conflictResolved) {
      this.capacityAttempt = null;
      this.conflictedCapacityVersionId = null;
    }
    this.publish({
      status: "ready",
      dashboard,
      errorMessage: "",
      capacitySubmission: conflictResolved ? "idle" : "conflict",
      capacityMessage: conflictResolved ? "" : "这份计划刚在别处改过，刷新一下再编辑。",
      capacityConflictLocked: !conflictResolved,
    });
  }

  private async runLoad(generation: number, session: AuthSession | undefined): Promise<void> {
    if (!session) {
      await this.routeToRealAuthentication();
      return;
    }
    const sessionKey = authSessionKey(session);
    const previous = this.dashboardOwnerSessionKey === sessionKey ? this.state.dashboard : null;
    if (!previous) {
      this.dashboardOwnerSessionKey = null;
      this.publish({ ...initialState, status: "loading" });
    }
    this.progressVerifiedSessionKey = null;

    try {
      const progress = await this.dependencies.getProgress(session.accessToken);
      if (!this.isRequestCurrent(generation, session)) return;
      if (!validProgressOwnership(progress)) {
        this.dashboardOwnerSessionKey = null;
        this.publish({
          ...initialState,
          status: "error",
          errorMessage: "建档信息还没对上，今日计划先不读取，重新登录一下就好。",
        });
        return;
      }
      if (!progress.completed) {
        const nextStep = progress.nextStep;
        if (nextStep === "workspace") return;
        this.capacityAttempt = null;
        this.conflictedCapacityVersionId = null;
        this.dashboardOwnerSessionKey = null;
        this.publish({ ...initialState, status: "redirecting" });
        try {
          await this.dependencies.navigateOnboarding(nextStep);
        } catch {
          if (this.isRequestCurrent(generation, session)) {
            this.publish({
              ...initialState,
              status: "error",
              errorMessage: "建档页面没打开，今日计划先保持锁定，别急，再试一次。",
            });
          }
        }
        return;
      }
      this.progressVerifiedSessionKey = sessionKey;

      const dashboard = await this.dependencies.getToday(session.accessToken);
      if (!this.isRequestCurrent(generation, session)) return;
      const conflictResolved =
        this.conflictedCapacityVersionId === null ||
        dashboard.capacity.capacityVersionId !== this.conflictedCapacityVersionId;
      if (conflictResolved) {
        this.capacityAttempt = null;
        this.conflictedCapacityVersionId = null;
      }
      this.dashboardOwnerSessionKey = sessionKey;
      this.publish({
        status: "ready",
        dashboard,
        errorMessage: "",
        capacitySubmission: conflictResolved ? "idle" : "conflict",
        capacityMessage: conflictResolved ? "" : "这份计划刚在别处改过，稍后刷新一下再编辑。",
        capacityConflictLocked: !conflictResolved,
      });
    } catch (error) {
      if (!this.isRequestCurrent(generation, session)) return;
      if (isAuthenticationFailure(error)) {
        await this.routeToRealAuthentication();
        return;
      }
      this.publish({
        ...this.state,
        status: previous ? "ready" : "error",
        dashboard: previous,
        errorMessage: previous ? "" : "今日计划暂时没读出来，稍后再试一次。",
        capacitySubmission: previous
          ? this.state.capacitySubmission === "conflict"
            ? "conflict"
            : "error"
          : "idle",
        capacityMessage: previous
          ? error instanceof ApiClientError
            ? `刚才刷新没成功，先给你看上次确认的计划（问题编号 ${error.requestId}）。`
            : "刚才刷新没成功，先给你看上次确认的计划。"
          : "",
      });
    }
  }

  private async routeToRealAuthentication(): Promise<void> {
    if (this.disposed) return;
    const authenticationGeneration = ++this.generation;
    this.capacityAttempt = null;
    this.conflictedCapacityVersionId = null;
    this.dashboardOwnerSessionKey = null;
    this.progressVerifiedSessionKey = null;
    this.loadInFlightSessionKey = null;
    this.dependencies.clearSession();
    const clearedSessionKey = authSessionKey(this.dependencies.currentSession());
    this.publish({ ...initialState, status: "redirecting" });
    try {
      await this.dependencies.reauthenticate();
    } catch {
      if (
        this.isCurrent(authenticationGeneration) &&
        authSessionKey(this.dependencies.currentSession()) === clearedSessionKey
      ) {
        this.publish({
          ...initialState,
          status: "error",
          errorMessage: "登录页面没打开，点一下再试一次，重新登录就好。",
        });
      }
    }
  }

  private isCurrent(generation: number): boolean {
    return !this.disposed && generation === this.generation;
  }

  private isRequestCurrent(generation: number, session: AuthSession): boolean {
    return (
      this.isCurrent(generation) &&
      authSessionKey(this.dependencies.currentSession()) === authSessionKey(session)
    );
  }

  private publish(state: TodayControllerState): void {
    if (this.disposed) return;
    this.state = state;
    this.dependencies.publish(state);
  }
}

function isAuthenticationFailure(error: unknown): boolean {
  return (
    error instanceof ApiClientError &&
    (error.statusCode === 401 || error.payload?.code === "authentication_required")
  );
}

function validProgressOwnership(progress: OnboardingProgress): boolean {
  return (progress.nextStep === "workspace") === progress.completed;
}

function capacityFingerprint(command: DailyCapacityCommand): string {
  return [
    command.basedOnCapacityVersionId,
    command.availableMinutes,
    command.energy,
    command.stress,
    command.confidenceLevel,
  ].join(":");
}

function authSessionKey(session: AuthSession | undefined): string {
  return session
    ? `${session.userId}\u0000${session.accessToken}\u0000${session.generation ?? "legacy"}`
    : "anonymous";
}
