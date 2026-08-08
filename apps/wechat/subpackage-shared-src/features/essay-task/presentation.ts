import type {
  EssayArtifactType,
  EssayTaskWorkspace,
  EssayWorkspaceFinding,
} from "../../api/essay-intelligence";
import type { EssayTaskStage } from "../../../miniprogram/features/essay-task/capabilities";
import { materialTextParagraphs } from "./material-paragraphs";

export interface MaterialSegmentView {
  anchorId: string;
  directoryLabel: string;
  paragraphLabel: string;
  materialOrder: number;
  text: string;
  segmentId: string;
  paragraphs: ReturnType<typeof materialTextParagraphs>;
  paragraphCount: number;
}

export interface MaterialDirectoryView {
  materialOrder: number;
  label: string;
  firstAnchorId: string;
  segmentCount: number;
}

export interface WorkspaceView {
  prompt: string;
  requirements: string;
  questionTypeLabel: string;
  wordLimit: number;
  maximumScore: number;
  materialSourceLabel: string;
  segments: MaterialSegmentView[];
  materialParagraphCount: number;
  directory: MaterialDirectoryView[];
  rubricStatusLabel: string;
  rubricStatusDetail: string;
  currentVersionLabel: string;
  findings: FindingView[];
  pointOptions: Array<{ pointId: string; label: string }>;
}

export interface FindingView extends EssayWorkspaceFinding {
  typeLabel: string;
  severityLabel: string;
  materialEvidenceSummary: string;
  answerEvidenceSummary: string;
}

const questionTypeLabels: Record<EssayTaskWorkspace["task"]["questionType"], string> = {
  summary: "归纳概括",
  comprehensive_analysis: "综合分析",
  countermeasure: "提出对策",
  implementation: "贯彻执行",
  composition: "文章写作",
};

const artifactLabels: Record<EssayArtifactType, string> = {
  material_annotation: "材料地图",
  point_extraction: "要点提炼",
  outline: "结构提纲",
  review_acknowledgement: "评阅确认",
  reflection: "学习复盘",
};

const findingTypeLabels: Readonly<Record<string, string>> = {
  point_finding: "找点",
  abstraction: "概括",
  structure: "结构",
  expression: "表达",
  format: "格式",
  unsupported_claim: "论据支撑",
};

const severityLabels: Readonly<Record<string, string>> = {
  low: "一般",
  medium: "重要",
  high: "高优先级",
};

export function essaySeverityLabel(severity: string): string {
  return severityLabels[severity] ?? "需关注";
}

export function presentEssayWorkspace(
  workspace: EssayTaskWorkspace,
  artifactType: EssayArtifactType | null,
): WorkspaceView {
  const segments = workspace.material.segments.map((segment, index) => {
    const paragraphs = materialTextParagraphs(segment.text, segment.segmentId);
    return {
      anchorId: `material-segment-${String(index + 1)}`,
      directoryLabel: `材料 ${String(segment.materialOrder)}`,
      paragraphLabel: `${String(paragraphs.length)} 段`,
      materialOrder: segment.materialOrder,
      text: segment.text,
      segmentId: segment.segmentId,
      paragraphs,
      paragraphCount: paragraphs.length,
    };
  });
  const directory = [...new Set(segments.map((item) => item.materialOrder))].map(
    (materialOrder) => {
      const matching = segments.filter((item) => item.materialOrder === materialOrder);
      const first = matching[0];
      if (!first) throw new Error("material directory cannot reference an empty material");
      return {
        materialOrder,
        label: `材料 ${String(materialOrder)}`,
        firstAnchorId: first.anchorId,
        segmentCount: matching.reduce((total, item) => total + item.paragraphCount, 0),
      };
    },
  );
  const rubric = workspace.rubricAvailability.rubric;
  const current = artifactType ? workspace.currentArtifacts[artifactType] : undefined;
  const pointArtifact = workspace.currentArtifacts.point_extraction;
  const points =
    pointArtifact?.payload.schemaType === "point_extraction" ? pointArtifact.payload.points : [];

  return {
    prompt: workspace.task.prompt,
    requirements: workspace.task.requirements ?? "题干未单独拆分作答要求，请以上方完整题干为准。",
    questionTypeLabel: questionTypeLabels[workspace.task.questionType],
    wordLimit: workspace.task.wordLimit,
    maximumScore: workspace.task.maximumScore,
    materialSourceLabel: {
      own_bank: "自研题库",
      official: "官方材料",
      external_confirmed: "外部材料(已核对)",
    }[workspace.material.sourceKind],
    segments,
    materialParagraphCount: segments.reduce((total, item) => total + item.paragraphCount, 0),
    directory,
    rubricStatusLabel: rubric ? "评分依据已备好" : "评分依据还没备好",
    rubricStatusDetail: rubric
      ? `${rubric.sourceKind === "official" ? "官方" : "AI 初拟·还没人工核对"} · ${String(rubric.totalMaximum)} 分`
      : "现在只能先做不依赖评分依据的步骤，不会硬编标准答案。",
    currentVersionLabel: current
      ? `${artifactLabels[current.artifactType]} · 已保存 ${String(current.versionNo)} 次`
      : "这一步还没存过",
    findings: (workspace.progression.currentReview?.findings ?? []).map(presentFinding),
    pointOptions: points.map((point, index) => ({
      pointId: point.pointId,
      label: `${String(index + 1)}. ${point.statement}`,
    })),
  };
}

export function stageBlockingReason(stage: EssayTaskStage, workspace: EssayTaskWorkspace): string {
  if (stage === "essay_drafting" && workspace.rubricAvailability.status !== "published") {
    return "这道题的评分依据还没备好，先别急着交，等一下就绪。";
  }
  if (stage === "essay_point_extraction") {
    if (workspace.rubricAvailability.status !== "published") {
      return "评分依据还没备好，稍微等等再提炼要点。";
    }
    if (!workspace.currentArtifacts.material_annotation) {
      return "材料地图还没做完，先去读材料、给每段划个重点。";
    }
  }
  if (stage === "essay_outlining") {
    if (workspace.rubricAvailability.status !== "published") {
      return "评分依据还没备好，稍微等等再排提纲。";
    }
    if (!workspace.currentArtifacts.point_extraction) {
      return "要点还没提炼完，先把要点定下来再排提纲。";
    }
  }
  if (stage === "essay_review") {
    if (workspace.rubricAvailability.status !== "published") {
      return "评分依据还没备好，先等等再确认评阅。";
    }
    if (
      workspace.progression.currentReview?.status !== "completed" ||
      workspace.progression.currentReview.findings.length === 0
    ) {
      return "现在还没有完整的评阅可以逐条确认，再等等。";
    }
  }
  return "";
}

export function presentFinding(finding: EssayWorkspaceFinding): FindingView {
  return {
    ...finding,
    typeLabel: findingTypeLabels[finding.type] ?? finding.type,
    severityLabel: essaySeverityLabel(finding.severity),
    materialEvidenceSummary:
      finding.materialEvidenceRefs.map((item) => item.quote).join("、") || "无材料引用",
    answerEvidenceSummary:
      finding.answerEvidenceRefs.map((item) => item.quote).join("、") || "无答案引用",
  };
}
