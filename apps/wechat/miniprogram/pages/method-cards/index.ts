import { discoverMethodCards } from "../../api/endpoints";
import type { DiscoveryCard, Track } from "../../api/generated/types.gen";

const TRACKS: Array<{ key: Track; label: string }> = [
  { key: "xingce", label: "行测" },
  { key: "essay", label: "申论" },
];

const DEMO_CARDS: DiscoveryCard[] = [
  {
    title: "资料分析：增长率速算三步法",
    recognition: "题干出现「同比增长」且给出现期与基期值时适用",
    methodSteps: [
      "先判断是求增长量还是增长率，别混",
      "用「现期÷(1+增长率)」估基期，保留两位有效数字",
      "选项差距大时直接首数法，差距小才精算",
    ],
    pitfalls: ["百分点与百分比混用", "基期现期方向搞反"],
    applicability: ["资料分析", "增长率"],
    exclusions: [],
    riskFlags: [],
    evidenceCount: 42,
    conservativeScore: 0.78,
    cardVersionId: "demo-1",
    evidencePolicyVersion: "v1",
    isAuthor: false,
  },
  {
    title: "言语理解：转折关联词定位法",
    recognition: "题干含「但是/然而/其实」等转折词，重点在转折后",
    methodSteps: [
      "圈出转折词，答案方向锁定其后半句",
      "排除与转折后语义相反的选项",
    ],
    pitfalls: ["被转折前的干扰信息带偏"],
    applicability: ["言语理解", "逻辑填空"],
    exclusions: [],
    riskFlags: ["样本偏少，谨慎参考"],
    evidenceCount: 8,
    conservativeScore: 0.54,
    cardVersionId: "demo-2",
    evidencePolicyVersion: "v1",
    isAuthor: false,
  },
];

Page({
  data: {
    tracks: TRACKS,
    activeTrack: "xingce" as Track,
    loading: true,
    usingDemo: false,
    cards: [] as Array<DiscoveryCard & { expanded: boolean; scorePct: number; shaky: boolean }>,
  },

  onLoad() {
    this.load();
  },

  onSwitchTrack(e: WechatMiniprogram.TouchEvent) {
    const { track } = e.currentTarget.dataset as { track: Track };
    if (track === this.data.activeTrack) return;
    this.setData({ activeTrack: track });
    this.load();
  },

  async load() {
    this.setData({ loading: true });
    try {
      const res = await discoverMethodCards({
        track: this.data.activeTrack,
        applicability: [],
      });
      this.render(res.cards || [], false);
    } catch (err) {
      console.error("[method-cards] discovery failed, fallback to demo", err);
      this.render(DEMO_CARDS, true);
    }
  },

  render(cards: DiscoveryCard[], usingDemo: boolean) {
    const view = cards.map((c) => ({
      ...c,
      expanded: false,
      scorePct: c.conservativeScore === null ? 0 : Math.round(c.conservativeScore * 100),
      shaky: c.evidenceCount < 20,
    }));
    this.setData({ loading: false, usingDemo, cards: view });
  },

  onToggle(e: WechatMiniprogram.TouchEvent) {
    const { idx } = e.currentTarget.dataset as { idx: number };
    const card = this.data.cards[idx];
    if (!card) return;
    this.setData({ [`cards[${idx}].expanded`]: !card.expanded });
  },

  onSave(e: WechatMiniprogram.TouchEvent) {
    e.stopPropagation?.();
    wx.showToast({ title: "已收藏，计划会用到它", icon: "none" });
  },

  onPublish() {
    wx.navigateTo({
      url: "/pages/method-cards/publish",
      fail: () => wx.showToast({ title: "发布页开发中", icon: "none" }),
    });
  },
});
