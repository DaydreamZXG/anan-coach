import {
  confirmPrimaryGoal,
  evaluatePositionEligibility,
  previewPrimaryGoal,
  searchPositions,
} from "../../api/endpoints";

type PositionVM = {
  positionId: string;
  name: string;
  department: string;
  city: string;
};

const DEMO_POSITIONS: PositionVM[] = [
  { positionId: "demo-pos1", name: "税务局一线执法岗", department: "国家税务总局某市税务局", city: "杭州" },
  { positionId: "demo-pos2", name: "海关综合业务岗", department: "某海关隶属机构", city: "宁波" },
  { positionId: "demo-pos3", name: "统计局综合管理岗", department: "国家统计局某调查总队", city: "杭州" },
];

Page({
  data: {
    // 当前目标（demo 兜底文案，接口成功后由确认流程更新）
    currentGoal: "税务局一线岗 · 2026 国考",
    keyword: "",
    searching: false,
    results: [] as PositionVM[],
    usingDemo: false,
    confirming: "",
  },

  onInput(e: WechatMiniprogram.Input) {
    this.setData({ keyword: e.detail.value });
  },

  async onSearch() {
    const kw = this.data.keyword.trim();
    if (!kw || this.data.searching) return;
    this.setData({ searching: true });
    try {
      // 搜索接口按资格条件过滤，这里把输入当城市，资格用宽松默认值
      const res = await searchPositions({
        education: "本科",
        major: "不限",
        politicalStatus: "群众",
        city: kw,
        limit: 10,
      });
      this.setData({
        searching: false,
        usingDemo: false,
        results: res.items.map((p) => ({
          positionId: p.positionId,
          name: p.name,
          department: p.departmentFull,
          city: p.city ?? "",
        })),
      });
    } catch (err) {
      console.warn("[goal] search fallback to demo", err);
      this.setData({ searching: false, usingDemo: true, results: DEMO_POSITIONS });
    }
  },

  async onPick(e: WechatMiniprogram.TouchEvent) {
    const { id, name } = e.currentTarget.dataset as { id: string; name: string };
    if (this.data.confirming) return;

    const confirmed = await new Promise<boolean>((resolve) => {
      wx.showModal({
        title: "把它设为主目标？",
        content: `之后所有任务和估分都会朝「${name}」对齐。`,
        confirmText: "就是它",
        success: (res) => resolve(!!res.confirm),
        fail: () => resolve(false),
      });
    });
    if (!confirmed) return;

    this.setData({ confirming: id });

    if (this.data.usingDemo) {
      this.setData({ currentGoal: `${name} · 2026 国考`, confirming: "", results: [] });
      wx.showToast({ title: "目标改好了（演示）", icon: "none" });
      return;
    }

    try {
      const snapshot = await evaluatePositionEligibility(id);
      const preview = await previewPrimaryGoal({
        positionId: id,
        eligibilitySnapshotId: snapshot.snapshotId,
      });
      await confirmPrimaryGoal({ previewId: preview.previewId, changeReason: "user_switch" });
      this.setData({ currentGoal: name, confirming: "", results: [] });
      wx.showToast({ title: "目标改好了，计划会跟着调", icon: "none" });
    } catch (err) {
      console.error("[goal] confirm failed", err);
      this.setData({ confirming: "" });
      wx.showToast({ title: "没改成功，再试一次", icon: "none" });
    }
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) });
  },
});
