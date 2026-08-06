import type { AbilityApi } from "./api/ability";
import type { ReportedScoreEvidenceRequest } from "../../../api/generated/types.gen";
import type { PositionOperationError } from "./contracts";
import { scoreEvidenceHistoryItemView, type ScoreEvidenceHistoryItemView } from "./ability/history";
import { reportedScoreRequestFromDraft, type ReportedScoreDraft } from "./ability/reporting";
import { positionOperationError, shouldRetainPositionCommand } from "./error-state";
import type { PositionStateStore } from "../../../features/positions/state-store";

export type ScoreEvidenceHistoryStatus = "idle" | "loading" | "ready" | "error";

export interface ScoreEvidenceControllerState extends ReportedScoreDraft {
  open: boolean;
  saving: boolean;
  submissionUncertain: boolean;
  error: PositionOperationError | null;
  validationError: string;
  historyStatus: ScoreEvidenceHistoryStatus;
  historyError: PositionOperationError | null;
  history: ScoreEvidenceHistoryItemView[];
  todayDate: string;
}

export interface ScoreEvidenceControllerOptions {
  api: Pick<AbilityApi, "getScoreEvidenceHistory" | "reportScore">;
  store: PositionStateStore;
  onChange: (state: ScoreEvidenceControllerState) => void;
  onSaved: () => Promise<void> | void;
}

export class ScoreEvidenceController {
  private readonly api: Pick<AbilityApi, "getScoreEvidenceHistory" | "reportScore">;
  private readonly store: PositionStateStore;
  private readonly onChange: (state: ScoreEvidenceControllerState) => void;
  private readonly onSaved: () => Promise<void> | void;
  private disposed = false;
  private historyEpoch = 0;
  private state: ScoreEvidenceControllerState;

  constructor(options: ScoreEvidenceControllerOptions) {
    this.api = options.api;
    this.store = options.store;
    this.onChange = options.onChange;
    this.onSaved = options.onSaved;
    this.state = {
      ...emptyScoreDraft(),
      open: false,
      saving: false,
      submissionUncertain: false,
      error: null,
      validationError: "",
      historyStatus: "idle",
      historyError: null,
      history: [],
      todayDate: localDate(),
    };
  }

  snapshot(): ScoreEvidenceControllerState {
    return {
      ...this.state,
      error: this.state.error ? { ...this.state.error } : null,
      historyError: this.state.historyError ? { ...this.state.historyError } : null,
      history: this.state.history.map((item) => ({
        ...item,
        correctionDraft: { ...item.correctionDraft },
      })),
    };
  }

  open(): void {
    const pending = this.store
      .pendingCommands("score-evidence")
      .map((command) => pendingScoreDraft(command.payload))
      .find((draft): draft is ReportedScoreDraft => draft !== null);
    if (pending) {
      this.state = {
        ...this.state,
        ...pending,
        open: true,
        saving: false,
        submissionUncertain: true,
        validationError: "",
        error: {
          status: "network",
          title: "存在结果未知的成绩命令",
          message: "已恢复原始输入；需要先确认刚才那次提交，不能重复创建成绩记录。",
          requestId: "",
          retryable: true,
        },
      };
      this.publish();
      void this.loadHistory();
      return;
    }
    this.state = {
      ...this.state,
      ...emptyScoreDraft(),
      open: true,
      error: null,
      validationError: "",
      submissionUncertain: false,
    };
    this.publish();
    void this.loadHistory();
  }

  close(): void {
    if (this.state.saving || this.state.submissionUncertain) return;
    this.state = { ...this.state, open: false, error: null, validationError: "" };
    this.publish();
  }

  update(patch: Partial<ReportedScoreDraft>): void {
    if (this.state.saving || this.state.submissionUncertain) return;
    this.state = { ...this.state, ...patch, error: null, validationError: "" };
    this.publish();
  }

  beginCorrection(evidenceId: string): void {
    if (this.state.saving || this.state.submissionUncertain) return;
    const evidence = this.state.history.find(
      (item) => item.evidenceId === evidenceId && item.active,
    );
    if (!evidence) return;
    this.state = {
      ...this.state,
      ...evidence.correctionDraft,
      error: null,
      validationError: "",
    };
    this.publish();
  }

