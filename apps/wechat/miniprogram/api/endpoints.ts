import { ensureSession } from "./auth";
import { request } from "./client";
import type {
  CompanionAskRequest,
  CompanionAskResponse,
  DailySettlementView,
  DiscoveryRequest,
  DiscoveryResponse,
  DocketOpenCourtResponse,
  DraftCreate,
  EssayAnswerCreateCommand,
  EssayAnswerCreateResult,
  EssayAttemptCreateCommand,
  EssayAttemptCreateResult,
  EssayCurrentReviewEnsureCommand,
  EssayCurrentReviewEnsureResult,
  EssayTaskWorkspace,
  MockExamReview,
  MockExamSession,
  MockExamSubmitRequest,
  MockExamSubmitResponse,
  DraftReceipt,
  GetAbilityEstimateApiV1CalibrationAbilityGetResponse,
  LearnerProfile,
  MasteryQueuePage,
  OnboardingProfileUpdate,
  OnboardingProgress,
  OnboardingResultProjection,
  PlacementAnswer,
  PlanChangeHistory,
  PlacementSubmitResponse,
  PlacementTestTask,
  PositionEligibilitySnapshot,
  PositionSearchResponse,
  PreparationExperienceWrite,
  PrimaryGoalConfirmResponse,
  PrimaryGoalPreview,
  QualificationMilestoneCommand,
  RetakeEnrollmentRequest,
  RetakeEnrollmentResult,
  RetakeWorkspace,
  ReportedScoreEvidenceRequest,
  ReportedScoreEvidenceResult,
  ReviewVerdictRequest,
  ReviewVerdictResponse,
  ScoreEvidenceHistory,
  SaveCheckpointCommand,
  StudyPreferenceWrite,
  SubmitTaskSessionCommand,
  TaskSessionProjection,
  TodayTaskstream,
} from "./generated/types.gen";

