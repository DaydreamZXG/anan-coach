import type {
  ExternalMockReportDraftCommand,
  ExternalMockReportProjection,
} from "../../api/generated/types.gen";
import { ReportingApi } from "../../api/reporting";
import { routes } from "../../app/navigation/routes";
import {
  confirmedExternalScoreRevision,
  editExternalScoreSpan,
  emptyExternalScoreState,
  externalScoreNeedsCorrection,
  reviewingExternalScoreState,
  toggleExternalScoreSpan,
  verifiedExternalScoreSpans,
  type ExternalScoreOcrState,
} from "../../features/reporting/external-score-workflow";
import { errorView, requireAuthenticatedClient } from "../../features/learning-brain/presentation";
import { createIdempotencyKey } from "../../platform/command-id";
import {
  chooseSourceImage,
  ensureCameraPermission,
  requestCameraSetting,
  type LocalSourceImageFile,
} from "../../platform/source-image";

interface ExternalScoreForm {
  platformName: string;
  examName: string;
  occurredDate: string;
  timedStatus: "timed" | "untimed" | "unknown";
  durationMinutes: string;
  totalScore: string;
  xingceScore: string;
  shenlunScore: string;
  moduleScoresText: string;
}

Page({
  data: {
    workflow: emptyExternalScoreState(),
    errorMessage: "",
    requestId: "",
    form: initialForm(),
    candidate: null as ExternalMockReportProjection | null,
    submitting: false,
    evidenceLabel: "",
    abilityLabel: "",
  },
  chooseCamera() {
    void this.selectImage("camera");
  },
  chooseAlbum() {
    void this.selectImage("album");
  },
  async selectImage(source: "camera" | "album") {
    try {
      if (source === "camera") {
        const permission = await ensureCameraPermission();
        if (permission === "denied" && !(await requestCameraSetting())) return;
      }
      const file = await chooseSourceImage(source, "external-mock-score.jpg");
      if (file) await this.startRecognition(file);
    } catch (error) {
      this.setWorkflowError(error, "成绩截图没有选取成功。");
    }
  },
  async startRecognition(file: LocalSourceImageFile) {
    const client = requireAuthenticatedClient();
    if (!client) return;
    this.setData({
      workflow: {
        ...emptyExternalScoreState(),
        phase: "uploading",
        localImagePath: file.localPath,
        message: "正在上传并识别成绩截图……",
      } satisfies ExternalScoreOcrState,
      errorMessage: "",
      requestId: "",
      candidate: null,
    });
    try {
      const revision = await new ReportingApi(client).createExternalScoreRecognition(
        file,
        await createIdempotencyKey("external-score-ocr"),
        (progressPercent) => {
          this.setData({ workflow: { ...this.data.workflow, progressPercent } });
        },
      );
      this.setData({ workflow: reviewingExternalScoreState(revision, file.localPath) });
    } catch (error) {
      this.setWorkflowError(error, "成绩图片没有识别成功。");
    }
  },
  editSpan(event: WechatMiniprogram.Input) {
    const spanId = String(event.currentTarget.dataset.id ?? "");
    this.setData({
      workflow: editExternalScoreSpan(this.data.workflow, spanId, event.detail.value),
    });
  },
  toggleSpan(event: WechatMiniprogram.BaseEvent) {
    const spanId = String(event.currentTarget.dataset.id ?? "");
    this.setData({ workflow: toggleExternalScoreSpan(this.data.workflow, spanId) });
  },
  async confirmRecognition() {
    const client = requireAuthenticatedClient();
    const revision = this.data.workflow.revision;
    if (!client || !revision || this.data.submitting) return;
    this.setData({ submitting: true, errorMessage: "" });
    try {
      const spans = verifiedExternalScoreSpans(this.data.workflow);
      const api = new ReportingApi(client);
      const corrected = externalScoreNeedsCorrection(this.data.workflow)
        ? await api.correctExternalScoreRecognition(
            revision,
            spans,
            await createIdempotencyKey("external-score-correction"),
          )
        : revision;
      const confirmed = confirmedExternalScoreRevision(
        await api.confirmExternalScoreRecognition(
          corrected,
          await createIdempotencyKey("external-score-transcription-confirm"),
        ),
      );
      this.setData({
        workflow: {
          ...this.data.workflow,
          phase: "structured_form",
          revision: confirmed,
          message: "识别文字已确认。请继续核对平台、时间、计时状态与分数。",
        },
      });
    } catch (error) {
      this.setWorkflowError(error, "识别文字没有确认成功。");
    } finally {
      this.setData({ submitting: false });
    }
  },
  updateField(event: WechatMiniprogram.Input) {
    const field = String(event.currentTarget.dataset.field ?? "") as keyof ExternalScoreForm;
    if (!(field in this.data.form)) return;
    this.setData({ form: { ...this.data.form, [field]: event.detail.value } });
  },
  updateDate(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ form: { ...this.data.form, occurredDate: event.detail.value } });
  },
  setTimedStatus(event: WechatMiniprogram.BaseEvent) {
    const value = String(event.currentTarget.dataset.value ?? "");
    if (value !== "timed" && value !== "untimed" && value !== "unknown") return;
    this.setData({
      form: {
        ...this.data.form,
        timedStatus: value,
        durationMinutes: value === "timed" ? this.data.form.durationMinutes : "",
      },
    });
  },
  async createCandidate() {
    const client = requireAuthenticatedClient();
    const revision = this.data.workflow.revision;
    if (!client || !revision || this.data.submitting) return;
    this.setData({ submitting: true, errorMessage: "" });
    try {
      const command = buildDraftCommand(this.data.form, confirmedExternalScoreRevision(revision));
      const { data } = await new ReportingApi(client).createExternalMockDraft(
        command,
        await createIdempotencyKey("external-score-structured-draft"),
      );
      this.setData({
        candidate: data,
        workflow: {
          ...this.data.workflow,
          phase: data.state === "confirmed" ? "confirmed" : "final_review",
          message: data.confirmationMessage,
        },
        evidenceLabel: evidenceGradeLabel(data.evidenceGrade),
        abilityLabel: abilityImpactLabel(data.abilityImpact),
      });
    } catch (error) {
      this.setWorkflowError(error, "没有识别出可核对的成绩字段。");
    } finally {
      this.setData({ submitting: false });
    }
  },
  async confirmCandidate() {
    const client = requireAuthenticatedClient();
    const candidate = this.data.candidate;
    if (!client || !candidate || this.data.submitting) return;
    this.setData({ submitting: true, errorMessage: "" });
    try {
      const { data } = await new ReportingApi(client).confirmExternalMock(
        candidate.reportVersionId,
        await createIdempotencyKey("external-score-final-confirm"),
      );
      this.setData({
        candidate: data,
        workflow: { ...this.data.workflow, phase: "confirmed", message: data.confirmationMessage },
        evidenceLabel: evidenceGradeLabel(data.evidenceGrade),
        abilityLabel: abilityImpactLabel(data.abilityImpact),
      });
    } catch (error) {
      this.setWorkflowError(error, "外部模考单没有完成最终确认。");
    } finally {
      this.setData({ submitting: false });
    }
  },
  restart() {
    this.setData({
      workflow: emptyExternalScoreState(),
      errorMessage: "",
      requestId: "",
      form: initialForm(),
      candidate: null,
      evidenceLabel: "",
      abilityLabel: "",
    });
  },
  openLearningProfile() {
    void wx.switchTab({ url: routes.ability });
  },
  returnToday() {
    void wx.switchTab({ url: routes.today });
  },
  setWorkflowError(error: unknown, fallback: string) {
    const view = errorView(error, fallback);
    this.setData({
      workflow: { ...this.data.workflow, phase: "error", message: view.message },
      errorMessage: view.message,
      requestId: view.requestId,
    });
  },
});

