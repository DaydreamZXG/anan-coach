import type {
  EssayLearningArtifactCommand,
  EssayPointCategory,
  EssayTaskWorkspace,
  MaterialAnnotationPayload,
  OutlinePayload,
  PointExtractionPayload,
  ReviewAcknowledgementPayload,
} from "../../api/essay-intelligence";
import type { EssayTaskStage } from "./capabilities";

export interface MaterialAnnotationDraft {
  segmentId: string;
  label: string;
  paragraphSummary: string;
  subjectsText: string;
  evidenceQuote: string;
}

export interface PointDraft {
  pointId: string;
  category: EssayPointCategory;
  categoryIndex: number;
  statement: string;
  sourceSegmentIds: string[];
}

export interface OutlineSectionDraft {
  sectionId: string;
  heading: string;
  argument: string;
  sourcePointIds: string[];
  plannedWordsText: string;
}

export interface ReviewDraft {
  acknowledgedFindingIds: string[];
  priorityFindingIds: string[];
  learnerUnderstanding: string;
}

export interface EssayFormDrafts {
  overallTheme: string;
  materialAnnotations: MaterialAnnotationDraft[];
  points: PointDraft[];
  thesis: string;
  outlineSections: OutlineSectionDraft[];
  review: ReviewDraft;
}

export const pointCategoryValues: EssayPointCategory[] = [
  "problem",
  "cause",
  "impact",
  "countermeasure",
  "example",
  "theme",
];

export const pointCategoryLabels = ["问题", "原因", "影响", "对策", "案例", "主题"];

export class EssayFormError extends Error {}

export function initialEssayFormDrafts(workspace: EssayTaskWorkspace): EssayFormDrafts {
  const material = workspace.currentArtifacts.material_annotation;
  const materialPayload =
    material?.payload.schemaType === "material_annotation" ? material.payload : null;
  const annotationBySegment = new Map(
    materialPayload?.annotations.map((item) => [item.segmentId, item]) ?? [],
  );
  const points = workspace.currentArtifacts.point_extraction;
  const pointPayload = points?.payload.schemaType === "point_extraction" ? points.payload : null;
  const outline = workspace.currentArtifacts.outline;
  const outlinePayload = outline?.payload.schemaType === "outline" ? outline.payload : null;
  const acknowledgement = workspace.currentArtifacts.review_acknowledgement;
  const acknowledgementPayload =
    acknowledgement?.payload.schemaType === "review_acknowledgement"
      ? acknowledgement.payload
      : null;

  return {
    overallTheme: materialPayload?.overallTheme ?? "",
    materialAnnotations: workspace.material.segments.map((segment) => {
      const saved = annotationBySegment.get(segment.segmentId);
      return {
        segmentId: segment.segmentId,
        label: `材料 ${String(segment.materialOrder)} · 第 ${String(segment.segmentOrder)} 段`,
        paragraphSummary: saved?.paragraphSummary ?? "",
        subjectsText: saved?.subjects.join("、") ?? "",
        evidenceQuote: saved?.evidenceQuote ?? "",
      };
    }),
    points: pointPayload?.points.map((point) => ({
      ...point,
      categoryIndex: pointCategoryValues.indexOf(point.category),
    })) ?? [newPointDraft()],
    thesis: outlinePayload?.thesis ?? "",
    outlineSections: outlinePayload?.sections.map((section) => ({
      sectionId: section.sectionId,
      heading: section.heading,
      argument: section.argument,
      sourcePointIds: section.sourcePointIds,
      plannedWordsText: String(section.plannedWords),
    })) ?? [newOutlineSectionDraft()],
    review: {
      acknowledgedFindingIds: acknowledgementPayload?.acknowledgedFindingIds ?? [],
      priorityFindingIds: acknowledgementPayload?.priorityFindingIds ?? [],
      learnerUnderstanding: acknowledgementPayload?.learnerUnderstanding ?? "",
    },
  };
}

