import { fetchMasteryQueue } from "../../api/endpoints";

interface Entry {
  id: "photo" | "record" | "review" | "essay";
  glyph: string;
  name: string;
  desc: string;
  tone: "cyan" | "violet" | "amber" | "green";
  badge?: string;
}

Page({
  data: {
    statusBarHeight: 20,
    entries: [
      { id: "photo", glyph: "拍", name: "拍题导入", desc: "纸题拍照，AI 识别入库", tone: "cyan" },
      {
        id: "record",
        glyph: "录",
        name: "录入答案",
        desc: "纸质做完，来这里对答案",
        tone: "violet",
      },
      { id: "review", glyph: "复", name: "待复盘", desc: "做完的题，等你说说思路", tone: "amber" },
      { id: "essay", glyph: "论", name: "申论练习", desc: "拍照上传，区间评分", tone: "green" },
    ] as Entry[],
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
  },

  onShow() {
    this.getTabBar()?.setData({ selected: 1 });
    this.refreshReviewBadge();
  },

  /** 待复盘角标：取掌握队列汇总，失败静默（未登录/演示态） */
  async refreshReviewBadge() {
    try {
      const page = await fetchMasteryQueue("pending_reflection");
      const n = page.summary.pendingReflectionCount;
      const badge = n > 0 ? `${n}` : "";
      const entries = this.data.entries.map((it) =>
        it.id === "review"
          ? { ...it, badge, desc: n > 0 ? `${n} 组错题等你说说思路` : "做完的题，等你说说思路" }
          : it,
      );
      this.setData({ entries });
    } catch {
      // 未登录或网络异常：保持默认文案
    }
  },

  /** 主入口：智能组卷由计划引擎按日生成，落在今日页主线 */
  onCompose() {
    wx.switchTab({ url: "/pages/today/index" });
  },

  onEntry(e: WechatMiniprogram.TouchEvent) {
    const id = e.currentTarget.dataset.id as Entry["id"];
    if (id === "review") {
      wx.navigateTo({ url: "/pages/review/index" });
      return;
    }
    const tips: Record<Exclude<Entry["id"], "review">, string> = {
      photo: "拍题识别正在接入，稍后再来",
      record: "答案录入正在接入，稍后再来",
      essay: "申论闭环正在接入，稍后再来",
    };
    wx.showToast({ title: tips[id], icon: "none" });
  },
});
