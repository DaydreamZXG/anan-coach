import type { ApiClient } from "./client";
import type {
  ContentAnchor,
  RichContentBlock,
  TableContentCell,
} from "../task-runtime/content-renderer/model";
import type { TaskEditLeaseToken } from "./task-editing";

export interface MockExamLearnerOption {
  optionId: string;
  label: string;
  text: string;
  html: string | null;
  content: RichContentBlock[];
}

export interface MockExamLearnerAsset {
  assetId: string;
  url: string;
  altText: string;
  originalOcrUrl: string;
  contentKind: "image" | "graph";
}

export interface MockExamLearnerMaterial {
  materialId: string;
  title: string | null;
  text: string | null;
  html: string | null;
  assets: MockExamLearnerAsset[];
  content: RichContentBlock[];
  anchors: ContentAnchor[];
}

/** Learner-safe by construction: answer and explanation fields do not exist. */
export interface MockExamLearnerQuestion {
  questionId: string;
  contentVersionId: string;
  moduleName: string;
  materialId: string | null;
  stem: string;
  stemHtml: string | null;
  stemContent: RichContentBlock[];
  options: MockExamLearnerOption[];
}

export interface MockExamModuleBlueprint {
  moduleName: string;
  questionCount: number;
}

export interface MockExamAnswerCheckpoint {
  questionId: string;
  selectedAnswer: string | null;
  eliminatedOptionIds: string[];
  marked: boolean;
  draftText: string;
  materialPosition?: {
    anchorId: string;
    scrollTop: number;
    zoom: number;
  };
}

export interface MockExamCheckpoint {
  currentQuestionId: string;
  answers: MockExamAnswerCheckpoint[];
}

export interface MockExamCheckpointReceipt {
  sessionId: string;
  status: "assigned";
  revision: number;
  checkpoint: MockExamCheckpoint;
  checkpointUpdatedAt: string;
}

export interface MockExamLearnerSession {
  sessionId: string;
  planTaskId: string | null;
  status: "assigned" | "submitted" | "reviewed";
  title: string;
  durationMinutes: number;
  questionCount: number;
  blueprint: MockExamModuleBlueprint[];
  bundleVersionId: string;
  paperVersionId: string;
  scoringPolicyVersionId: string;
  scoreMetricName: string;
  scoreMetricUnit: string;
  scoreMetricDefinition: string;
  nextTrajectoryEventSeq: number;
  materials: MockExamLearnerMaterial[];
  questions: MockExamLearnerQuestion[];
  revision: number;
  startedAt: string;
  deadlineAt: string;
  checkpoint: MockExamCheckpoint;
}

export interface MockExamCheckpointCommand {
  expectedRevision: number;
  currentQuestionId: string;
  answers: MockExamAnswerCheckpoint[];
}

export interface MockExamSubmitCommand {
  expectedRevision: number;
  answers: Array<{ questionId: string; selectedAnswer: string | null }>;
}

export type MockExamDisputeCategory =
  | "stem_error"
  | "option_error"
  | "answer_error"
  | "material_mismatch"
  | "image_unreadable"
  | "other";

export interface MockExamDisputeCommand {
  questionId: string;
  contentVersionId: string;
  category: MockExamDisputeCategory;
  description: string;
}

export interface MockExamDisputeReceipt extends MockExamDisputeCommand {
  disputeId: string;
  sessionId: string;
  evidenceAttached: true;
  createdAt: string;
}

export interface MockExamModuleResult {
  moduleName: string;
  correct: number;
  total: number;
  accuracy: number;
}

export interface MockExamSubmitReceipt {
  sessionId: string;
  status: "submitted" | "reviewed";
  review: {
    estimatedScore: number;
    estimateSource: string;
    performanceIndex: number;
    performanceRange: { lower: number; recommended: number; upper: number };
    metricName: "balanced_performance_index" | "question_weighted_performance_index";
    metricUnit: "index_points";
    metricDefinition: string;
    intervalDefinition: string;
    paperVersionId: string;
    scoringPolicyVersionId: string;
    submittedAt: string;
    modules: MockExamModuleResult[];
    nextWeekReason: string;
  };
}

export interface MockExamTrajectoryCommand {
  eventSeq: number;
  eventType: "visited" | "answer_changed" | "mark_changed" | "skipped" | "left" | "resumed";
  questionId: string | null;
  clientOccurredAt: string;
  sessionRevision: number;
  payload: Record<string, string | number | boolean | null>;
}

