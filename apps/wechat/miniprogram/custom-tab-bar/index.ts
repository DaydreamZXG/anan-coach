const TABS = [
  {
    pagePath: "/pages/today/index",
    text: "今日",
    key: "today",
    icon: "/assets/tabbar/today.png",
    activeIcon: "/assets/tabbar/today-active.png",
  },
  {
    pagePath: "/pages/ability/index",
    text: "能力",
    key: "ability",
    icon: "/assets/tabbar/ability.png",
    activeIcon: "/assets/tabbar/ability-active.png",
  },
  {
    pagePath: "/pages/anan/index",
    text: "岸岸",
    key: "anan",
    icon: "/assets/tabbar/anan.png",
    activeIcon: "/assets/tabbar/anan-active.png",
  },
  {
    pagePath: "/pages/me/index",
    text: "我的",
    key: "me",
    icon: "/assets/tabbar/me.png",
    activeIcon: "/assets/tabbar/me-active.png",
  },
] as const;

Component({
  data: {
    tabs: TABS as unknown as Array<{
      pagePath: string;
      text: string;
      key: string;
      icon: string;
      activeIcon: string;
    }>,
    active: "today",
    hidden: false,
  },
  methods: {
    setActive(key: string) {
      this.setData({ active: key });
    },
    setHidden(hidden: boolean) {
      this.setData({ hidden });
    },
    onTap(e: WechatMiniprogram.TouchEvent) {
      const { path, key } = e.currentTarget.dataset as {
        path: string;
        key: string;
      };
      if (key === this.data.active) return;
      void wx.switchTab({ url: path });
    },
  },
});
