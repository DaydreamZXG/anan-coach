import type { EssayArtifactType } from "../../api/essay-intelligence";

export type EssayTaskStage =
  | "essay_material_reading"
  | "essay_point_extraction"
  | "essay_outlining"
  | "essay_drafting"
  | "essay_review"
  | "essay_rewrite"
  | "essay_transfer";

export type EssayTaskMode =
  "material-reading" | "practice" | "baseline" | "review" | "rewrite" | "transfer";

export interface EssayStageCapability {
  stage: EssayTaskStage;
  title: string;
  modeLabel: string;
  artifactType: EssayArtifactType | null;
  writable: boolean;
  unavailableReason: string;
}

const capabilities: Record<EssayTaskStage, EssayStageCapability> = {
  essay_material_reading: {
    stage: "essay_material_reading",
    title: "申论材料地图",
    modeLabel: "申论 Agent · 材料理解",
    artifactType: "material_annotation",
    writable: true,
    unavailableReason: "",
  },
  essay_point_extraction: {
    stage: "essay_point_extraction",
    title: "申论要点提炼",
    modeLabel: "申论 Agent · 找点归纳",
    artifactType: "point_extraction",
    writable: true,
    unavailableReason: "",
  },
  essay_outlining: {
    stage: "essay_outlining",
    title: "申论结构提纲",
    modeLabel: "申论 Agent · 结构搭建",
    artifactType: "outline",
    writable: true,
    unavailableReason: "",
  },
  essay_review: {
    stage: "essay_review",
    title: "申论评阅确认",
    modeLabel: "申论 Agent · 证据化评阅",
    artifactType: "review_acknowledgement",
    writable: true,
    unavailableReason: "",
  },
  essay_drafting: {
    stage: "essay_drafting",
    title: "申论答案成稿",
    modeLabel: "申论 Agent · 基线作答",
    artifactType: null,
    writable: true,
    unavailableReason: "",
  },
  essay_rewrite: {
    stage: "essay_rewrite",
    title: "申论二次改写",
    modeLabel: "申论 Agent · 评阅后改写",
    artifactType: null,
    writable: false,
    unavailableReason: "改写必须同时绑定原答案、评阅版本和新答案版本；对应答案写入能力尚未开放。",
  },
  essay_transfer: {
    stage: "essay_transfer",
    title: "申论迁移验证",
    modeLabel: "申论 Agent · 新材料验证",
    artifactType: null,
    writable: false,
    unavailableReason: "迁移验证必须写入独立答案和验证版本；当前页面不会用普通笔记代替迁移证据。",
  },
};

export const writableEssayStages = new Set<EssayTaskStage>(
  Object.values(capabilities)
    .filter((item) => item.writable)
    .map((item) => item.stage),
);

export function essayStageCapability(stage: EssayTaskStage): EssayStageCapability {
  return capabilities[stage];
}

export function parseEssayTaskStage(value: string | undefined): EssayTaskStage | null {
  return value && value in capabilities ? (value as EssayTaskStage) : null;
}

export function inferEssayStageFromMode(mode: string | undefined): EssayTaskStage | null {
  const stages: Partial<Record<EssayTaskMode, EssayTaskStage>> = {
    "material-reading": "essay_material_reading",
    baseline: "essay_drafting",
    review: "essay_review",
    rewrite: "essay_rewrite",
    transfer: "essay_transfer",
  };
  return mode && mode in stages ? (stages[mode as EssayTaskMode] ?? null) : null;
}

export function withEssayTaskContext(
  startUrl: string,
  stage: string,
  planTaskId: string,
  basedOnExecutionVersionId?: string,
): string {
  const separator = startUrl.includes("?") ? "&" : "?";
  const executionContext = basedOnExecutionVersionId
    ? `&basedOnExecutionVersionId=${encodeURIComponent(basedOnExecutionVersionId)}`
    : "";
  return `${startUrl}${separator}stage=${encodeURIComponent(stage)}&planTaskId=${encodeURIComponent(planTaskId)}${executionContext}`;
}