export class MockExamApi {
  constructor(private readonly client: ApiClient) {}

  async createOrRestoreWeekly(
    idempotencyKey: string,
    planTaskId?: string,
  ): Promise<MockExamLearnerSession> {
    const response = await this.client.post<unknown>(
      "/api/v1/mock-exams/weekly",
      planTaskId ? { planTaskId: requiredId(planTaskId, "planTaskId") } : {},
      { idempotencyKey, timeoutMs: 30_000 },
    );
    return mapLearnerSafeMockSession(unwrapApiResponse(response));
  }

  async getSession(sessionId: string): Promise<MockExamLearnerSession> {
    const response = await this.client.get<unknown>(sessionPath(sessionId));
    return mapLearnerSafeMockSession(unwrapApiResponse(response));
  }

  async saveCheckpoint(
    sessionId: string,
    command: MockExamCheckpointCommand,
    idempotencyKey: string,
    lease: TaskEditLeaseToken,
  ): Promise<MockExamCheckpointReceipt> {
    const response = await this.client.put<unknown>(
      `${sessionPath(sessionId)}/checkpoint`,
      command,
      {
        idempotencyKey,
        taskEditLease: lease,
      },
    );
    return mapCheckpointReceipt(unwrapApiResponse(response));
  }

  async submit(
    sessionId: string,
    command: MockExamSubmitCommand,
    idempotencyKey: string,
    lease: TaskEditLeaseToken,
  ): Promise<MockExamSubmitReceipt> {
    const response = await this.client.post<unknown>(`${sessionPath(sessionId)}/submit`, command, {
      idempotencyKey,
      taskEditLease: lease,
      timeoutMs: 30_000,
    });
    return mapSubmitReceipt(unwrapApiResponse(response));
  }

  async reportDispute(
    sessionId: string,
    command: MockExamDisputeCommand,
    idempotencyKey: string,
  ): Promise<MockExamDisputeReceipt> {
    const response = await this.client.post<unknown>(
      `${sessionPath(sessionId)}/disputes`,
      command,
      { idempotencyKey },
    );
    return mapDisputeReceipt(unwrapApiResponse(response));
  }

  async recordTrajectory(
    sessionId: string,
    command: MockExamTrajectoryCommand,
    idempotencyKey: string,
  ): Promise<void> {
    await this.client.post(
      `/api/v2/mock-exams/${encodeURIComponent(sessionId)}/trajectory-events`,
      command,
      { idempotencyKey },
    );
  }
}

function mapCheckpointReceipt(value: unknown): MockExamCheckpointReceipt {
  const source = record(value, "mock checkpoint receipt");
  const checkpoint = record(field(source, "checkpoint"), "mock checkpoint");
  const status = stringField(source, ["status"]);
  const revision = integerField(source, ["revision"]);
  if (status !== "assigned") throw new Error("Mock checkpoint receipt status is invalid");
  if (revision < 2) throw new Error("Mock checkpoint receipt revision is invalid");
  return {
    sessionId: stringField(source, ["sessionId", "session_id"]),
    status,
    revision,
    checkpoint: {
      currentQuestionId: stringField(checkpoint, ["currentQuestionId", "current_question_id"]),
      answers: array(field(checkpoint, "answers"), "checkpoint answers").map(mapCheckpointAnswer),
    },
    checkpointUpdatedAt: isoInstantField(source, ["checkpointUpdatedAt", "checkpoint_updated_at"]),
  };
}

function requiredId(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`Mock exam requires ${label}`);
  return normalized;
}

