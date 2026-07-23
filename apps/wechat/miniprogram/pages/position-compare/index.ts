import { comparePositions } from "../../api/endpoints";

type CompareVM = {
  positionId: string;
  name: string;
  department: string;
  region: string;
  headcount: number;
  targetScoreLabel: string;
  eligibilityLabel: string;
  eligibilityClass: string;
  metLine: string;
  isPrimary: boolean;
  tags: string[];
};

const ELIGIBILITY: Record<string, { label: string; cls: string }> = {
  eligible: { label: "条件符合", cls: "is-ok" },
  needs_confirmation: { label: "待确认", cls: "is-warn" },
  ineligible: { label: "不符合", cls: "is-bad" },
};

const DEMO: CompareVM[] = [
  {
    positionId: "d1", name: "税务局一线执法岗", department: "某市税务局", region: "杭州",
    headcount: 3, targetScoreLabel: "目标 132", eligibilityLabel: "条件符合", eligibilityClass: "is-ok",
    metLine: "达标 8 / 待确认 0 / 不符 0", isPrimary: true, tags: ["当前目标", "目标分最低"],
  },
  {
    positionId: "d2", name: "海关综合业务岗", department: "某海关隶属机构", region: "宁波",
    headcount: 6, targetScoreLabel: "目标 138", eligibilityLabel: "待确认", eligibilityClass: "is-warn",
    metLine: "达标 6 / 待确认 2 / 不符 0", isPrimary: false, tags: ["招录最多"],
  },
];

Page({
  data: {
    loading: true,
    usingDemo: false,
    items: [] as CompareVM[],
  },

  onLoad(query: Record<string, string>) {
    const ids = (query.ids || "").split(",").filter(Boolean);
    void this.load(ids);
  },

  async load(ids: string[]) {
    if (ids.length < 2) {
      this.setData({ loading: false, usingDemo: true, items: DEMO });
      return;
    }
    try {
      const res = await comparePositions({ positionIds: ids });
      const h = res.highlights;
      this.setData({
        loading: false,
        items: res.items.map((it) => {
          const eg = ELIGIBILITY[it.eligibilityStatus] ?? { label: it.eligibilityStatus, cls: "" };
          const tags: string[] = [];
          if (it.isPrimaryGoal) tags.push("当前目标");
          if (h.lowestTargetScorePositionIds.includes(it.positionId)) tags.push("目标分最低");
          if (h.largestHeadcountPositionIds.includes(it.positionId)) tags.push("招录最多");
          if (h.confirmedEligiblePositionIds.includes(it.positionId)) tags.push("资格已确认");
          return {
            positionId: it.positionId,
            name: it.name,
            department: it.departmentFull,
            region: it.region,
            headcount: it.headcount,
            targetScoreLabel: it.targetScore ? `目标 ${it.targetScore.recommended}` : "目标分待算",
            eligibilityLabel: eg.label,
            eligibilityClass: eg.cls,
            metLine: `达标 ${it.metCount} / 待确认 ${it.pendingCount} / 不符 ${it.notMetCount}`,
            isPrimary: it.isPrimaryGoal,
            tags,
          };
        }),
      });
    } catch (err) {
      console.warn("[position-compare] fallback to demo", err);
      this.setData({ loading: false, usingDemo: true, items: DEMO });
    }
  },

  onOpenDetail(e: WechatMiniprogram.TouchEvent) {
    const { id } = e.currentTarget.dataset as { id: string };
    wx.navigateTo({
      url: `/pages/position-detail/index?positionId=${id}`,
      fail: () => wx.showToast({ title: "跳转失败，再点一次", icon: "none" }),
    });
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) });
  },
});
