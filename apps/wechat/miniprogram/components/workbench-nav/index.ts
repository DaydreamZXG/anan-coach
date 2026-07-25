import { measureSystemChrome } from "../safe-top/metrics";

Component({
  properties: {
    title: { type: String, value: "" },
    showBack: { type: Boolean, value: true },
    backBehavior: { type: String, value: "navigate" },
    tone: { type: String, value: "light" },
  },

  data: {
    paddingTop: 20,
    navHeight: 44,
    capsuleReserve: 96,
  },

  lifetimes: {
    attached() {
      const metrics = measureSystemChrome(8);
      const menu = wx.getMenuButtonBoundingClientRect();
      const navHeight =
        menu.top > 0 ? Math.max(44, (menu.top - metrics.statusBarHeight) * 2 + menu.height) : 44;
      this.setData({
        paddingTop: metrics.statusBarHeight,
        navHeight,
        capsuleReserve: Math.max(48, metrics.capsuleReserve),
      });
    },
  },

  methods: {
    goBack() {
      if (this.data.backBehavior === "emit") {
        this.triggerEvent("back");
        return;
      }
      wx.navigateBack({
        fail() {
          void wx.switchTab({ url: "/pages/today/index" });
        },
      });
    },
  },
});
