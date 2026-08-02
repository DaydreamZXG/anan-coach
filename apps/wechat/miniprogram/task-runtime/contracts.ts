export type TaskMode = "practice" | "verification" | "mock_exam" | "essay";
export type TaskPhase =
  | "briefing"
  | "answering"
  | "feedback"
  | "reflection"
  | "candidate_review"
  | "variant_verification"
  | "summary"
  | "submitted";
export type SyncState = "saved" | "saving" | "pending" | "conflict" | "error";
export type ExitPolicy = "immediate" | "confirm" | "timer_continues" | "blocked";
export type AnswerConfidence = "low" | "medium" | "high";
export type TaskServerState =
  "answering" | "submitted" | "reflection_required" | "cause_decision_required" | "completed";

export interface MaterialPosition {
  anchorId: string;
  scrollTop: number;
  zoom: number;
}

/** Figma 70:2 题面批注：坐标归一化到批注层宽高（0..1）。 */
export interface AnnotationPoint {
  x: number;
  y: number;
  /** 契约增量 v1：相对本笔起笔的毫秒偏移，逐点非降序；旧数据缺省合法。 */
  t?: number;
  /**
   * 契约增量 v1：归一化压感（0 < p <= 1）。仅压感设备写入（微信 iOS 的
   * touch.force > 0 时按满压 3.3333 归一）；安卓/无压感设备不写该字段。
   */
  p?: number;
}

export interface AnnotationStroke {
  strokeId: string;
  tool: "pen" | "highlighter";
  width: number;
  points: AnnotationPoint[];
  atMs: number;
  /**
   * 契约增量 v1：擦除墓碑。橡皮/套索不再物理删除笔画，改为盖擦除时刻；
   * 渲染与回放过滤该字段非空的笔画（回放在 replayMs >= erasedAtMs 时消失）。
   */
  erasedAtMs?: number;
  /**
   * 契约增量 v2：锚定到具体文字段。blockId 与后端 RichParagraphBlock.blockId 同源
   * （由 scope+序号确定性生成，随 contentVersionId 冻结），segmentIndex 是该块内
   * 句段序号，segmentText 是用户标记的实际原文。AI 据此理解"标记了哪句"。
   * 旧笔迹无锚定字段时视为普通涂画。
   */
  anchor?: {
    blockId: string;
    segmentIndex: number;
    segmentText: string;
  } | null;
}

/** Figma 75:2/78:2 的作答动作事实。 */
export type AttemptActionType =
  | "option_selected"
  | "option_changed"
  | "option_eliminated"
  | "elimination_undone"
  | "confidence_set"
  | "draft_edited"
  | "marked"
  | "unmarked"
  | "skipped"
  | "paused"
  | "resumed"
  | "material_expanded"
  // 契约增量 v1：材料锚点滚动事实（detail=anchorId，≥2 秒节流）。
  | "material_scrolled"
  | "annotation_added"
  | "annotation_erased"
  // 契约增量 v1：跨题时间线的进出题事实（optionId/detail 不填）。
  | "question_entered"
  | "question_left";

export interface AttemptActionEvent {
  atMs: number;
  type: AttemptActionType;
  /** 契约增量 v1：会话全局累计毫秒时间线；旧数据缺省合法。 */
  sessionAtMs?: number;
  optionId?: string;
  detail?: string;
}

export interface QuestionAttemptDraft {
  questionId: string;
  selectedOptionIds: string[];
  eliminatedOptionIds: string[];
  confidence?: AnswerConfidence;
  marked: boolean;
  skipped: boolean;
  elapsedSeconds: number;
  /**
   * 契约增量 v1：本题活跃作答毫秒（Date.now() 差值累计，暂停/切题冻结）。
   * 缺省时以 elapsedSeconds * 1000 为准；仅本地使用，不随检查点上报。
   */
  elapsedMs?: number;
  draftText: string;
  materialPosition?: MaterialPosition;
  /**
   * 契约增量 v1：行测真实训练与整卷模考都必须采集动作事件；
   * 行测练习与模考共用批注层入口，笔迹随各自检查点上报。
   */
  annotations?: AnnotationStroke[];
  actionEvents?: AttemptActionEvent[];
  /** 契约增量 v1：动作事件达到 400 上限被截断时置 true；缺省等价 false。 */
  actionEventsTruncated?: boolean;
}