export function newPointDraft(now = Date.now()): PointDraft {
  return {
    pointId: `point-${String(now)}-${Math.random().toString(36).slice(2, 7)}`,
    category: "problem",
    categoryIndex: 0,
    statement: "",
    sourceSegmentIds: [],
  };
}

export function newOutlineSectionDraft(now = Date.now()): OutlineSectionDraft {
  return {
    sectionId: `section-${String(now)}-${Math.random().toString(36).slice(2, 7)}`,
    heading: "",
    argument: "",
    sourcePointIds: [],
    plannedWordsText: "",
  };
}

export function buildArtifactCommand(
  stage: EssayTaskStage,
  workspace: EssayTaskWorkspace,
  drafts: EssayFormDrafts,
): EssayLearningArtifactCommand {
  const common = {
    taskVersionId: workspace.task.taskVersionId,
    materialBundleVersionId: workspace.material.materialBundleVersionId,
  };
  if (stage === "essay_material_reading") {
    const payload = materialPayload(drafts);
    return withBasedOn(
      { artifactType: "material_annotation", ...common, payload },
      workspace.currentArtifacts.material_annotation?.artifactVersionId,
    );
  }
  const rubricVersionId = workspace.rubricAvailability.rubric?.rubricVersionId;
  if (!rubricVersionId) throw new EssayFormError("该阶段需要已发布的评分规程版本。");
  if (stage === "essay_point_extraction") {
    const payload = pointPayload(workspace, drafts);
    return withBasedOn(
      { artifactType: "point_extraction", ...common, rubricVersionId, payload },
      workspace.currentArtifacts.point_extraction?.artifactVersionId,
    );
  }
  if (stage === "essay_outlining") {
    const payload = outlinePayload(workspace, drafts);
    return withBasedOn(
      { artifactType: "outline", ...common, rubricVersionId, payload },
      workspace.currentArtifacts.outline?.artifactVersionId,
    );
  }
  if (stage === "essay_review") {
    const reviewVersionId = workspace.progression.reviewVersionId;
    if (!reviewVersionId || workspace.progression.reviewStatus !== "completed") {
      throw new EssayFormError("只有已完成的评阅版本可以进行确认。");
    }
    const payload = reviewPayload(workspace, drafts);
    return withBasedOn(
      {
        artifactType: "review_acknowledgement",
        ...common,
        rubricVersionId,
        reviewVersionId,
        payload,
      },
      workspace.currentArtifacts.review_acknowledgement?.artifactVersionId,
    );
  }
  throw new EssayFormError("该阶段尚无符合完整证据契约的写入能力。");
}

function materialPayload(drafts: EssayFormDrafts): MaterialAnnotationPayload {
  const overallTheme = required(drafts.overallTheme, "请填写整组材料的核心主题。");
  return {
    schemaType: "material_annotation",
    overallTheme,
    annotations: drafts.materialAnnotations.map((item) => {
      const evidenceQuote = item.evidenceQuote.trim();
      return {
        segmentId: item.segmentId,
        paragraphSummary: required(item.paragraphSummary, `请完成${item.label}的段落主旨。`),
        subjects: splitLabels(item.subjectsText, `请填写${item.label}的对象或主体。`),
        ...(evidenceQuote ? { evidenceQuote } : {}),
      };
    }),
  };
}

function pointPayload(
  workspace: EssayTaskWorkspace,
  drafts: EssayFormDrafts,
): PointExtractionPayload {
  const source = workspace.currentArtifacts.material_annotation;
  if (!source) throw new EssayFormError("请先完成并保存材料分段标注。");
  if (drafts.points.length === 0) throw new EssayFormError("至少提炼 1 个要点。");
  return {
    schemaType: "point_extraction",
    sourceMaterialAnnotationVersionId: source.artifactVersionId,
    points: drafts.points.map((point, index) => ({
      pointId: point.pointId,
      category: point.category,
      statement: required(point.statement, `请填写第 ${String(index + 1)} 个要点。`),
      sourceSegmentIds: requiredIds(
        point.sourceSegmentIds,
        `请为第 ${String(index + 1)} 个要点勾选材料出处。`,
      ),
    })),
  };
}

