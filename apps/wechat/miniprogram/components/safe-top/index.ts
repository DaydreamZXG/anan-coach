/**
 * 顶部安全区占位：内容从系统胶囊按钮下方开始。
 * 规范算法：占位高度 = 胶囊 bottom + 呼吸间距。
 * 兜底：胶囊信息拿不到时用 statusBarHeight + 44(导航栏标准高) + 间距。
 */
Component({
  properties: {
    /** 额外的呼吸间距（px），默认 8 */
    gap: { type: Number, value: 8 },
  },

  data: {
    holderHeight: 88,
  },

  lifetimes: {
    attached() {
      let height = 0;
      try {
        const menu = wx.getMenuButtonBoundingClientRect();
        if (menu && menu.bottom > 0) {
          height = menu.bottom + this.data.gap;
        }
      } catch (_) {
        // 忽略，走兜底
      }
      if (height <= 0) {
        const win = wx.getWindowInfo();
        height = (win.statusBarHeight || 20) + 44 + this.data.gap;
      }
      this.setData({ holderHeight: height });
    },
  },
});