export interface TaskRuntimeState {
  taskId: string;
  serverSessionId?: string;
  planTaskId?: string;
  contentVersionId?: string;
  mode: TaskMode;
  phase: TaskPhase;
  serverState: TaskServerState;
  revision: number;
  syncState: SyncState;
  requestId?: string;
  questionOrder: string[];
  currentQuestionIndex: number;
  attempts: Record<string, QuestionAttemptDraft>;
  totalElapsedSeconds: number;
  /**
   * 契约增量 v1：会话全局活跃毫秒累计（sessionAtMs 的时钟源）。
   * 缺省时以 totalElapsedSeconds * 1000 为准；仅本地使用。
   */
  sessionElapsedMs?: number;
  exitPolicy: ExitPolicy;
  reflectionRequired: boolean;
  reflectionCompleted: boolean;
  causeDecisionRequired: boolean;
  causeDecisionCompleted: boolean;
  reviewId?: string;
  startedAt?: string;
  resultReceivedAt?: string;
  submittedAt?: string;
}

export type TaskRuntimeEvent =
  | { type: "TASK_STARTED"; startedAt: string }
  /** 契约增量 v1：ms 为本轮真实活跃毫秒（Date.now() 差值）；缺省按 seconds * 1000 累计。 */
  | { type: "TICK"; seconds: number; ms?: number }
  | { type: "QUESTION_CHANGED"; index: number }
  | { type: "ANSWER_SELECTED"; optionIds: string[] }
  | { type: "OPTION_ELIMINATED"; optionId: string; eliminated: boolean }
  | { type: "CONFIDENCE_SET"; confidence: AnswerConfidence }
  | { type: "QUESTION_MARKED"; marked: boolean }
  | { type: "QUESTION_SKIPPED" }
  | { type: "DRAFT_CHANGED"; text: string }
  | { type: "MATERIAL_POSITION_CHANGED"; position: MaterialPosition }
  | { type: "ANNOTATIONS_CHANGED"; strokes: AnnotationStroke[]; action: "added" | "erased" }
  | {
      type: "ACTION_LOGGED";
      action: "paused" | "resumed" | "material_expanded";
      detail?: string;
    }
  | { type: "SAVE_REQUESTED" }
  | { type: "SAVE_SUCCEEDED"; revision: number }
  | { type: "SAVE_FAILED"; requestId: string }
  | { type: "REVISION_CONFLICT"; requestId: string }
  | {
      type: "TASK_RESULT_RECEIVED";
      receivedAt: string;
      reviewId: string;
      causeDecisionRequired: boolean;
      revision: number;
    }
  | {
      type: "REFLECTION_COMMITTED";
      causeDecisionRequired: boolean;
      revision: number;
    }
  | { type: "CAUSE_DECISION_COMMITTED"; revision: number }
  | { type: "PHASE_CHANGED"; phase: TaskPhase }
  | { type: "TASK_SUBMITTED"; submittedAt: string };

export interface CreateTaskRuntimeInput {
  taskId: string;
  mode: TaskMode;
  questionOrder: string[];
  serverSessionId?: string | undefined;
  planTaskId?: string | undefined;
  contentVersionId?: string | undefined;
  serverState?: TaskServerState | undefined;
  phase?: TaskPhase | undefined;
  attempts?: Record<string, QuestionAttemptDraft> | undefined;
  currentQuestionIndex?: number | undefined;
  totalElapsedSeconds?: number | undefined;
  /**
   * 会话全局活跃毫秒。恢复检查点或本地草稿时显式传入，
   * createTaskRuntime 会与秒级累计取最大值，保证动作时间线不倒退。
   */
  sessionElapsedMs?: number | undefined;
  reflectionRequired?: boolean | undefined;
  reflectionCompleted?: boolean | undefined;
  causeDecisionRequired?: boolean | undefined;
  causeDecisionCompleted?: boolean | undefined;
  reviewId?: string | undefined;
  revision?: number | undefined;
}