function initialForm(): ExternalScoreForm {
  const today = new Date();
  return {
    platformName: "粉笔",
    examName: "",
    occurredDate: `${String(today.getFullYear())}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`,
    timedStatus: "timed",
    durationMinutes: "120",
    totalScore: "",
    xingceScore: "",
    shenlunScore: "",
    moduleScoresText: "",
  };
}

function buildDraftCommand(
  form: ExternalScoreForm,
  revision: ReturnType<typeof confirmedExternalScoreRevision>,
): ExternalMockReportDraftCommand {
  const platformName = required(form.platformName, "平台名称");
  const examName = required(form.examName, "模考名称");
  const durationMinutes = optionalNumber(form.durationMinutes, "作答时长");
  if (form.timedStatus === "timed" && durationMinutes === undefined) {
    throw new Error("计时模考必须填写作答时长。");
  }
  return {
    artifactVersionId: revision.artifactVersionId,
    recognitionRevisionId: revision.recognitionRevisionId,
    platformName,
    examName,
    occurredAt: new Date(`${form.occurredDate}T12:00:00+08:00`).toISOString(),
    timedStatus: form.timedStatus,
    durationMinutes: form.timedStatus === "timed" ? (durationMinutes ?? null) : null,
    totalScore: optionalNumber(form.totalScore, "总分") ?? null,
    xingceScore: optionalNumber(form.xingceScore, "行测分") ?? null,
    shenlunScore: optionalNumber(form.shenlunScore, "申论分") ?? null,
    moduleScores: parseModuleScores(form.moduleScoresText),
  };
}

function parseModuleScores(value: string): Record<string, number> {
  const normalized = value.trim();
  if (!normalized) return {};
  return Object.fromEntries(
    normalized.split(/[,，\n]/).map((part) => {
      const [name = "", raw = ""] = part.split(/[:：]/, 2);
      const key = required(name, "模块名称");
      const score = optionalNumber(raw, `${key}分数`);
      if (score === undefined) throw new Error(`${key}缺少分数。`);
      return [key, score];
    }),
  );
}

function optionalNumber(value: string, label: string): number | undefined {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) throw new Error(`${label}必须是非负数字。`);
  return parsed;
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`请填写${label}。`);
  return normalized;
}

function evidenceGradeLabel(value: ExternalMockReportProjection["evidenceGrade"]): string {
  return {
    candidate_unconfirmed: "候选未确认",
    ability_eligible: "可进入能力估计",
    trend_only_untimed: "仅趋势 · 未计时",
    trend_only_incomplete: "仅趋势 · 科目不完整",
    trend_only_stale: "仅趋势 · 证据过期",
  }[value];
}

function abilityImpactLabel(value: ExternalMockReportProjection["abilityImpact"]): string {
  return {
    blocked_until_confirmed: "未进入能力估计",
    entered_estimate: "已更新能力分析",
    trend_only: "仅用于趋势，不改写能力值",
  }[value];
}
