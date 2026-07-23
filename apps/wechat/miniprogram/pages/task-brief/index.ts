import { fetchTaskBriefing } from "../../api/endpoints";

const MODE_LABEL: Record<string, string> = {
  baseline: "摸清现状",
  targeted: "定向补弱",
  verification: "验证掌握",
};

Page({
  data: {
    loading: true,
    taskId: "",
    title: "",
    reason: "",
    modeLabel: "",
    moduleName: "",
    questionCount: 0,
    targetMinutes: 0,
    perQuestionSeconds: 0,
    starting: false,
  },

  onLoad(query: Record<string, string>) {
    const taskId = query.taskId || query.planTaskId || "";
    this.setData({ taskId });
    if (!taskId) {
      // 没有任务 ID，回今日页重新选任务
      wx.showToast({ title: "没找到这项任务，回今日页看看", icon: "none" });
      setTimeout(() => wx.switchTab({ url: "/pages/today/index" }), 1200);
      return;
    }
    void this.load(taskId);
  },

  async load(taskId: string) {
    try {
      const brief = await fetchTaskBriefing(taskId);
      this.setData({
        loading: false,
        title: brief.title,
        reason: brief.reason,
        modeLabel: MODE_LABEL[brief.mode] ?? brief.mode,
        moduleName: brief.moduleName ?? "",
        questionCount: brief.questionCount ?? 3,
        targetMinutes: Math.max(1, Math.round(brief.timing.targetSeconds / 60)),
        perQuestionSeconds: brief.timing.perQuestionTargetSeconds,
      });
    } catch (err) {
      // 简报拿不到不拦路：直接进练习
      console.warn("[task-brief] briefing unavailable, going straight to practice", err);
      this.goPractice();
    }
  },

  goPractice() {
    wx.redirectTo({
      url: `/pages/practice/index?taskId=${this.data.taskId}`,
      fail: () => wx.switchTab({ url: "/pages/today/index" }),
    });
  },

  onStart() {
    if (this.data.starting) return;
    this.setData({ starting: true });
    this.goPractice();
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/today/index" }) });
  },
});