export function mapLearnerSafeMockSession(value: unknown): MockExamLearnerSession {
  const source = record(value, "mock session");
  assertNoForbiddenLearnerFields(source, "session");
  const questions = array(field(source, "questions"), "questions").map(mapQuestion);
  const materials = array(field(source, "materials"), "materials").map(mapMaterial);
  const questionIds = new Set(questions.map((question) => question.questionId));
  const materialIds = new Set(materials.map((material) => material.materialId));
  if (questions.some((question) => question.materialId && !materialIds.has(question.materialId))) {
    throw new Error("Mock question references a material outside its frozen bundle");
  }
  const checkpointSource = optionalRecord(field(source, "checkpoint"));
  const fallbackQuestionId = questions.at(0)?.questionId ?? "";
  const currentQuestionId = stringField(
    checkpointSource ?? source,
    ["currentQuestionId", "current_question_id"],
    fallbackQuestionId,
  );
  const checkpointAnswersValue = checkpointSource
    ? field(checkpointSource, "answers")
    : field(source, "answers");
  const checkpointAnswers = optionalArray(checkpointAnswersValue).map(mapCheckpointAnswer);
  const checkpointByQuestion = new Map(
    checkpointAnswers
      .filter((answer) => questionIds.has(answer.questionId))
      .map((answer) => [answer.questionId, answer]),
  );
  const answers = questions.map(
    (question): MockExamAnswerCheckpoint =>
      checkpointByQuestion.get(question.questionId) ?? emptyCheckpointAnswer(question.questionId),
  );

  const durationMinutes = integerField(source, ["durationMinutes", "duration_minutes"]);
  if (durationMinutes <= 0) throw new Error("Mock session duration must be positive");
  const revision = integerField(source, ["revision"]);
  if (revision < 1) throw new Error("Mock session revision must be positive");
  const status = stringField(source, ["status"]);
  if (status !== "assigned" && status !== "submitted" && status !== "reviewed") {
    throw new Error("Mock session status is invalid");
  }
  const startedAt = isoInstantField(source, ["startedAt", "started_at"]);
  const deadlineAt = isoInstantField(source, ["deadlineAt", "deadline_at"]);
  if (Date.parse(deadlineAt) <= Date.parse(startedAt)) {
    throw new Error("Mock session deadline must follow its start time");
  }

  return {
    sessionId: stringField(source, ["sessionId", "session_id"]),
    planTaskId: nullableStringField(source, ["planTaskId", "plan_task_id"]),
    status,
    title: stringField(source, ["title"]),
    durationMinutes,
    questionCount: questions.length,
    blueprint: optionalArray(field(source, "blueprint")).map(mapBlueprint),
    bundleVersionId: versionIdField(source, ["bundleVersionId", "bundle_version_id"]),
    paperVersionId: stringField(source, ["paperVersionId", "paper_version_id"]),
    scoringPolicyVersionId: stringField(source, [
      "scoringPolicyVersionId",
      "scoring_policy_version_id",
    ]),
    scoreMetricName: stringField(source, ["scoreMetricName", "score_metric_name"]),
    scoreMetricUnit: stringField(source, ["scoreMetricUnit", "score_metric_unit"]),
    scoreMetricDefinition: stringField(source, [
      "scoreMetricDefinition",
      "score_metric_definition",
    ]),
    nextTrajectoryEventSeq: integerField(source, [
      "nextTrajectoryEventSeq",
      "next_trajectory_event_seq",
    ]),
    materials,
    questions,
    revision,
    startedAt,
    deadlineAt,
    checkpoint: {
      currentQuestionId: questionIds.has(currentQuestionId)
        ? currentQuestionId
        : fallbackQuestionId,
      answers,
    },
  };
}

function mapQuestion(value: unknown): MockExamLearnerQuestion {
  const source = record(value, "learner question");
  const questionId = stringField(source, ["questionId", "question_id"]);
  return {
    questionId,
    contentVersionId: versionIdField(source, ["contentVersionId", "content_version_id"]),
    moduleName: stringField(source, ["moduleName", "module_name"]),
    materialId: nullableStringField(source, ["materialId", "material_id"]),
    stem: stringField(source, ["stem"]),
    stemHtml: nullableStringField(source, ["stemHtml", "stem_html"]),
    stemContent: array(valueFor(source, ["stemContent", "stem_content"]), "stem content").map(
      mapRichContentBlock,
    ),
    options: array(field(source, "options"), "question options").map(mapOption),
  };
}

function mapOption(value: unknown, index: number): MockExamLearnerOption {
  const source = record(value, "learner option");
  const label = stringField(source, ["label"], String.fromCharCode(65 + index));
  return {
    optionId: stringField(source, ["optionId", "option_id"], label),
    label,
    text: stringField(source, ["text", "optionText", "option_text"]),
    html: nullableStringField(source, ["html", "optionHtml", "option_html"]),
    content: array(field(source, "content"), "option content").map(mapRichContentBlock),
  };
}

