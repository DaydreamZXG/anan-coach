Component({
  properties: {
    title: { type: String, value: '' },
    showBack: { type: Boolean, value: true }
  },

  data: {
    paddingTop: 20,
    navHeight: 44,
  },

  lifetimes: {
    attached() {
      let paddingTop = 20;
      let navHeight = 44;
      try {
        const win = wx.getWindowInfo();
        paddingTop = win.statusBarHeight || 20;
        const menu = wx.getMenuButtonBoundingClientRect();
        if (menu.top > 0) {
          navHeight = (menu.top - paddingTop) * 2 + menu.height;
        }
      } catch {
        // Fallback
      }
      this.setData({ paddingTop, navHeight });
    }
  },

  methods: {
    goBack() {
      wx.navigateBack({
        fail() {
          void wx.switchTab({ url: '/pages/today/index' });
        }
      });
    }
  }
});
