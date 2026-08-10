import type { StructuredVisualValidation, ValidationIssue } from "./schema";

export interface StructuredVisualPresentation {
  status: "valid" | "empty" | "needs_review" | "invalid";
  title: string;
  stateTitle: string;
  stateDescription: string;
  warningText: string;
  ariaLabel: string;
}

export function presentStructuredVisual(
  validation: StructuredVisualValidation,
): StructuredVisualPresentation {
  const name = validation.registration?.accessibleName ?? "结构化视觉";
  if (validation.status === "invalid") {
    const firstError = validation.issues.find((issue) => issue.severity === "error");
    return {
      status: "invalid",
      title: "",
      stateTitle: "这份结构化数据无法绘制",
      stateDescription: issueDescription(firstError, "请检查 schemaVersion 与错误字段。"),
      warningText: "",
      ariaLabel: `${name}数据无效`,
    };
  }
  if (validation.status === "empty") {
    const emptyIssue = validation.issues.at(-1);
    return {
      status: "empty",
      title: "",
      stateTitle: "暂无可绘制数据",
      stateDescription: issueDescription(emptyIssue, "请提供完整的结构化数据。"),
      warningText: "",
      ariaLabel: `${name}暂无数据`,
    };
  }
  if (validation.status === "needs_review") {
    const reviewIssue = validation.issues.at(-1);
    return {
      status: "needs_review",
      title: "",
      stateTitle: "这份图形需要人工复核",
      stateDescription: issueDescription(
        reviewIssue,
        "当前证据不足以可信重绘，已停止渲染以避免画错。",
      ),
      warningText: "",
      ariaLabel: `${name}需要人工复核`,
    };
  }

  const title = "title" in validation.visual ? validation.visual.title : "";
  const warnings = validation.issues.filter((issue) => issue.severity === "warning");
  return {
    status: "valid",
    title,
    stateTitle: "",
    stateDescription: "",
    warningText: warnings.map((issue) => issue.message).join("；"),
    ariaLabel: title.length > 0 ? `${name}：${title}` : name,
  };
}

function issueDescription(issue: ValidationIssue | undefined, fallback: string): string {
  return issue ? `${issue.path}：${issue.message}` : fallback;
}
