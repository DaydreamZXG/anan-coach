const TABS = [
  { pagePath: "/pages/today/index", text: "今日", key: "today" },
  { pagePath: "/pages/ability/index", text: "能力", key: "ability" },
  { pagePath: "/pages/anan/index", text: "岸岸", key: "anan" },
  { pagePath: "/pages/me/index", text: "我的", key: "me" },
] as const;

Component({
  data: {
    tabs: TABS as unknown as Array<{
      pagePath: string;
      text: string;
      key: string;
    }>,
    active: "today",
  },
  methods: {
    setActive(key: string) {
      this.setData({ active: key });
    },
    onTap(e: WechatMiniprogram.TouchEvent) {
      const { path, key } = e.currentTarget.dataset as {
        path: string;
        key: string;
      };
      if (key === this.data.active) return;
      wx.switchTab({ url: path });
    },
  },
});