function mapAsset(value: unknown): MockExamLearnerAsset {
  const source = record(value, "learner asset");
  return {
    assetId: stringField(source, ["assetId", "asset_id"]),
    url: stringField(source, ["url", "sourceUrl", "source_url"]),
    altText: stringField(source, ["altText", "alt_text"], "题目图片"),
    originalOcrUrl: stringField(source, ["originalOcrUrl", "original_ocr_url"]),
    contentKind: contentKindField(source),
  };
}

function mapMaterial(value: unknown): MockExamLearnerMaterial {
  const source = record(value, "learner material");
  return {
    materialId: stringField(source, ["materialId", "material_id"]),
    title: nullableStringField(source, ["title"]),
    text: nullableStringField(source, ["text"]),
    html: nullableStringField(source, ["html"]),
    assets: optionalArray(field(source, "assets")).map(mapAsset),
    content: array(field(source, "content"), "material content").map(mapRichContentBlock),
    anchors: optionalArray(field(source, "anchors")).map(mapContentAnchor),
  };
}

function mapContentAnchor(value: unknown): ContentAnchor {
  const source = record(value, "content anchor");
  return {
    anchorId: stringField(source, ["anchorId", "anchor_id"]),
    blockId: stringField(source, ["blockId", "block_id"]),
    label: stringField(source, ["label"]),
  };
}

function mapRichContentBlock(value: unknown): RichContentBlock {
  const source = record(value, "rich content block");
  const type = stringField(source, ["type"]);
  const blockId = stringField(source, ["blockId", "block_id"]);
  if (type === "paragraph") {
    return {
      type,
      blockId,
      text: stringField(source, ["text"]),
      html: nullableStringField(source, ["html"]),
    };
  }
  if (type === "formula") {
    const format = stringField(source, ["format"]);
    if (format !== "latex") throw new Error("Mock formula format must be latex");
    return {
      type,
      blockId,
      expression: stringField(source, ["expression"]),
      format,
      display: booleanField(source, ["display"], false),
    };
  }
  if (type === "image" || type === "graph") {
    const zoomable = booleanField(source, ["zoomable"], false);
    if (!zoomable) throw new Error("Mock visual content must be zoomable");
    const visual = {
      blockId,
      assetId: stringField(source, ["assetId", "asset_id"]),
      url: stringField(source, ["url"]),
      originalOcrUrl: stringField(source, ["originalOcrUrl", "original_ocr_url"]),
      altText: stringField(source, ["altText", "alt_text"]),
      zoomable: true as const,
    };
    return type === "image" ? { type, ...visual } : { type, ...visual };
  }
  if (type === "table") {
    const horizontallyScrollable = booleanField(
      source,
      ["horizontallyScrollable", "horizontally_scrollable"],
      false,
    );
    if (!horizontallyScrollable) throw new Error("Mock tables must support horizontal scrolling");
    return {
      type,
      blockId,
      rowCount: integerField(source, ["rowCount", "row_count"]),
      columnCount: integerField(source, ["columnCount", "column_count"]),
      frozenHeaderRows: integerField(source, ["frozenHeaderRows", "frozen_header_rows"]),
      horizontallyScrollable: true,
      cells: array(field(source, "cells"), "table cells").map(mapTableCell),
    };
  }
  throw new Error(`Unsupported mock content block: ${type}`);
}

function mapTableCell(value: unknown): TableContentCell {
  const source = record(value, "table cell");
  return {
    row: integerField(source, ["row"]),
    column: integerField(source, ["column"]),
    rowSpan: integerField(source, ["rowSpan", "row_span"]),
    columnSpan: integerField(source, ["columnSpan", "column_span"]),
    header: booleanField(source, ["header"], false),
    text: stringField(source, ["text"]),
    html: nullableStringField(source, ["html"]),
  };
}

function mapBlueprint(value: unknown): MockExamModuleBlueprint {
  const source = record(value, "mock blueprint");
  return {
    moduleName: stringField(source, ["moduleName", "module_name"]),
    questionCount: integerField(source, ["questionCount", "question_count"]),
  };
}

