import { fetchPlanHistory } from "../../api/endpoints";

type EntryVM = {
  planVersionId: string;
  dateLabel: string;
  headline: string;
  modeLabel: string;
  modeClass: string;
  minutes: number;
};

const MODE: Record<string, { label: string; cls: string }> = {
  normal: { label: "常规", cls: "" },
  mock: { label: "模考", cls: "is-mock" },
  recovery: { label: "恢复", cls: "is-recovery" },
  unavailable: { label: "休整", cls: "is-rest" },
};

const DEMO: EntryVM[] = [
  { planVersionId: "d1", dateLabel: "今天", headline: "资料分析错题多，加了一组基期计算专练", modeLabel: "常规", modeClass: "", minutes: 90 },
  { planVersionId: "d2", dateLabel: "昨天", headline: "你昨晚只睡了 5 小时，降载 30%，只保核心任务", modeLabel: "恢复", modeClass: "is-recovery", minutes: 60 },
  { planVersionId: "d3", dateLabel: "3 天前", headline: "周模考日，全真卷一套", modeLabel: "模考", modeClass: "is-mock", minutes: 120 },
];

Page({
  data: {
    loading: true,
    usingDemo: false,
    entries: [] as EntryVM[],
  },

  onLoad() {
    void this.load();
  },

  async load() {
    try {
      const history = await fetchPlanHistory();
      this.setData({
        loading: false,
        entries: history.items.map((e) => {
          const mode = MODE[e.dayMode] ?? { label: e.dayMode, cls: "" };
          return {
            planVersionId: e.planVersionId,
            dateLabel: e.localDate,
            headline: e.headline,
            modeLabel: mode.label,
            modeClass: mode.cls,
            minutes: e.scheduledMinutes,
          };
        }),
      });
    } catch (err) {
      console.warn("[plan-history] fallback to demo", err);
      this.setData({ loading: false, usingDemo: true, entries: DEMO });
    }
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) });
  },
});
