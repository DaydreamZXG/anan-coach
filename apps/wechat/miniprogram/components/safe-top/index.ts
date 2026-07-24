/**
 * 自定义导航页的顶部安全区。
 * menu：正文从系统胶囊下方开始。
 * status：标题可以与胶囊同排，仅避开系统状态栏。
 */
Component({
  properties: {
    gap: { type: Number, value: 8 },
    mode: { type: String, value: "menu" },
  },

  data: {
    holderHeight: 0,
  },

  lifetimes: {
    attached() {
      const height =
        this.data.mode === "status"
          ? wx.getWindowInfo().statusBarHeight + this.data.gap
          : wx.getMenuButtonBoundingClientRect().bottom + this.data.gap;
      this.setData({ holderHeight: height });
    },
  },
});
