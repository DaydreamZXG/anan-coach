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
  planningCompletionWritable: boolean;
}

export interface EssayRewriteWriterContext {
  kind: "rewrite";
  attemptId: string;
  baseReviewVersionId: string;
  baseAnswerVersionId: string;
  findingDecisionArtifactVersionId: string;
  basedOnAnswerVersionId: string;
  basedOnRewriteVersionId?: string;
}

export interface EssayTransferWriterContext {
  kind: "transfer";
  targetTaskVersionId: string;
  targetMaterialBundleVersionId: string;
  sourceRewriteVersionId: string;
  sourceFindingDecisionArtifactVersionId: string;
  basedOnTransferVerificationVersionId?: string;
}

export type EssayWriterContext = EssayRewriteWriterContext | EssayTransferWriterContext;

const capabilities: Record<EssayTaskStage, EssayStageCapability> = {
  essay_material_reading: {
    stage: "essay_material_reading",
    title: "申论材料地图",
    modeLabel: "申论 Agent · 材料理解",
    artifactType: "material_annotation",
    writable: true,
    unavailableReason: "",
    planningCompletionWritable: true,
  },
  essay_point_extraction: {
    stage: "essay_point_extraction",
    title: "申论要点提炼",
    modeLabel: "申论 Agent · 找点归纳",
    artifactType: "point_extraction",
    writable: true,
    unavailableReason: "",
    planningCompletionWritable: true,
  },
  essay_outlining: {
    stage: "essay_outlining",
    title: "申论结构提纲",
    modeLabel: "申论 Agent · 结构搭建",
    artifactType: "outline",
    writable: true,
    unavailableReason: "",
    planningCompletionWritable: true,
  },
  essay_review: {
    stage: "essay_review",
    title: "申论评阅确认",
    modeLabel: "申论 Agent · 原文核对评阅",
    artifactType: "review_acknowledgement",
    writable: true,
    unavailableReason: "",
    planningCompletionWritable: true,
  },
  essay_drafting: {
    stage: "essay_drafting",
    title: "申论答案成稿",
    modeLabel: "申论 Agent · 基线作答",
    artifactType: null,
    writable: true,
    unavailableReason: "",
    planningCompletionWritable: true,
  },
  essay_rewrite: {
    stage: "essay_rewrite",
    title: "申论二次改写",
    modeLabel: "申论 Agent · 评阅后改写",
    artifactType: null,
    writable: false,
    unavailableReason: "还缺原作答、完整评阅或已确认问题，暂时不能开始改写。",
    planningCompletionWritable: false,
  },
  essay_transfer: {
    stage: "essay_transfer",
    title: "申论迁移验证",
    modeLabel: "申论 Agent · 新材料验证",
    artifactType: null,
    writable: false,
    unavailableReason: "还缺新题、新材料、已完成改写或已确认问题，暂时不能开始迁移练习。",
    planningCompletionWritable: false,
  },
};

export const writableEssayStages = new Set<EssayTaskStage>(
  Object.values(capabilities)
    .filter((item) => item.writable)
    .map((item) => item.stage),
);

export function essayStageCapability(
  stage: EssayTaskStage,
  writerContext?: EssayWriterContext | null,
): EssayStageCapability {
  const capability = capabilities[stage];
  if (stage === "essay_rewrite" && writerContext?.kind === "rewrite") {
    return {
      ...capability,
      writable: true,
      unavailableReason: "",
      planningCompletionWritable: true,
    };
  }
  if (stage === "essay_transfer" && writerContext?.kind === "transfer") {
    return {
      ...capability,
      writable: true,
      unavailableReason: "",
      planningCompletionWritable: true,
    };
  }
  return capability;
}

export function parseEssayWriterContext(
  stage: EssayTaskStage,
  options: Record<string, string | undefined>,
): EssayWriterContext | null {
  if (stage === "essay_rewrite") {
    const requiredValues = {
      attemptId: normalized(options.attemptId),
      baseReviewVersionId: normalized(options.baseReviewVersionId),
      baseAnswerVersionId: normalized(options.baseAnswerVersionId),
      findingDecisionArtifactVersionId: normalized(options.findingDecisionArtifactVersionId),
      basedOnAnswerVersionId: normalized(options.basedOnAnswerVersionId),
    };
    if (Object.values(requiredValues).some((value) => !value)) return null;
    return {
      kind: "rewrite",
      ...requiredValues,
      ...(normalized(options.basedOnRewriteVersionId)
        ? { basedOnRewriteVersionId: normalized(options.basedOnRewriteVersionId) }
        : {}),
    };
  }
  if (stage === "essay_transfer") {
    const requiredValues = {
      targetTaskVersionId: normalized(options.taskVersionId),
      targetMaterialBundleVersionId: normalized(options.materialBundleVersionId),
      sourceRewriteVersionId: normalized(options.sourceRewriteVersionId),
      sourceFindingDecisionArtifactVersionId: normalized(
        options.sourceFindingDecisionArtifactVersionId,
      ),
    };
    if (Object.values(requiredValues).some((value) => !value)) return null;
    return {
      kind: "transfer",
      ...requiredValues,
      ...(normalized(options.basedOnTransferVerificationVersionId)
        ? {
            basedOnTransferVerificationVersionId: normalized(
              options.basedOnTransferVerificationVersionId,
            ),
          }
        : {}),
    };
  }
  return null;
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
  const [path, rawQuery = ""] = startUrl.split("?", 2);
  const pairs: string[] = [];
  try {
    for (const item of rawQuery.split("&").filter(Boolean)) {
      const [rawKey = "", rawValue = ""] = item.split("=", 2);
      const key = decodeURIComponent(rawKey);
      decodeURIComponent(rawValue);
      if (!["stage", "mode", "planTaskId", "basedOnExecutionVersionId"].includes(key)) {
        pairs.push(item);
      }
    }
  } catch {
    return startUrl;
  }
  pairs.push(`stage=${encodeURIComponent(stage)}`, `planTaskId=${encodeURIComponent(planTaskId)}`);
  if (basedOnExecutionVersionId) {
    pairs.push(`basedOnExecutionVersionId=${encodeURIComponent(basedOnExecutionVersionId)}`);
  }
  return `${path ?? startUrl}?${pairs.join("&")}`;
}

function normalized(value: string | undefined): string {
  return value?.trim() ?? "";
}
