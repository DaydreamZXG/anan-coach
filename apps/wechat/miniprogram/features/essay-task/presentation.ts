import type {
  EssayArtifactType,
  EssayTaskWorkspace,
  EssayWorkspaceFinding,
} from "../../api/essay-intelligence";
import type { EssayTaskStage } from "./capabilities";

export interface MaterialSegmentView {
  anchorId: string;
  directoryLabel: string;
  paragraphLabel: string;
  materialOrder: number;
  text: string;
  segmentId: string;
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
  material_annotation: "材料标注",
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

export function presentEssayWorkspace(
  workspace: EssayTaskWorkspace,
  artifactType: EssayArtifactType | null,
): WorkspaceView {
  const segments = workspace.material.segments.map((segment, index) => ({
    anchorId: `material-segment-${String(index + 1)}`,
    directoryLabel: `材料 ${String(segment.materialOrder)}`,
    paragraphLabel: `第 ${String(segment.segmentOrder)} 段`,
    materialOrder: segment.materialOrder,
    text: segment.text,
    segmentId: segment.segmentId,
  }));
  const directory = [...new Set(segments.map((item) => item.materialOrder))].map(
    (materialOrder) => {
      const matching = segments.filter((item) => item.materialOrder === materialOrder);
      const first = matching[0];
      if (!first) throw new Error("material directory cannot reference an empty material");
      return {
        materialOrder,
        label: `材料 ${String(materialOrder)}`,
        firstAnchorId: first.anchorId,
        segmentCount: matching.length,
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
      own_bank: "自有题库",
      official: "官方材料",
      external_confirmed: "已确认外部材料",
    }[workspace.material.sourceKind],
    segments,
    directory,
    rubricStatusLabel: rubric ? "评分规则已就绪" : "评分规则待补充",
    rubricStatusDetail: rubric
      ? `${rubric.sourceKind === "official" ? "官方" : "AI 编制·未人工核验"} · ${String(rubric.totalMaximum)} 分`
      : "当前只开放不依赖评分点的材料标注，不会伪造标准答案。",
    currentVersionLabel: current
      ? `${artifactLabels[current.artifactType]} · 第 ${String(current.versionNo)} 次保存`
      : "本阶段还没有保存",
    findings: (workspace.progression.currentReview?.findings ?? []).map(presentFinding),
    pointOptions: points.map((point, index) => ({
      pointId: point.pointId,
      label: `${String(index + 1)}. ${point.statement}`,
    })),
  };
}

export function stageBlockingReason(stage: EssayTaskStage, workspace: EssayTaskWorkspace): string {
  if (stage === "essay_drafting" && workspace.rubricAvailability.status !== "published") {
    return "这道题的评分规则还未就绪，暂时不能提交评阅。";
  }
  if (stage === "essay_point_extraction") {
    if (workspace.rubricAvailability.status !== "published") {
      return "评分规则还未就绪，暂时不能开始要点提炼。";
    }
    if (!workspace.currentArtifacts.material_annotation) {
      return "请先完成材料地图，并为每个要点选择对应材料段落。";
    }
  }
  if (stage === "essay_outlining") {
    if (workspace.rubricAvailability.status !== "published") {
      return "评分规则还未就绪，暂时不能开始提纲练习。";
    }
    if (!workspace.currentArtifacts.point_extraction) {
      return "请先完成要点提炼，并为每个提纲段落选择支撑要点。";
    }
  }
  if (stage === "essay_review") {
    if (workspace.rubricAvailability.status !== "published") {
      return "评分规则还未就绪，暂时不能确认评阅。";
    }
    if (
      workspace.progression.currentReview?.status !== "completed" ||
      workspace.progression.currentReview.findings.length === 0
    ) {
      return "当前还没有可逐条确认的完整评阅。";
    }
  }
  return "";
}

export function presentFinding(finding: EssayWorkspaceFinding): FindingView {
  return {
    ...finding,
    typeLabel: findingTypeLabels[finding.type] ?? finding.type,
    severityLabel: severityLabels[finding.severity] ?? finding.severity,
    materialEvidenceSummary:
      finding.materialEvidenceRefs.map((item) => item.quote).join("、") || "无材料引用",
    answerEvidenceSummary:
      finding.answerEvidenceRefs.map((item) => item.quote).join("、") || "无答案引用",
  };
}
