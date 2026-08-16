import type {
  PrivateQuestionBookDetail,
  PrivateQuestionBookPracticeLaunch,
  TodayDashboard,
} from "../../api/generated/types.gen";
import { ApiClientError } from "../../api/client";
import {
  registeredTaskNavigationTarget,
  type RegisteredNavigationTarget,
} from "../../app/navigation/routes";
import { createIdempotencyKey } from "../../platform/command-id";
import type {
  ImportWorkspace,
  PrivateQuestionBookGateway,
} from "../../features/external-learning/contracts";

export interface QuestionBookFlowState {
  status: "idle" | "saving" | "ready" | "starting" | "error";
  book: PrivateQuestionBookDetail | null;
  launch: PrivateQuestionBookPracticeLaunch | null;
  navigationTarget: RegisteredNavigationTarget | null;
  errorMessage: string;
}

interface PendingSave {
  command: {
    importId: string;
    recognitionRevisionId: string;
    title: string;
  };
  idempotencyKey: string;
}

interface PendingStart {
  bookId: string;
  planTaskId: string;
  idempotencyKey: string;
}

export interface QuestionBookFlowOptions {
  gateway: Pick<
    PrivateQuestionBookGateway,
    "createQuestionBook" | "getQuestionBook" | "startQuestionBookPractice"
  >;
  onChange: (state: QuestionBookFlowState) => void;
  createCommandKey?: (prefix: string) => Promise<string>;
}

export class QuestionBookFlow {
  private readonly gateway: QuestionBookFlowOptions["gateway"];
  private readonly onChange: QuestionBookFlowOptions["onChange"];
  private readonly createCommandKey: (prefix: string) => Promise<string>;
  private state: QuestionBookFlowState = initialState();
  private pendingSave: PendingSave | null = null;
  private pendingStart: PendingStart | null = null;
  private savePromise: Promise<PrivateQuestionBookDetail | null> | null = null;
  private startPromise: Promise<RegisteredNavigationTarget | null> | null = null;
  private workspaceSignature = "";
  private disposed = false;
  private operationEpoch = 0;

  constructor(options: QuestionBookFlowOptions) {
    this.gateway = options.gateway;
    this.onChange = options.onChange;
    this.createCommandKey = options.createCommandKey ?? createIdempotencyKey;
  }

  snapshot(): QuestionBookFlowState {
    return cloneState(this.state);
  }

  async syncWorkspace(
    workspace: ImportWorkspace,
    title: string,
  ): Promise<PrivateQuestionBookDetail | null> {
    const recognition = workspace.recognition;
    if (!recognition || recognition.state !== "confirmed" || workspace.questionUnits.length === 0) {
      return null;
    }
    if (this.savePromise) return this.savePromise;

    const command = {
      importId: workspace.importId,
      recognitionRevisionId: recognition.recognitionRevisionId,
      title: normalizedBookTitle(title),
    };
    const signature = questionBookWorkspaceSignature(workspace);
    const current = this.state.book;
    const sameBook =
      current?.importId === command.importId &&
      current.recognitionRevisionId === command.recognitionRevisionId;
    if (sameBook && signature === this.workspaceSignature) return current;

    const operation = this.saveWorkspace(command, signature, sameBook ? current.bookId : null);
    this.savePromise = operation;
    try {
      return await operation;
    } finally {
      if (this.savePromise === operation) this.savePromise = null;
    }
  }

  async loadBook(bookId: string): Promise<PrivateQuestionBookDetail | null> {
    const normalized = bookId.trim();
    if (!normalized || this.disposed) return null;
    const epoch = ++this.operationEpoch;
    this.patch({ status: "saving", errorMessage: "" });
    try {
      const book = await this.gateway.getQuestionBook(normalized);
      if (!this.isCurrent(epoch)) return null;
      this.pendingSave = null;
      this.workspaceSignature = "";
      this.patch({ status: "ready", book, launch: null, navigationTarget: null });
      return book;
    } catch (error) {
      if (!this.isCurrent(epoch)) return null;
      this.patch({ status: "error", errorMessage: questionBookErrorMessage(error) });
      return null;
    }
  }

  async retrySave(
    workspace: ImportWorkspace,
    title: string,
  ): Promise<PrivateQuestionBookDetail | null> {
    return this.syncWorkspace(workspace, title);
  }

  async startPractice(planTaskId: string): Promise<RegisteredNavigationTarget | null> {
    const book = this.state.book;
    const normalizedTaskId = planTaskId.trim();
    if (!book || !normalizedTaskId || this.disposed) return null;
    if (this.startPromise) return this.startPromise;

    const operation = this.startBookPractice(book.bookId, normalizedTaskId);
    this.startPromise = operation;
    try {
      return await operation;
    } finally {
      if (this.startPromise === operation) this.startPromise = null;
    }
  }

  dispose(): void {
    this.disposed = true;
    this.operationEpoch += 1;
  }