  startNew(): void {
    if (this.state.saving || this.state.submissionUncertain) return;
    this.state = { ...this.state, ...emptyScoreDraft(), error: null, validationError: "" };
    this.publish();
  }

  async loadHistory(): Promise<void> {
    if (this.disposed) return;
    const epoch = ++this.historyEpoch;
    this.state = { ...this.state, historyStatus: "loading", historyError: null };
    this.publish();
    try {
      const response = await this.api.getScoreEvidenceHistory();
      if (!this.isHistoryCurrent(epoch)) return;
      this.state = {
        ...this.state,
        historyStatus: "ready",
        historyError: null,
        history: response.data.items.map(scoreEvidenceHistoryItemView),
      };
      this.publish();
    } catch (error) {
      if (!this.isHistoryCurrent(epoch)) return;
      this.state = {
        ...this.state,
        historyStatus: "error",
        historyError: positionOperationError(error),
      };
      this.publish();
    }
  }

  async submit(): Promise<void> {
    if (this.disposed || this.state.saving) return;
    const prepared = reportedScoreRequestFromDraft(this.state);
    if (!prepared.request) {
      this.state = { ...this.state, validationError: prepared.error };
      this.publish();
      return;
    }
    const scope = this.state.supersedesEvidenceId || "new";
    this.state = { ...this.state, saving: true, error: null, validationError: "" };
    this.publish();
    try {
      const idempotencyKey = await this.store.commandKey("score-evidence", scope, prepared.request);
      await this.api.reportScore(prepared.request, idempotencyKey);
      if (!this.isActive()) return;
      this.store.completeCommand("score-evidence", scope);
      await Promise.all([this.loadHistory(), this.onSaved()]);
      if (!this.isActive()) return;
      this.state = {
        ...this.state,
        open: false,
        saving: false,
        submissionUncertain: false,
        error: null,
      };
      this.publish();
    } catch (error) {
      if (!this.isActive()) return;
      const operationError = positionOperationError(error);
      const uncertain = shouldRetainPositionCommand(error);
      if (!uncertain) this.store.completeCommand("score-evidence", scope);
      this.state = {
        ...this.state,
        saving: false,
        submissionUncertain: uncertain,
        error: operationError,
      };
      this.publish();
    }
  }

  dispose(): void {
    this.disposed = true;
    this.historyEpoch += 1;
  }

  private publish(): void {
    if (!this.disposed) this.onChange(this.snapshot());
  }

  private isActive(): boolean {
    return !this.disposed;
  }

  private isHistoryCurrent(epoch: number): boolean {
    return !this.disposed && epoch === this.historyEpoch;
  }
}

function emptyScoreDraft(): ReportedScoreDraft {
  return {
    xingceScore: "",
    shenlunScore: "",
    scoreContext: "full_mock",
    scoreTimedStatus: "timed",
    scoreDate: localDate(),
    supersedesEvidenceId: "",
  };
}

function localDate(): string {
  const date = new Date();
  const year = String(date.getFullYear());
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function pendingScoreDraft(value: unknown): ReportedScoreDraft | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<ReportedScoreEvidenceRequest>;
  if (
    (candidate.reportedContext !== "official_exam" &&
      candidate.reportedContext !== "full_mock" &&
      candidate.reportedContext !== "historical_estimate") ||
    (candidate.timedStatus !== "timed" &&
      candidate.timedStatus !== "untimed" &&
      candidate.timedStatus !== "unknown") ||
    typeof candidate.occurredAt !== "string"
  ) {
    return null;
  }
  const scoreDate = chinaDate(candidate.occurredAt);
  if (!scoreDate) return null;
  return {
    xingceScore: scoreText(candidate.xingceScore),
    shenlunScore: scoreText(candidate.shenlunScore),
    scoreContext: candidate.reportedContext,
    scoreTimedStatus: candidate.timedStatus,
    scoreDate,
    supersedesEvidenceId: candidate.supersedesEvidenceId ?? "",
  };
}

function scoreText(value: number | null | undefined): string {
  return typeof value === "number" ? String(value) : "";
}

function chinaDate(value: string): string {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "";
  const date = new Date(timestamp + 8 * 60 * 60 * 1000);
  const year = String(date.getUTCFullYear());
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