function mapCheckpointAnswer(value: unknown): MockExamAnswerCheckpoint {
  const source = record(value, "mock checkpoint answer");
  const position = optionalRecord(valueFor(source, ["materialPosition", "material_position"]));
  return {
    questionId: stringField(source, ["questionId", "question_id"]),
    selectedAnswer: nullableStringField(source, ["selectedAnswer", "selected_answer"]),
    eliminatedOptionIds: stringArrayField(source, ["eliminatedOptionIds", "eliminated_option_ids"]),
    marked: booleanField(source, ["marked"], false),
    draftText: stringField(source, ["draftText", "draft_text"], ""),
    ...(position
      ? {
          materialPosition: {
            anchorId: stringField(position, ["anchorId", "anchor_id"]),
            scrollTop: numberField(position, ["scrollTop", "scroll_top"]),
            zoom: numberField(position, ["zoom"]),
          },
        }
      : {}),
  };
}

function mapSubmitReceipt(value: unknown): MockExamSubmitReceipt {
  const source = record(value, "mock submit receipt");
  const review = record(field(source, "review"), "mock review");
  const status = stringField(source, ["status"]);
  if (status !== "submitted" && status !== "reviewed") {
    throw new Error("Mock submit status is invalid");
  }
  const metricName = stringField(review, ["metricName", "metric_name"]);
  if (
    metricName !== "balanced_performance_index" &&
    metricName !== "question_weighted_performance_index"
  ) {
    throw new Error("Mock score metric is invalid");
  }
  const metricUnit = stringField(review, ["metricUnit", "metric_unit"]);
  if (metricUnit !== "index_points") {
    throw new Error("Mock score metric unit is invalid");
  }
  return {
    sessionId: stringField(source, ["sessionId", "session_id"]),
    status,
    review: {
      estimatedScore: numberField(review, ["estimatedScore", "estimated_score"]),
      estimateSource: stringField(review, ["estimateSource", "estimate_source"]),
      performanceIndex: numberField(review, ["performanceIndex", "performance_index"]),
      performanceRange: mapPerformanceRange(field(review, "performanceRange")),
      metricName,
      metricUnit,
      metricDefinition: stringField(review, ["metricDefinition", "metric_definition"]),
      intervalDefinition: stringField(review, ["intervalDefinition", "interval_definition"]),
      paperVersionId: stringField(review, ["paperVersionId", "paper_version_id"]),
      scoringPolicyVersionId: stringField(review, [
        "scoringPolicyVersionId",
        "scoring_policy_version_id",
      ]),
      submittedAt: isoInstantField(review, ["submittedAt", "submitted_at"]),
      modules: optionalArray(field(review, "modules")).map((item) => {
        const module = record(item, "mock module result");
        return {
          moduleName: stringField(module, ["moduleName", "module_name"]),
          correct: integerField(module, ["correct"]),
          total: integerField(module, ["total"]),
          accuracy: numberField(module, ["accuracy"]),
        };
      }),
      nextWeekReason: stringField(review, ["nextWeekReason", "next_week_reason"]),
    },
  };
}

function mapPerformanceRange(value: unknown): {
  lower: number;
  recommended: number;
  upper: number;
} {
  const source = record(value, "mock performance range");
  return {
    lower: numberField(source, ["lower"]),
    recommended: numberField(source, ["recommended"]),
    upper: numberField(source, ["upper"]),
  };
}

function mapDisputeReceipt(value: unknown): MockExamDisputeReceipt {
  const source = record(value, "mock dispute receipt");
  const category = stringField(source, ["category"]);
  if (!isDisputeCategory(category)) throw new Error("Mock dispute category is invalid");
  if (field(source, "evidenceAttached") !== true && field(source, "evidence_attached") !== true) {
    throw new Error("Mock dispute must attach frozen session evidence");
  }
  return {
    disputeId: stringField(source, ["disputeId", "dispute_id"]),
    sessionId: stringField(source, ["sessionId", "session_id"]),
    questionId: stringField(source, ["questionId", "question_id"]),
    contentVersionId: versionIdField(source, ["contentVersionId", "content_version_id"]),
    category,
    description: stringField(source, ["description"]),
    evidenceAttached: true,
    createdAt: isoInstantField(source, ["createdAt", "created_at"]),
  };
}

function isDisputeCategory(value: string): value is MockExamDisputeCategory {
  return [
    "stem_error",
    "option_error",
    "answer_error",
    "material_mismatch",
    "image_unreadable",
    "other",
  ].includes(value);
}

