import type { ReportProjection } from "../../api/generated/types.gen";
import { ReportingApi, type ReportKind } from "../../api/reporting";
import { routes } from "../../app/navigation/routes";
import { currentAuthSession } from "../../auth/session";
import { storeCompanionLaunchContext } from "../companion/context-store";
import {
  dateTimeLabel,
  errorView,
  requireAuthenticatedClient,
} from "../learning-brain/presentation";

type PageCode = "F03" | "F13" | "REPORT_OVERVIEW";

export function registerReportPage(defaultKind: ReportKind, pageCode: PageCode): void {
  Page({
    data: {
      status: "loading",
      errorMessage: "",
      requestId: "",
      report: null as ReportProjection | null,
      generatedAtLabel: "",
      evidenceCount: 0,
      planEvidenceCount: 0,
      partial: false,
      empty: false,
      kind: defaultKind,
      subjectRef: "current",
      pageCode,
    },
    onLoad(options: Record<string, string | undefined>) {
      const kind = pageCode === "REPORT_OVERVIEW" ? parseOverviewKind(options.kind) : defaultKind;
      const subjectRef = options.sessionId?.trim() || "current";
      this.setData({ kind, subjectRef });
      void this.loadReport();
    },
    async loadReport() {
      const client = requireAuthenticatedClient();
      if (!client) return;
      this.setData({ status: "loading", errorMessage: "", requestId: "" });
      try {
        const { data } = await new ReportingApi(client).getReport(
          this.data.kind,
          this.data.subjectRef,
        );
        this.setData({
          status: "ready",
          report: data,
          generatedAtLabel: dateTimeLabel(data.generatedAt),
          evidenceCount: new Set(data.charts.flatMap((chart) => chart.evidenceRefs)).size,
          planEvidenceCount: data.conclusion.nextPlanEvidenceRefs.length,
          partial: data.state === "partial",
          empty: data.state === "empty",
        });
      } catch (error) {
        const view = errorView(error, "报告加载失败，请重试。");
        this.setData({ status: "error", errorMessage: view.message, requestId: view.requestId });
      }
    },
    retry() {
      void this.loadReport();
    },
    back() {
      // 从报告中心进入时返回上一页；无返回栈（冷启动直达）时回今日计划。
      const pages = getCurrentPages();
      if (pages.length > 1) {
        void wx.navigateBack();
      } else {
        void wx.switchTab({ url: routes.today });
      }
    },
    openNextAction() {
      void wx.switchTab({ url: routes.today });
    },
    askAnan() {
      const auth = currentAuthSession();
      const report = this.data.report;
      if (!auth || !report) return;
      storeCompanionLaunchContext(auth.userId, {
        sourceLabel: report.title,
        title: report.conclusion.headline,
        summary: `${report.conclusion.detail}；报告建议：${report.conclusion.nextAction}`,
        suggestedQuestion:
          "结合这份报告的全部图表、结论和学习依据，告诉我最该先改变的一件事；如需调整计划，先给我看影响。",
        routeId: reportRoute(pageCode),
        workspace:
          report.reportKind === "single_set" || report.reportKind === "mock"
            ? "training"
            : "profile",
        objectRefs: [
          {
            type: "report",
            id: report.projectionVersionId,
            version: report.inputFingerprint,
          },
        ],
      });
      void wx.switchTab({ url: routes.anan });
    },
  });
}

function parseOverviewKind(value: string | undefined): ReportKind {
  if (value === "single_set" || value === "weekly" || value === "position_gap") return value;
  return "weekly";
}

function reportRoute(pageCode: PageCode): string {
  if (pageCode === "F03") return "pages/mock-report/index";
  if (pageCode === "F13") return "pages/global-report/index";
  return "pages/report-overview/index";
}
