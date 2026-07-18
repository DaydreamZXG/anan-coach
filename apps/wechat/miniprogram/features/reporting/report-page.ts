import type { ReportProjection } from "../../api/generated/types.gen";
import { ReportingApi, type ReportKind } from "../../api/reporting";
import { routes } from "../../app/navigation/routes";
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
    openNextAction() {
      void wx.switchTab({ url: routes.today });
    },
  });
}

function parseOverviewKind(value: string | undefined): ReportKind {
  if (value === "single_set" || value === "weekly" || value === "position_gap") return value;
  return "weekly";
}
