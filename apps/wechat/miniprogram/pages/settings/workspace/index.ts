import { authedV2 } from "../../../api/endpoints";
import type {
  PositionWorkspaceItem,
  PositionWorkspaceResponse,
} from "../../../api/generated/types.gen";

/** 列表项视图模型（主目标与备选共用） */
interface WorkspaceItemView {
  positionId: string;
  name: string;
  department: string;
  regionText: string;
  scoreText: string;
  evidenceText: string;
}

/** 证据等级文案 */
const EVIDENCE_TEXT: Record<string, string> = {
  direct: "直接证据",
  strong: "强证据",
  limited: "证据有限",
  unavailable: "暂无证据",
};

function toView(item: PositionWorkspaceItem): WorkspaceItemView {
  const target = item.targetScore;
  return {
    positionId: item.positionId,
    name: item.name,
    department: item.departmentFull,
    regionText: item.city ? `${item.region} · ${item.city}` : item.region,
    scoreText: target
      ? `${target.lowerBound} - ${target.upperBound} 分，建议 ${target.recommended} 分`
      : "目标分暂未生成",
    evidenceText: target ? (EVIDENCE_TEXT[target.evidenceLevel] ?? "暂无证据") : "",
  };
}

Page({
  data: {
    statusBarHeight: 20,
    viewState: "loading" as "loading" | "error" | "ready" | "empty",
    examYear: 0,
    primary: null as WorkspaceItemView | null,
    alternates: [] as WorkspaceItemView[],
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    void this.load();
  },

  onBack() {
    wx.navigateBack({
      fail: () => {
        wx.switchTab({ url: "/pages/profile/index" });
      },
    });
  },

  onRetry() {
    void this.load();
  },

  onGoPosition() {
    wx.navigateTo({ url: "/pages/onboarding/position/index" });
  },

  async load() {
    this.setData({ viewState: "loading" });
    try {
      const resp = await authedV2<PositionWorkspaceResponse>({
        method: "GET",
        path: "/api/v2/positions/workspace",
      });
      const primaryItem = resp.items.find((item) => item.isPrimaryGoal) ?? null;
      if (!primaryItem) {
        this.setData({ viewState: "empty", primary: null, alternates: [] });
        return;
      }
      this.setData({
        viewState: "ready",
        examYear: resp.examYear,
        primary: toView(primaryItem),
        alternates: resp.items.filter((item) => !item.isPrimaryGoal).map(toView),
      });
    } catch {
      this.setData({ viewState: "error" });
    }
  },
});
