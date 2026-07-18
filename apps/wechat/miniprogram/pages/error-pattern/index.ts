import { LearningBrainApi } from "../../api/learning-brain";
import type { ErrorPatternDetail } from "../../api/generated/types.gen";
import {
  dateTimeLabel,
  errorView,
  requireAuthenticatedClient,
} from "../../features/learning-brain/presentation";

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    detail: null as ErrorPatternDetail | null,
    latestLabel: "",
    patternId: "",
    retryAllowed: false,
  },
  onLoad(options: Record<string, string | undefined>) {
    this.setData({ patternId: options.patternId?.trim() ?? "" });
    void this.loadPattern();
  },
  async loadPattern() {
    const patternId = this.data.patternId;
    if (!patternId) {
      this.setData({
        status: "error",
        errorMessage: "错误模式地址缺少标识。",
        retryAllowed: false,
      });
      return;
    }
    const client = requireAuthenticatedClient();
    if (!client) return;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const { data } = await new LearningBrainApi(client).getErrorPattern(patternId);
      this.setData({
        status: "ready",
        detail: data,
        latestLabel: dateTimeLabel(data.pattern.latestObservedAt),
        retryAllowed: false,
      });
    } catch (error) {
      const view = errorView(error, "错误模式加载失败。");
      this.setData({
        status: "error",
        errorMessage: view.message,
        requestId: view.requestId,
        retryAllowed: true,
      });
    }
  },
  retryLoad() {
    if (this.data.retryAllowed) void this.loadPattern();
  },
});