  private async saveWorkspace(
    command: PendingSave["command"],
    signature: string,
    existingBookId: string | null,
  ): Promise<PrivateQuestionBookDetail | null> {
    const epoch = ++this.operationEpoch;
    this.patch({ status: "saving", errorMessage: "" });
    try {
      let book: PrivateQuestionBookDetail;
      if (existingBookId) {
        book = await this.gateway.getQuestionBook(existingBookId);
      } else {
        const replay = this.pendingSave;
        const pending =
          replay && sameSaveCommand(replay.command, command)
            ? replay
            : {
                command,
                idempotencyKey: await this.createCommandKey("question-book-save"),
              };
        this.pendingSave = pending;
        book = await this.gateway.createQuestionBook(pending.command, pending.idempotencyKey);
      }
      if (!this.isCurrent(epoch)) return null;
      this.pendingSave = null;
      this.workspaceSignature = signature;
      this.patch({ status: "ready", book, launch: null, navigationTarget: null });
      return book;
    } catch (error) {
      if (!this.isCurrent(epoch)) return null;
      if (!isUncertain(error)) this.pendingSave = null;
      this.patch({ status: "error", errorMessage: questionBookErrorMessage(error) });
      return null;
    }
  }

  private async startBookPractice(
    bookId: string,
    planTaskId: string,
  ): Promise<RegisteredNavigationTarget | null> {
    const epoch = ++this.operationEpoch;
    this.patch({ status: "starting", errorMessage: "" });
    try {
      const replay = this.pendingStart;
      const pending =
        replay && replay.bookId === bookId && replay.planTaskId === planTaskId
          ? replay
          : {
              bookId,
              planTaskId,
              idempotencyKey: await this.createCommandKey("question-book-practice"),
            };
      this.pendingStart = pending;
      const launch = await this.gateway.startQuestionBookPractice(
        pending.bookId,
        { planTaskId: pending.planTaskId },
        pending.idempotencyKey,
      );
      if (!this.isCurrent(epoch)) return null;
      const navigationTarget = registeredTaskNavigationTarget(launch.startUrl, planTaskId);
      if (!navigationTarget) throw new Error("题本练习入口没有通过本地路由校验。");
      this.pendingStart = null;
      this.patch({ status: "ready", launch, navigationTarget, errorMessage: "" });
      return navigationTarget;
    } catch (error) {
      if (!this.isCurrent(epoch)) return null;
      if (!isUncertain(error)) this.pendingStart = null;
      this.patch({ status: "error", errorMessage: questionBookErrorMessage(error) });
      return null;
    }
  }

  private patch(patch: Partial<QuestionBookFlowState>): void {
    this.state = { ...this.state, ...patch };
    if (!this.disposed) this.onChange(this.snapshot());
  }

  private isCurrent(epoch: number): boolean {
    return !this.disposed && this.operationEpoch === epoch;
  }
}

export function preferredQuestionBookPlanTaskId(dashboard: TodayDashboard): string | null {
  for (const task of dashboard.tasks) {
    if (
      task.track !== "xingce" ||
      task.stage !== "objective_practice" ||
      (task.status !== "ready" && task.status !== "in_progress")
    ) {
      continue;
    }
    if (registeredTaskNavigationTarget(task.startUrl, task.id)) return task.id;
  }
  return null;
}

export function questionBookErrorMessage(error: unknown): string {
  if (error instanceof ApiClientError && error.message.trim()) return error.message;
  if (error instanceof Error && /[一-鿿]/u.test(error.message)) return error.message;
  return "刚才这一步没有完成，题本不会丢，可以直接重试。";
}

function initialState(): QuestionBookFlowState {
  return {
    status: "idle",
    book: null,
    launch: null,
    navigationTarget: null,
    errorMessage: "",
  };
}

function cloneState(state: QuestionBookFlowState): QuestionBookFlowState {
  return {
    ...state,
    book: state.book
      ? { ...state.book, questions: state.book.questions.map((question) => ({ ...question })) }
      : null,
    launch: state.launch
      ? { ...state.launch, excludedCounts: { ...state.launch.excludedCounts } }
      : null,
    navigationTarget: state.navigationTarget ? { ...state.navigationTarget } : null,
  };
}

function normalizedBookTitle(title: string): string {
  const normalized = title.replace(/\.(pdf|png|jpe?g)$/iu, "").trim();
  return (normalized || "我的名师题本").slice(0, 120);
}

function questionBookWorkspaceSignature(workspace: ImportWorkspace): string {
  return workspace.questionUnits
    .map(
      (unit) =>
        `${unit.questionUnitId}:${unit.state}:${unit.match?.confirmedContentVersionId ?? ""}:${String(unit.match?.versionNo ?? 0)}`,
    )
    .join("|");
}

function sameSaveCommand(left: PendingSave["command"], right: PendingSave["command"]): boolean {
  return (
    left.importId === right.importId &&
    left.recognitionRevisionId === right.recognitionRevisionId &&
    left.title === right.title
  );
}

function isUncertain(error: unknown): boolean {
  return error instanceof ApiClientError && error.classification === "transport_unknown";
}
