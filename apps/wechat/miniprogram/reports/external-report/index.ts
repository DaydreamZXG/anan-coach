import type { ExternalMockReportProjection } from "../../api/generated/types.gen";
import { ReportingApi } from "../shared/api/reporting";
import { routes } from "../../app/navigation/routes";
import { currentAuthSession } from "../../auth/session";
import {
  markCoachEntryEvidence,
  readCoachEntryIntent,
} from "../../features/onboarding/coach-entry";
import {
  confirmedExternalScoreRevision,
  editExternalScoreSpan,
  emptyExternalScoreState,
  externalScoreNeedsCorrection,
  reviewingExternalScoreState,
  toggleExternalScoreSpan,
  verifiedExternalScoreSpans,
  ExternalScoreWorkflowError,
  type ExternalScoreOcrState,
} from "../shared/features/reporting/external-score-workflow";
import {
  buildExternalScoreDraftCommand,
  ExternalScoreFormError,
  initialExternalScoreForm,
  type ExternalScoreForm,
} from "./features/form";
import { errorView, requireAuthenticatedClient } from "../../features/learning-brain/presentation";
import { createIdempotencyKey } from "../../platform/command-id";
import {
  chooseSourceImage,
  ensureCameraPermission,
  requestCameraSetting,
  type LocalSourceImageFile,
} from "../shared/platform/source-image";

Page({
  data: {
    workflow: emptyExternalScoreState(),
    errorMessage: "",
    requestId: "",
    form: initialExternalScoreForm(),
    formErrorMessage: "",
    formErrorField: "",
    candidate: null as ExternalMockReportProjection | null,
    submitting: false,
    evidenceLabel: "",
    abilityLabel: "",
    coachEntryActive: false,
  },
  onLoad() {
    const session = currentAuthSession();
    this.setData({
      coachEntryActive:
        session !== undefined && readCoachEntryIntent(session.userId)?.path === "score_report",
    });
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
    this.setData({
      form: { ...this.data.form, [field]: event.detail.value },
      formErrorMessage: "",
      formErrorField: "",
    });
  },
  updateDate(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({
      form: { ...this.data.form, occurredDate: event.detail.value },
      formErrorMessage: "",
      formErrorField: "",
    });
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
      formErrorMessage: "",
      formErrorField: "",
    });
  },
  async createCandidate() {
    const client = requireAuthenticatedClient();
    const revision = this.data.workflow.revision;
    if (!client || !revision || this.data.submitting) return;
    this.setData({
      submitting: true,
      errorMessage: "",
      formErrorMessage: "",
      formErrorField: "",
    });
    try {
      const command = buildExternalScoreDraftCommand(
        this.data.form,
        confirmedExternalScoreRevision(revision),
      );
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
      if (data.state === "confirmed") markCurrentCoachEntryEvidence();
    } catch (error) {
      if (error instanceof ExternalScoreFormError) {
        this.setData({
          formErrorMessage: error.message,
          formErrorField: error.field,
          requestId: "",
        });
      } else {
        this.setWorkflowError(error, "成绩草稿没有保存成功，请稍后再试。");
      }
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
      markCurrentCoachEntryEvidence();
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
      form: initialExternalScoreForm(),
      formErrorMessage: "",
      formErrorField: "",
      candidate: null,
      evidenceLabel: "",
      abilityLabel: "",
    });
  },
  openLearningProfile() {
    void wx.switchTab({ url: routes.ability });
  },
  continueCoachMeeting() {
    markCurrentCoachEntryEvidence();
    void wx.switchTab({ url: routes.today });
  },
  returnToday() {
    void wx.switchTab({ url: routes.today });
  },
  setWorkflowError(error: unknown, fallback: string) {
    const view =
      error instanceof ExternalScoreWorkflowError
        ? { message: error.message, requestId: "" }
        : errorView(error, fallback);
    this.setData({
      workflow: { ...this.data.workflow, phase: "error", message: view.message },
      errorMessage: view.message,
      requestId: view.requestId,
    });
  },
});

function markCurrentCoachEntryEvidence(): void {
  const session = currentAuthSession();
  if (session) markCoachEntryEvidence(session.userId, "score_report");
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
