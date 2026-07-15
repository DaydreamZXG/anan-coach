import type {
  TaskHelpCategory,
  TaskHelpCommand,
  TaskHelpIssueCode,
  TaskHelpPageObjectReference,
} from "../../api/task-help";

export interface TaskHelpContext {
  pageObjectReference: TaskHelpPageObjectReference;
  syncState: TaskHelpCommand["diagnostics"]["syncState"];
  requestId?: string;
  errorCode?: string;
}

const ISSUE_CATEGORY: Readonly<Record<TaskHelpIssueCode, TaskHelpCategory>> = {
  operation_navigation: "operation",
  operation_answer_control: "operation",
  operation_submit: "operation",
  operation_display: "operation",
  fault_content_render: "fault",
  fault_network_sync: "fault",
  fault_timer: "fault",
  fault_submission: "fault",
};

export const TASK_HELP_OPTIONS = Object.freeze([
  { code: "operation_navigation", label: "找不到页面或切题入口", category: "operation" },
  { code: "operation_answer_control", label: "不会使用作答、排除或标记", category: "operation" },
  { code: "operation_submit", label: "不了解保存或交卷操作", category: "operation" },
  { code: "operation_display", label: "需要调整字号或护眼显示", category: "operation" },
  { code: "fault_content_render", label: "题面、材料或图片显示异常", category: "fault" },
  { code: "fault_network_sync", label: "断点保存或网络同步异常", category: "fault" },
  { code: "fault_timer", label: "计时显示异常", category: "fault" },
  { code: "fault_submission", label: "无法交卷", category: "fault" },
] as const satisfies ReadonlyArray<{
  code: TaskHelpIssueCode;
  label: string;
  category: TaskHelpCategory;
}>);

export function createTaskHelpCommand(
  context: TaskHelpContext,
  issueCode: TaskHelpIssueCode,
  clientTime: string,
): TaskHelpCommand {
  return {
    category: ISSUE_CATEGORY[issueCode],
    issueCode,
    pageObjectReference: { ...context.pageObjectReference },
    diagnostics: {
      clientTime: required(clientTime, "clientTime"),
      syncState: context.syncState,
      ...(context.requestId ? { requestId: context.requestId } : {}),
      ...(context.errorCode ? { errorCode: context.errorCode } : {}),
    },
  };
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`Global task help requires ${label}`);
  return normalized;
}