function outlinePayload(workspace: EssayTaskWorkspace, drafts: EssayFormDrafts): OutlinePayload {
  const source = workspace.currentArtifacts.point_extraction;
  if (!source) throw new EssayFormError("请先完成并保存要点提炼。");
  if (drafts.outlineSections.length === 0) throw new EssayFormError("至少创建 1 个提纲段落。");
  const thesis = drafts.thesis.trim();
  return {
    schemaType: "outline",
    sourcePointExtractionVersionId: source.artifactVersionId,
    ...(thesis ? { thesis } : {}),
    sections: drafts.outlineSections.map((section, index) => {
      const plannedWords = Number(section.plannedWordsText);
      if (!Number.isInteger(plannedWords) || plannedWords <= 0 || plannedWords > 5000) {
        throw new EssayFormError(`请填写第 ${String(index + 1)} 段有效的预计字数。`);
      }
      return {
        sectionId: section.sectionId,
        heading: required(section.heading, `请填写第 ${String(index + 1)} 段的小标题。`),
        argument: required(section.argument, `请填写第 ${String(index + 1)} 段的中心论述。`),
        sourcePointIds: requiredIds(
          section.sourcePointIds,
          `请为第 ${String(index + 1)} 段选择支撑要点。`,
        ),
        plannedWords,
      };
    }),
  };
}

function reviewPayload(
  workspace: EssayTaskWorkspace,
  drafts: EssayFormDrafts,
): ReviewAcknowledgementPayload {
  const findingIds = new Set(
    workspace.progression.currentReview?.findings.map((item) => item.findingId) ?? [],
  );
  if (findingIds.size === 0) throw new EssayFormError("评阅结果未返回可确认的 finding 证据。");
  const acknowledged = requiredIds(
    drafts.review.acknowledgedFindingIds,
    "请逐条阅读并勾选你已确认的评阅问题。",
  );
  if (acknowledged.length !== findingIds.size) {
    throw new EssayFormError("请阅读并确认本版评阅的全部问题。");
  }
  const priorities = requiredIds(
    drafts.review.priorityFindingIds,
    "请选择 1–3 个本次优先修正问题。",
  );
  if (priorities.length > 3) throw new EssayFormError("优先修正问题最多选择 3 个。");
  if (
    acknowledged.some((id) => !findingIds.has(id)) ||
    priorities.some((id) => !acknowledged.includes(id))
  ) {
    throw new EssayFormError("评阅问题版本已变化，请刷新后重新确认。");
  }
  return {
    schemaType: "review_acknowledgement",
    acknowledgedFindingIds: acknowledged,
    priorityFindingIds: priorities,
    learnerUnderstanding: required(
      drafts.review.learnerUnderstanding,
      "请用自己的话写下对本次问题的理解。",
    ),
  };
}

function withBasedOn<T extends EssayLearningArtifactCommand>(
  command: T,
  basedOn: string | undefined,
): T {
  return basedOn ? { ...command, basedOnArtifactVersionId: basedOn } : command;
}

function required(value: string, message: string): string {
  const normalized = value.trim();
  if (!normalized) throw new EssayFormError(message);
  return normalized;
}

function requiredIds(values: string[], message: string): string[] {
  const normalized = [...new Set(values.map((item) => item.trim()).filter(Boolean))];
  if (normalized.length === 0) throw new EssayFormError(message);
  return normalized;
}

function splitLabels(value: string, message: string): string[] {
  const values = [
    ...new Set(
      value
        .split(/[、，,;；\s]+/u)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
  if (values.length === 0) throw new EssayFormError(message);
  if (values.length > 8) throw new EssayFormError("每段最多记录 8 个对象或主体。");
  return values;
}
