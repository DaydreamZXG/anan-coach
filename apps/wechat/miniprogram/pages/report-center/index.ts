import type { ReportCenterProjection } from "../../api/generated/types.gen";
import { MockExamApi } from "../../api/mock-exam";
import { ReportingApi } from "../../api/reporting";
import { registeredNavigationTarget, routes } from "../../app/navigation/routes";
import { createIdempotencyKey } from "../../platform/command-id";
import {
  dateTimeLabel,
  errorView,
  requireAuthenticatedClient,
} from "../../features/learning-brain/presentation";

type PaperView = ReportCenterProjection["standardPapers"][number] & {
  statusLabel: string;
  actionLabel: string;
};

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    center: null as ReportCenterProjection | null,
    generatedAtLabel: "",
    papers: [] as PaperView[],
    startingMock: false,
  },
  onShow() {
    void this.loadCenter();
  },
  async loadCenter() {
    const client = requireAuthenticatedClient();
    if (!client) return;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const { data } = await new ReportingApi(client).getCenter();
      const statusLabels = {
        available: "可开始",
        in_progress: "作答中",
        submitted: "已交卷",
      } as const;
      this.setData({
        status: "ready",
        center: data,
        generatedAtLabel: dateTimeLabel(data.generatedAt),
        papers: data.standardPapers.map((paper) => ({
          ...paper,
          statusLabel: statusLabels[paper.status],
          actionLabel:
            paper.status === "submitted"
              ? "查看报告"
              : paper.status === "in_progress"
                ? "继续作答"
                : "开始作答",
        })),
      });
    } catch (error) {
      const view = errorView(error, "报告中心加载失败，请重试。");
      this.setData({ status: "error", errorMessage: view.message, requestId: view.requestId });
    }
  },
  retry() {
    void this.loadCenter();
  },
  async startWeeklyMock() {
    if (this.data.startingMock) return;
    const client = requireAuthenticatedClient();
    if (!client) return;
    this.setData({ startingMock: true });
    try {
      const session = await new MockExamApi(client).createOrRestoreWeekly(
        await createIdempotencyKey("report-center-mock"),
      );
      const route =
        session.status === "assigned"
          ? `${routes.mockExam}?sessionId=${encodeURIComponent(session.sessionId)}`
          : `${routes.mockReport}?sessionId=${encodeURIComponent(session.sessionId)}`;
      await wx.navigateTo({ url: route });
    } catch (error) {
      const view = errorView(error, "标准卷没有创建成功，请重试。");
      this.setData({ errorMessage: view.message, requestId: view.requestId });
    } finally {
      this.setData({ startingMock: false });
    }
  },
  openPaper(event: WechatMiniprogram.BaseEvent) {
    const paper = this.data.papers[Number(event.currentTarget.dataset.index)];
    if (!paper) return;
    const url =
      paper.status === "submitted"
        ? `${routes.mockReport}?sessionId=${encodeURIComponent(paper.sessionId ?? "")}`
        : paper.sessionId
          ? `${routes.mockExam}?sessionId=${encodeURIComponent(paper.sessionId)}`
          : routes.mockExam;
    void wx.navigateTo({ url });
  },
  openExternalScore() {
    void wx.navigateTo({ url: routes.externalReport });
  },
  openReport(event: WechatMiniprogram.BaseEvent) {
    const index = Number(event.currentTarget.dataset.index);
    const card = this.data.center?.reportCards[index];
    if (!card) return;
    const target = registeredNavigationTarget(card.route);
    if (target?.mode === "navigateTo") void wx.navigateTo({ url: target.url });
  },
});