/** v2 写接口幂等键 */
export function idemKey(): string {
  return `mp-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export async function authedRequest<T>(options: {
  method: "GET" | "POST" | "PUT" | "DELETE";
  path: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined>;
  idempotencyKey?: string;
}): Promise<T> {
  const session = await ensureSession();
  return request<T>({
    method: options.method,
    path: options.path,
    ...(options.body !== undefined ? { body: options.body } : {}),
    ...(options.query !== undefined ? { query: options.query } : {}),
    token: session.accessToken,
    ...(options.idempotencyKey
      ? { extraHeaders: { "Idempotency-Key": options.idempotencyKey } }
      : {}),
  });
}

/** v2 响应统一为 envelope，这里解出 data */
export async function authedV2<T>(options: Parameters<typeof authedRequest>[0]): Promise<T> {
  const envelope = await authedRequest<{ data: T }>(options);
  return envelope.data;
}

// ═══════ 今日 / 能力 ═══════

/** 今日任务流（今日页主数据源） */
export async function fetchTodayTaskstream(): Promise<TodayTaskstream> {
  return authedRequest<TodayTaskstream>({ method: "GET", path: "/api/v1/taskstream/today" });
}

/** 今日结算视图（daily-settlement@v1 契约，v2 envelope） */
export async function fetchDailySettlement(): Promise<DailySettlementView> {
  return authedV2<DailySettlementView>({
    method: "GET",
    path: "/api/v2/plans/today/settlement",
  });
}

/** 能力估分（能力页主数据源） */
export async function fetchAbilityEstimate(): Promise<GetAbilityEstimateApiV1CalibrationAbilityGetResponse> {
  return authedRequest<GetAbilityEstimateApiV1CalibrationAbilityGetResponse>({
    method: "GET",
    path: "/api/v1/calibration/ability",
  });
}

// ═══════ 复盘（开庭） ═══════

/** 开庭：取回今天该复盘的错题卡组（v1 无 envelope） */
export async function openReviewCourt(): Promise<DocketOpenCourtResponse> {
  return authedRequest<DocketOpenCourtResponse>({
    method: "POST",
    path: "/api/v1/dockets/open-court",
  });
}

/** 对单张复盘卡提交判定，返回 准/驳 与岸岸点评 */
export async function submitReviewVerdict(
  docketId: string,
  cardId: string,
  body: ReviewVerdictRequest,
): Promise<ReviewVerdictResponse> {
  return authedRequest<ReviewVerdictResponse>({
    method: "POST",
    path: `/api/v1/review-sessions/${docketId}/cards/${cardId}/verdict`,
    body,
  });
}

// ═══════ 申论智能 ═══════

/** 申论任务工作台：素材 + 题干 + 进度 + 评分标尺 */
export async function fetchEssayWorkspace(taskVersionId: string): Promise<EssayTaskWorkspace> {
  return authedV2<EssayTaskWorkspace>({
    method: "GET",
    path: `/api/v2/essay-intelligence/tasks/${taskVersionId}/workspace`,
  });
}

/** 开启一次申论作答（幂等） */
export async function createEssayAttempt(
  body: EssayAttemptCreateCommand,
  idempotencyKey: string,
): Promise<EssayAttemptCreateResult> {
  return authedV2<EssayAttemptCreateResult>({
    method: "POST",
    path: "/api/v2/essay-intelligence/attempts",
    body,
    idempotencyKey,
  });
}

/** 提交申论作答文本（幂等） */
export async function createEssayAnswer(
  body: EssayAnswerCreateCommand,
  idempotencyKey: string,
): Promise<EssayAnswerCreateResult> {
  return authedV2<EssayAnswerCreateResult>({
    method: "POST",
    path: "/api/v2/essay-intelligence/answers",
    body,
    idempotencyKey,
  });
}

/** 确保当前作答有一份 grounded 批改（幂等，可能返回排队中的 job） */
export async function ensureEssayReview(
  body: EssayCurrentReviewEnsureCommand,
  idempotencyKey: string,
): Promise<EssayCurrentReviewEnsureResult> {
  return authedV2<EssayCurrentReviewEnsureResult>({
    method: "POST",
    path: "/api/v2/essay-intelligence/reviews/current/ensure",
    body,
    idempotencyKey,
  });
}

// ═══════ 模考 ═══════

/** 创建或取回本周模考场次（v1 无 envelope） */
export async function createWeeklyMockExam(): Promise<MockExamSession> {
  return authedRequest<MockExamSession>({ method: "POST", path: "/api/v1/mock-exams/weekly" });
}

/** 交卷，返回本场复盘报告 */
export async function submitMockExam(
  sessionId: string,
  body: MockExamSubmitRequest,
): Promise<MockExamSubmitResponse> {
  return authedRequest<MockExamSubmitResponse>({
    method: "POST",
    path: `/api/v1/mock-exams/${sessionId}/submit`,
    body,
  });
}

/** 取回模考复盘报告 */
export async function fetchMockExamReview(sessionId: string): Promise<MockExamReview> {
  return authedRequest<MockExamReview>({
    method: "GET",
    path: `/api/v1/mock-exams/${sessionId}/review`,
  });
}

/** 标记报告已读 */
export async function markMockReviewViewed(sessionId: string): Promise<MockExamReview> {
  return authedRequest<MockExamReview>({
    method: "POST",
    path: `/api/v1/mock-exams/${sessionId}/review/view`,
  });
}

// ═══════ 二战恢复计划 ═══════

/** 二战工作台：可用分数凭证 + 当前恢复计划 */
export async function fetchRetakeWorkspace(): Promise<RetakeWorkspace> {
  return authedV2<RetakeWorkspace>({ method: "GET", path: "/api/v2/retake" });
}

/** 用一条分数凭证报名恢复计划（幂等） */
export async function enrollRetakeProgram(
  body: RetakeEnrollmentRequest,
  idempotencyKey: string,
): Promise<RetakeEnrollmentResult> {
  return authedV2<RetakeEnrollmentResult>({
    method: "POST",
    path: "/api/v2/retake/programs",
    body,
    idempotencyKey,
  });
}

/** 分数凭证历史 */
export async function fetchScoreEvidenceHistory(): Promise<ScoreEvidenceHistory> {
  return authedV2<ScoreEvidenceHistory>({ method: "GET", path: "/api/v2/score-evidence" });
}

/** 自报一条分数凭证（幂等），返回新凭证与更新后的能力估计 */
export async function reportScoreEvidence(
  body: ReportedScoreEvidenceRequest,
  idempotencyKey: string,
): Promise<ReportedScoreEvidenceResult> {
  return authedV2<ReportedScoreEvidenceResult>({
    method: "POST",
    path: "/api/v2/score-evidence/reported",
    body,
    idempotencyKey,
  });
}

/** 计划变更历史（我的页-计划历史） */
export async function fetchPlanHistory(): Promise<PlanChangeHistory> {
  return authedV2<PlanChangeHistory>({ method: "GET", path: "/api/v2/plans/history" });
}

// ═══════ 岸岸（AI 伴学） ═══════

/** 向岸岸提问（岸岸页主数据源，v2 幂等） */
export async function askCompanion(body: CompanionAskRequest): Promise<CompanionAskResponse> {
  return authedV2<CompanionAskResponse>({
    method: "POST",
    path: "/api/v2/companion/ask",
    body,
    idempotencyKey: idemKey(),
  });
}

// ═══════ 社区知识卡 ═══════

/** 知识卡广场：按赛道 + 适用范围发现他人卡片 */
export async function discoverMethodCards(body: DiscoveryRequest): Promise<DiscoveryResponse> {
  return authedV2<DiscoveryResponse>({
    method: "POST",
    path: "/api/v2/community-method-cards/discovery",
    body,
  });
}

/** 发布知识卡草稿（结构化 UGC 入口） */
export async function createMethodCardDraft(
  body: Omit<DraftCreate, "idempotencyKey">
): Promise<DraftReceipt> {
  const key = idemKey();
  return authedV2<DraftReceipt>({
    method: "POST",
    path: "/api/v2/community-method-cards/drafts",
    body: { ...body, idempotencyKey: key },
    idempotencyKey: key,
  });
}

// ═══════ Onboarding ═══════

/** v2 引导进度（步骤状态机与各资源版本号） */
export async function fetchOnboardingProgress(): Promise<OnboardingProgress> {
  return authedV2<OnboardingProgress>({ method: "GET", path: "/api/v2/onboarding/progress" });
}

/** 步骤一：备考经历 */
export async function putPreparationExperience(body: PreparationExperienceWrite): Promise<unknown> {
  return authedV2<unknown>({
    method: "PUT",
    path: "/api/v2/onboarding/preparation-experience",
    body,
    idempotencyKey: idemKey(),
  });
}

/** 资格资料写入（v1 画像接口） */
export async function updateOnboardingProfile(
  body: OnboardingProfileUpdate,
): Promise<LearnerProfile> {
  return authedRequest<LearnerProfile>({
    method: "PUT",
    path: "/api/v1/onboarding/profile",
    body,
  });
}

/** 步骤二确认：资格 milestone */
export async function completeQualificationMilestone(
  body: QualificationMilestoneCommand,
): Promise<unknown> {
  return authedV2<unknown>({
    method: "POST",
    path: "/api/v2/onboarding/qualification/complete",
    body,
    idempotencyKey: idemKey(),
  });
}

/** 步骤三：每周学习节奏 */
export async function putStudyPreference(body: StudyPreferenceWrite): Promise<unknown> {
  return authedV2<unknown>({
    method: "PUT",
    path: "/api/v2/onboarding/study-preference",
    body,
    idempotencyKey: idemKey(),
  });
}

/** 完成引导：确认结果页 */
export async function acknowledgeOnboarding(
  expectedProgressVersion: number,
): Promise<OnboardingResultProjection> {
  return authedV2<OnboardingResultProjection>({
    method: "POST",
    path: "/api/v2/onboarding/complete",
    body: { acknowledgeResult: true, expectedProgressVersion },
    idempotencyKey: idemKey(),
  });
}

// ═══════ 岗位与目标 ═══════

export interface PositionSearchQuery {
  education: string;
  major: string;
  politicalStatus: string;
  grassrootsYears?: number;
  freshGraduate?: boolean;
  region?: string;
  city?: string;
  limit?: number;
}

/** 可报岗位搜索 */
export async function searchPositions(query: PositionSearchQuery): Promise<PositionSearchResponse> {
  return authedRequest<PositionSearchResponse>({
    method: "GET",
    path: "/api/v1/positions/search",
    query: { ...query },
  });
}

/** 岗位资格评估（产出目标分 preview 所需的快照） */
export async function evaluatePositionEligibility(
  positionId: string,
): Promise<PositionEligibilitySnapshot> {
  return authedV2<PositionEligibilitySnapshot>({
    method: "POST",
    path: `/api/v2/positions/${encodeURIComponent(positionId)}/eligibility`,
    idempotencyKey: idemKey(),
  });
}

/** 主目标 preview：拿到目标分区间与影响面 */
export async function previewPrimaryGoal(input: {
  positionId: string;
  eligibilitySnapshotId: string;
}): Promise<PrimaryGoalPreview> {
  return authedV2<PrimaryGoalPreview>({
    method: "POST",
    path: "/api/v2/position-goals/primary/preview",
    body: { positionId: input.positionId, eligibilitySnapshotId: input.eligibilitySnapshotId },
    idempotencyKey: idemKey(),
  });
}

/** 确认主目标 */
export async function confirmPrimaryGoal(input: {
  previewId: string;
  changeReason: string;
}): Promise<PrimaryGoalConfirmResponse> {
  return authedV2<PrimaryGoalConfirmResponse>({
    method: "PUT",
    path: "/api/v2/position-goals/primary",
    body: { previewId: input.previewId, changeReason: input.changeReason },
    idempotencyKey: idemKey(),
  });
}

// ═══════ 定位测试 ═══════

export async function fetchPlacementTest(): Promise<PlacementTestTask> {
  return authedRequest<PlacementTestTask>({
    method: "GET",
    path: "/api/v1/onboarding/placement-test",
  });
}

export async function submitPlacementTest(
  answers: PlacementAnswer[],
): Promise<PlacementSubmitResponse> {
  return authedRequest<PlacementSubmitResponse>({
    method: "POST",
    path: "/api/v1/onboarding/placement-test/answers",
    body: { answers },
  });
}

// ═══════ 掌握队列 / 复盘 ═══════

export type MasteryBucket =
  "pending_reflection" | "due_verification" | "stable_mastery" | "invalidated";

/** 掌握队列（复盘入口数据源），按桶分页 */
export async function fetchMasteryQueue(
  bucket: MasteryBucket,
  cursor?: string,
): Promise<MasteryQueuePage> {
  return authedV2<MasteryQueuePage>({
    method: "GET",
    path: "/api/v2/xingce/mastery-queue",
    query: { bucket, pageSize: 30, ...(cursor ? { cursor } : {}) },
  });
}

// ═══════ 行测做题会话 ═══════

/** 开始或续做一组任务（幂等） */
export async function startTaskSession(planTaskId: string): Promise<TaskSessionProjection> {
  return authedV2<TaskSessionProjection>({
    method: "POST",
    path: "/api/v2/xingce/task-sessions",
    body: { planTaskId },
    idempotencyKey: idemKey(),
  });
}

/** 作答 checkpoint（带 revision 乐观锁） */
export async function saveTaskCheckpoint(
  sessionId: string,
  body: SaveCheckpointCommand,
): Promise<TaskSessionProjection> {
  return authedV2<TaskSessionProjection>({
    method: "PUT",
    path: `/api/v2/xingce/task-sessions/${encodeURIComponent(sessionId)}/checkpoint`,
    body,
  });
}

/** 交卷 */
export async function submitTaskSession(
  sessionId: string,
  body: SubmitTaskSessionCommand,
): Promise<TaskSessionProjection> {
  return authedV2<TaskSessionProjection>({
    method: "POST",
    path: `/api/v2/xingce/task-sessions/${encodeURIComponent(sessionId)}/submit`,
    body,
    idempotencyKey: idemKey(),
  });
}