function emptyCheckpointAnswer(questionId: string): MockExamAnswerCheckpoint {
  return {
    questionId,
    selectedAnswer: null,
    eliminatedOptionIds: [],
    marked: false,
    draftText: "",
  };
}

function unwrapApiResponse(value: unknown): unknown {
  const source = optionalRecord(value);
  if (!source) return value;
  return "data" in source && "requestId" in source && "generatedAt" in source ? source.data : value;
}

function sessionPath(sessionId: string): string {
  return `/api/v1/mock-exams/${encodeURIComponent(required(sessionId, "sessionId"))}`;
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`Mock exam API requires ${label}`);
  return normalized;
}

function record(value: unknown, label: string): Record<string, unknown> {
  const candidate = optionalRecord(value);
  if (!candidate) throw new Error(`${label} must be an object`);
  return candidate;
}

function optionalRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

const forbiddenLearnerFields = new Set([
  "answer",
  "correctanswer",
  "correctoptionids",
  "explanation",
  "preferredanalysis",
  "originalanalysis",
  "storedanalysis",
  "knowledgechunks",
  "teachingpoint",
  "scoringsnapshot",
]);

function assertNoForbiddenLearnerFields(value: unknown, path: string): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) => {
      assertNoForbiddenLearnerFields(item, `${path}[${String(index)}]`);
    });
    return;
  }
  const source = optionalRecord(value);
  if (!source) return;
  for (const [key, child] of Object.entries(source)) {
    const normalizedKey = key.replaceAll("_", "").toLowerCase();
    if (forbiddenLearnerFields.has(normalizedKey)) {
      throw new Error(`Learner-safe mock payload contains forbidden field ${path}.${key}`);
    }
    assertNoForbiddenLearnerFields(child, `${path}.${key}`);
  }
}

function field(source: Record<string, unknown>, name: string): unknown {
  return source[name];
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
  return value;
}

function optionalArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function valueFor(source: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) if (key in source) return source[key];
  return undefined;
}

function stringField(
  source: Record<string, unknown>,
  keys: string[],
  defaultValue?: string,
): string {
  const value = valueFor(source, keys);
  if (typeof value === "string") return value;
  if (defaultValue !== undefined) return defaultValue;
  throw new Error(`${keys[0] ?? "string field"} must be a string`);
}

function versionIdField(source: Record<string, unknown>, keys: string[]): string {
  const value = stringField(source, keys);
  if (!/^[a-f0-9]{64}$/u.test(value)) {
    throw new Error(`${keys[0] ?? "version identifier"} must be a 64-character digest`);
  }
  return value;
}

function nullableStringField(source: Record<string, unknown>, keys: string[]): string | null {
  const value = valueFor(source, keys);
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") throw new Error(`${keys[0] ?? "string field"} must be a string`);
  return value || null;
}

function stringArrayField(source: Record<string, unknown>, keys: string[]): string[] {
  const value = valueFor(source, keys);
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new Error(`${keys[0] ?? "string array"} must be an array`);
  return value.map((item) => {
    if (typeof item !== "string") {
      throw new Error(`${keys[0] ?? "string array"} must be a string array`);
    }
    return item;
  });
}

function numberField(source: Record<string, unknown>, keys: string[]): number {
  const value = valueFor(source, keys);
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${keys[0] ?? "number field"} must be a finite number`);
  }
  return value;
}

function integerField(source: Record<string, unknown>, keys: string[]): number {
  const value = numberField(source, keys);
  if (!Number.isInteger(value)) throw new Error(`${keys[0] ?? "integer field"} must be an integer`);
  return value;
}

function booleanField(
  source: Record<string, unknown>,
  keys: string[],
  defaultValue: boolean,
): boolean {
  const value = valueFor(source, keys);
  if (value === undefined) return defaultValue;
  if (typeof value !== "boolean") throw new Error(`${keys[0] ?? "boolean field"} must be boolean`);
  return value;
}

function contentKindField(source: Record<string, unknown>): "image" | "graph" {
  const value = stringField(source, ["contentKind", "content_kind"]);
  if (value !== "image" && value !== "graph") {
    throw new Error("Mock asset contentKind is invalid");
  }
  return value;
}

function isoInstantField(source: Record<string, unknown>, keys: string[]): string {
  const value = stringField(source, keys);
  if (!Number.isFinite(Date.parse(value))) throw new Error(`${keys[0] ?? "instant"} is invalid`);
  return value;
}
