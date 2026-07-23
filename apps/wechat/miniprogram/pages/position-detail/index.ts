import {
  confirmPrimaryGoal,
  evaluatePositionEligibility,
  fetchPositionDetail,
  previewPrimaryGoal,
} from "../../api/endpoints";

type Row = { label: string; value: string };

const DEMO = {
  name: "税务局一线执法岗（演示）",
  departmentFull: "国家税务总局某市税务局",
  region: "浙江 · 杭州",
  headcount: 3,
  targetScore: "132",
  rows: [
    { label: "学历要求", value: "本科及以上" },
    { label: "专业要求", value: "财政学类、经济学类" },
    { label: "政治面貌", value: "不限" },
    { label: "户籍要求", value: "不限" },
    { label: "基层年限", value: "不限" },
    { label: "试卷类型", value: "行政执法类" },
    { label: "加试", value: "无" },
  ] as Row[],
  entryLine: "近年入围线约 128 ~ 135，平均 131.6（3 年数据）",
};

Page({
  data: {
    loading: true,
    usingDemo: false,
    positionId: "",
    name: "",
    departmentFull: "",
    region: "",
    headcount: 0,
    targetScore: "",
    rows: [] as Row[],
    entryLine: "",
    remark: "",
    confirming: false,
  },

  onLoad(query: Record<string, string>) {
    const positionId = query.positionId || "";
    this.setData({ positionId });
    void this.load(positionId);
  },

  async load(positionId: string) {
    if (!positionId) {
      this.setData({ loading: false, usingDemo: true, ...DEMO });
      return;
    }
    try {
      const d = await fetchPositionDetail(positionId);
      const es = d.entryScore;
      const entryParts: string[] = [];
      if (typeof es.latestEntryScore === "number") entryParts.push(`最近入围线 ${es.latestEntryScore}`);
      if (typeof es.averageEntryScore === "number") entryParts.push(`平均 ${es.averageEntryScore}`);
      this.setData({
        loading: false,
        name: d.name,
        departmentFull: d.departmentFull,
        region: `${d.region}${d.city ? " · " + d.city : ""}`,
        headcount: d.headcount,
        targetScore: typeof es.targetScore === "number" ? String(es.targetScore) : "",
        rows: [
          { label: "学历要求", value: d.educationMin || "不限" },
          { label: "专业要求", value: d.majorRequired || "不限" },
          { label: "政治面貌", value: d.politicalReq || "不限" },
          { label: "户籍要求", value: d.hukouReq || "不限" },
          { label: "基层年限", value: d.serviceMinYrs ? `${d.serviceMinYrs} 年及以上` : "不限" },
          { label: "试卷类型", value: d.paperType || "未标注" },
          { label: "加试", value: d.hasExtraExam ? "有" : "无" },
        ],
        entryLine: entryParts.length
          ? `${entryParts.join("，")}（${es.sourceCount} 年数据）`
          : "暂无历年入围数据",
        remark: d.remark || "",
      });
    } catch (err) {
      console.warn("[position-detail] fallback to demo", err);
      this.setData({ loading: false, usingDemo: true, ...DEMO });
    }
  },

  async onSetGoal() {
    if (this.data.confirming) return;

    const confirmed = await new Promise<boolean>((resolve) => {
      wx.showModal({
        title: "把它设为主目标？",
        content: `之后所有任务和估分都会朝「${this.data.name}」对齐。`,
        confirmText: "就是它",
        success: (res) => resolve(!!res.confirm),
        fail: () => resolve(false),
      });
    });
    if (!confirmed) return;

    if (this.data.usingDemo) {
      wx.showToast({ title: "目标改好了（演示）", icon: "none" });
      return;
    }

    this.setData({ confirming: true });
    try {
      const snapshot = await evaluatePositionEligibility(this.data.positionId);
      const preview = await previewPrimaryGoal({
        positionId: this.data.positionId,
        eligibilitySnapshotId: snapshot.snapshotId,
      });
      await confirmPrimaryGoal({ previewId: preview.previewId, changeReason: "user_switch" });
      wx.showToast({ title: "目标改好了，计划会跟着调", icon: "none" });
      setTimeout(() => wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) }), 1200);
    } catch (err) {
      console.error("[position-detail] confirm failed", err);
      this.setData({ confirming: false });
      wx.showToast({ title: "没改成功，再试一次", icon: "none" });
    }
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) });
  },
});
