import {
  confirmPrimaryGoal,
  evaluatePositionEligibility,
  fetchOnboardingProgress,
  previewPrimaryGoal,
  searchPositions,
} from "../../../api/endpoints";
import type { PositionSearchQuery } from "../../../api/endpoints";
import type {
  OnboardingProgress,
  PositionEligibilitySnapshot,
  PositionSummary,
} from "../../../api/generated/types.gen";

/** 资格资料页写入本地存储的资格信息（结构保持一致） */
interface QualificationInfo {
  education: string;
  major: string;
  politicalStatus: string;
  freshGraduate: boolean;
  grassrootsYears: number;
}

interface PositionItemView {
  positionId: string;
  name: string;
  departmentFull: string;
  regionText: string;
  headcountText: string;
  entryScoreText: string;
}

interface PreviewView {
  previewId: string;
  positionName: string;
  recommended: string;
  rangeText: string;
  evidenceText: string;
  sampleText: string;
}

/** 证据等级映射 */
const EVIDENCE_TEXT: Record<string, string> = {
  direct: "直接证据",
  strong: "证据较强",
  limited: "证据有限",
  unavailable: "暂无证据",
};

function toItemView(p: PositionSummary): PositionItemView {
  // 有近年进面分（平均分/最低分）才展示，没有就不显示该行
  const score = p.entryScore?.averageEntryScore ?? p.entryScore?.latestEntryScore ?? null;
  return {
    positionId: p.positionId,
    name: p.name,
    departmentFull: p.departmentFull,
    regionText: p.city ? `${p.region} · ${p.city}` : p.region,
    headcountText: `招录 ${p.headcount} 人`,
    entryScoreText: score === null ? "" : `进面分约 ${Math.round(score)}`,
  };
}

Page({
  data: {
    statusBarHeight: 20,
    steps: [1, 2, 3, 4, 5, 6],
    step: 3,
    region: "",
    searching: false,
    searched: false,
    items: [] as PositionItemView[],
    pickingId: "",
    preview: null as PreviewView | null,
    confirming: false,
  },

  /** 资格资料（来自上一步的本地存储） */
  qualification: null as QualificationInfo | null,
  /** 引导进度（本页写接口不带版本号，仅按规范预拉取） */
  progress: null as OnboardingProgress | null,

  async onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });

    const stored = wx.getStorageSync("anan.qualification") as QualificationInfo | "";
    if (!stored || !stored.education || !stored.major) {
      wx.showToast({ title: "请先完成资格资料", icon: "none" });
      wx.redirectTo({ url: "/pages/onboarding/qualification/index" });
      return;
    }
    this.qualification = stored;

    try {
      this.progress = await fetchOnboardingProgress();
    } catch {
      wx.showToast({ title: "网络不太稳定，稍后再试", icon: "none" });
    }
  },

  onRegionInput(e: WechatMiniprogram.Input) {
    this.setData({ region: e.detail.value });
  },

  async onSearch() {
    const q = this.qualification;
    if (!q || this.data.searching) return;
    const query: PositionSearchQuery = {
      education: q.education,
      major: q.major,
      politicalStatus: q.politicalStatus,
      grassrootsYears: q.grassrootsYears,
      freshGraduate: q.freshGraduate,
      limit: 30,
    };
    const region = this.data.region.trim();
    if (region) query.region = region;

    this.setData({ searching: true, searched: false, items: [], preview: null });
    try {
      const res = await searchPositions(query);
      this.setData({ items: (res.items ?? []).map(toItemView), searched: true });
    } catch {
      wx.showToast({ title: "网络不太稳定，稍后再试", icon: "none" });
    } finally {
      this.setData({ searching: false });
    }
  },

  async onPick(e: WechatMiniprogram.TouchEvent) {
    const positionId = e.currentTarget.dataset.id as string;
    const item = this.data.items.find((it) => it.positionId === positionId);
    if (!item || this.data.pickingId !== "" || this.data.confirming) return;

    this.setData({ pickingId: positionId, preview: null });

    // 先评估资格拿到快照，再取目标分预览
    let snapshot: PositionEligibilitySnapshot;
    try {
      snapshot = await evaluatePositionEligibility(positionId);
    } catch {
      wx.showToast({ title: "网络不太稳定，稍后再试", icon: "none" });
      this.setData({ pickingId: "" });
      return;
    }

    try {
      const preview = await previewPrimaryGoal({
        positionId,
        eligibilitySnapshotId: snapshot.snapshotId,
      });
      const t = preview.targetScore;
      this.setData({
        preview: {
          previewId: preview.previewId,
          positionName: item.name,
          recommended: `${Math.round(t.recommended)}`,
          rangeText: `${Math.round(t.lowerBound)} – ${Math.round(t.upperBound)}`,
          evidenceText: EVIDENCE_TEXT[t.evidenceLevel] ?? "暂无证据",
          sampleText: `样本 ${t.sampleCount} 个同类岗位`,
        },
      });
    } catch {
      wx.showToast({ title: "这个岗位暂时没有分数依据，换一个试试", icon: "none" });
    } finally {
      this.setData({ pickingId: "" });
    }
  },

  async onConfirm() {
    const preview = this.data.preview;
    if (!preview || this.data.confirming) return;
    this.setData({ confirming: true });
    try {
      await confirmPrimaryGoal({ previewId: preview.previewId, changeReason: "初次设定主目标" });
      wx.showToast({ title: "目标已设定", icon: "success" });
      wx.redirectTo({ url: "/pages/onboarding/preference/index" });
    } catch {
      wx.showToast({ title: "网络不太稳定，稍后再试", icon: "none" });
      this.setData({ confirming: false });
    }
  },
});
