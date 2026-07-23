Component({
  data: {
    selected: 0,
    list: [
      { path: "/pages/today/index", text: "今日", glyph: "今" },
      { path: "/pages/training/index", text: "训练", glyph: "练" },
      { path: "/pages/ability/index", text: "能力", glyph: "能" },
      { path: "/pages/profile/index", text: "我的", glyph: "我" },
    ],
  },

  methods: {
    onSwitch(e: WechatMiniprogram.TouchEvent) {
      const index = Number(e.currentTarget.dataset.index);
      const item = this.data.list[index];
      if (!item || index === this.data.selected) return;
      wx.switchTab({ url: item.path });
    },
  },
});
