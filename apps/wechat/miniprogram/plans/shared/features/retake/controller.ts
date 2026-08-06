import type {
  RetakeProgramView,
  RetakeScoreEvidenceOption,
  RetakeWorkspace,
} from "../../../../api/generated/types.gen";
import type { RetakeApi } from "../../api/retake";
import { createIdempotencyKey } from "../../../../platform/command-id";
import { retakeError, type RetakeError } from "./error";
import {
  clearPendingRetakeProgram,
  loadPendingRetakeProgram,
  storePendingRetakeProgram,
} from "./pending-store";

type RetakeApiContract = Pick<RetakeApi, "getWorkspace" | "startProgram">;

export interface RetakeEvidenceView {
  evidenceId: string;
  examYear: number;
  sourceLabel: string;
  xingceLabel: string;
  essayLabel: string;
  totalLabel: string;
  selected: boolean;
}

export interface RetakeControllerState {
  status: "idle" | "loading" | "ready" | "error";
  targetExamYear: number;
  evidence: RetakeEvidenceView[];
  selectedEvidenceId: string;
  program: RetakeProgramView | null;
  scoreUseBoundary: string;
  saving: boolean;
  pendingRecovery: boolean;
  error: RetakeError | null;
}

export interface RetakeControllerOptions {
  userId: string;
  api: RetakeApiContract;
  onChange: (state: RetakeControllerState) => void;
  createCommandKey?: () => Promise<string>;
  isSessionCurrent?: () => boolean;
}

export class RetakeController {
  private readonly userId: string;
  private readonly api: RetakeApiContract;
  private readonly onChange: (state: RetakeControllerState) => void;
  private readonly createCommandKey: () => Promise<string>;
  private readonly currentSession: () => boolean;
  private disposed = false;
  private operationEpoch = 0;
  private state: RetakeControllerState = {
    status: "idle",
    targetExamYear: 0,
    evidence: [],
    selectedEvidenceId: "",
    program: null,
    scoreUseBoundary: "",
    saving: false,
    pendingRecovery: false,
    error: null,
  };

  constructor(options: RetakeControllerOptions) {
    this.userId = required(options.userId);
    this.api = options.api;
    this.onChange = options.onChange;
    this.createCommandKey =
      options.createCommandKey ?? (() => createIdempotencyKey("retake-program"));
    this.currentSession = options.isSessionCurrent ?? (() => true);
  }

  snapshot(): RetakeControllerState {
    return {
      ...this.state,
      evidence: this.state.evidence.map((item) => ({ ...item })),
      program: this.state.program ? clone(this.state.program) : null,
      error: this.state.error ? { ...this.state.error } : null,
    };
  }

  sessionIsCurrent(): boolean {
    return this.isActive();
  }

  async load(): Promise<void> {
    if (!this.isActive()) return;
    const epoch = ++this.operationEpoch;
    this.patch({ status: "loading", saving: false, error: null });
    try {
      const response = await this.api.getWorkspace();
      if (!this.isCurrent(epoch)) return;
      this.applyWorkspace(response.data);
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      this.patch({ status: "error", saving: false, error: retakeError(error) });
    }
  }

  selectEvidence(evidenceId: string): void {
    if (
      !this.isActive() ||
      this.state.status !== "ready" ||
      this.state.saving ||
      this.state.pendingRecovery ||
      this.state.program
    ) {
      return;
    }
    const selected = this.state.evidence.some((item) => item.evidenceId === evidenceId);
    if (!selected) return;
    this.state = {
      ...this.state,
      selectedEvidenceId: evidenceId,
      evidence: this.state.evidence.map((item) => ({
        ...item,
        selected: item.evidenceId === evidenceId,
      })),
      error: null,
    };
    this.publish();
  }

  async startProgram(): Promise<void> {
    if (
      !this.isActive() ||
      this.state.status !== "ready" ||
      this.state.saving ||
      this.state.program
    ) {
      return;
    }
    const pending = loadPendingRetakeProgram(this.userId);
    const evidenceId = pending?.evidenceId ?? this.state.selectedEvidenceId;
    if (!evidenceId) return;
    const epoch = ++this.operationEpoch;
    this.patch({ saving: true, error: null });
    try {
      const command = pending ?? {
        ownerUserId: this.userId,
        evidenceId,
        idempotencyKey: await this.createCommandKey(),
        createdAtMs: 0,
        expiresAtMs: 0,
      };
      if (!this.isCurrent(epoch)) return;
      if (!pending) {
        storePendingRetakeProgram(this.userId, command);
      }
      this.patch({ pendingRecovery: true });
      const response = await this.api.startProgram(
        { sourceScoreEvidenceId: command.evidenceId },
        command.idempotencyKey,
      );
      if (!this.isCurrent(epoch)) return;
      clearPendingRetakeProgram(this.userId);
      this.patch({
        program: response.data.program,
        saving: false,
        pendingRecovery: false,
        error: null,
      });
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const failure = retakeError(error);
      if (!failure.retryable) clearPendingRetakeProgram(this.userId);
      this.patch({
        saving: false,
        pendingRecovery: failure.retryable && loadPendingRetakeProgram(this.userId) !== null,
        error: failure,
      });
    }
  }

  dispose(): void {
    this.disposed = true;
    this.operationEpoch += 1;
  }

  private applyWorkspace(workspace: RetakeWorkspace): void {
    if (workspace.program) clearPendingRetakeProgram(this.userId);
    const pending = workspace.program ? null : loadPendingRetakeProgram(this.userId);
    const selectedEvidenceId = chooseEvidenceId(
      workspace.eligibleScoreEvidence,
      pending?.evidenceId,
    );
    this.state = {
      status: "ready",
      targetExamYear: workspace.targetExamYear,
      evidence: workspace.eligibleScoreEvidence.map((item) =>
        evidenceView(item, item.evidenceId === selectedEvidenceId),
      ),
      selectedEvidenceId,
      program: workspace.program ?? null,
      scoreUseBoundary: workspace.scoreUseBoundary,
      saving: false,
      pendingRecovery: pending !== null,
      error: null,
    };
    this.publish();
  }

  private patch(patch: Partial<RetakeControllerState>): void {
    if (!this.isActive()) return;
    this.state = { ...this.state, ...patch };
    this.publish();
  }

  private publish(): void {
    if (this.isActive()) this.onChange(this.snapshot());
  }

  private isCurrent(epoch: number): boolean {
    return this.isActive() && epoch === this.operationEpoch;
  }

  private isActive(): boolean {
    return !this.disposed && this.currentSession();
  }
}

function evidenceView(item: RetakeScoreEvidenceOption, selected: boolean): RetakeEvidenceView {
  return {
    evidenceId: item.evidenceId,
    examYear: item.examYear,
    sourceLabel: item.sourceType === "official_exam" ? "已核验实考" : "本人确认实考",
    xingceLabel: scoreLabel(item.xingceScore),
    essayLabel: scoreLabel(item.essayScore),
    totalLabel: scoreLabel(item.totalScore),
    selected,
  };
}

function chooseEvidenceId(
  evidence: RetakeScoreEvidenceOption[],
  pendingEvidenceId?: string,
): string {
  if (pendingEvidenceId && evidence.some((item) => item.evidenceId === pendingEvidenceId)) {
    return pendingEvidenceId;
  }
  return evidence[0]?.evidenceId ?? "";
}

function scoreLabel(value: number | null | undefined): string {
  if (value === null || value === undefined) return "未记录";
  return Number.isInteger(value) ? String(value) : value.toFixed(1).replace(/\.0$/u, "");
}

function required(value: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error("Retake controller requires an authenticated user");
  return normalized;
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
