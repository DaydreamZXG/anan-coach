import { ensureSession } from "./auth";
import { request } from "./client";
import type {
  CompanionAskRequest,
  CompanionAskResponse,
  DailySettlementView,
  DiscoveryRequest,
  DiscoveryResponse,
  DraftCreate,
  DraftReceipt,
  GetAbilityEstimateApiV1CalibrationAbilityGetResponse,
  LearnerProfile,
  MasteryQueuePage,
  OnboardingProfileUpdate,
  OnboardingProgress,
  OnboardingResultProjection,
  PlacementAnswer,
  PlacementSubmitResponse,
  PlacementTestTask,
  PositionEligibilitySnapshot,
  PositionSearchResponse,
  PreparationExperienceWrite,
  PrimaryGoalConfirmResponse,
  PrimaryGoalPreview,
  QualificationMilestoneCommand,
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
