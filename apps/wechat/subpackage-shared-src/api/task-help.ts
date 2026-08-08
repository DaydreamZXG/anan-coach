import type { ApiEnvelope } from "../../miniprogram/domain/contracts";
import type { ApiClient } from "../../miniprogram/api/client";

export type TaskHelpCategory = "operation" | "fault";
export type TaskHelpIssueCode =
  | "operation_navigation"
  | "operation_answer_control"
  | "operation_submit"
  | "operation_display"
  | "fault_content_render"
  | "fault_network_sync"
  | "fault_timer"
  | "fault_submission";

export interface TaskHelpPageObjectReference {
  pageCode: string;
  route: string;
  track: "xingce" | "essay";
  objectType: "page" | "task_session" | "plan_task" | "review";
  objectId: string;
  taskMode: "practice" | "targeted" | "retest" | "baseline" | "verification" | "mock";
  lifecycle: "answering" | "submitted";
}

/**
 * The help request is deliberately content-free. It cannot carry a question stem,
 * options, selected answer, correct answer, explanation, draft, or material text.
 */
export interface TaskHelpCommand {
  category: TaskHelpCategory;
  issueCode: TaskHelpIssueCode;
  pageObjectReference: TaskHelpPageObjectReference;
  diagnostics: {
    clientTime: string;
    syncState: "saved" | "saving" | "pending" | "conflict" | "error" | "unknown";
    requestId?: string;
    errorCode?: string;
  };
}

export interface TaskHelpReceipt {
  sessionId: string;
  mode: "practice" | "targeted" | "retest" | "baseline" | "verification" | "mock";
  scope: TaskHelpCategory;
  sessionState: string;
  policyVersion: "task-help-learner-safe-v1";
  title: string;
  steps: string[];
  permittedTools: string[];
  blockedCapabilities: Array<
    "current_question_answer" | "answer_analysis" | "formula_card" | "solution_ai"
  >;
  contentLookupPerformed: false;
}

export interface TaskHelpWireRequest {
  sessionId: string;
  mode: TaskHelpReceipt["mode"];
  scope: TaskHelpCategory;
}

export class TaskHelpApi {
  constructor(private readonly client: ApiClient) {}

  submit(command: TaskHelpCommand, idempotencyKey: string): Promise<ApiEnvelope<TaskHelpReceipt>> {
    return this.client.post<TaskHelpReceipt>("/api/v2/task-help", taskHelpWirePayload(command), {
      idempotencyKey,
    });
  }
}

export function taskHelpWirePayload(command: TaskHelpCommand): TaskHelpWireRequest {
  const reference = command.pageObjectReference;
  return {
    sessionId: required(reference.objectId, "sessionId"),
    mode: reference.taskMode,
    scope: command.category,
  };
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`Task help requires ${label}`);
  return normalized;
}
