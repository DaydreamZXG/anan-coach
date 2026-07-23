import { installUnauthorizedReset } from "./api/auth";

App({
  globalData: {
    statusBarHeight: 20,
    navBarHeight: 44,
    /** 右侧需为系统胶囊按钮预留的宽度（px），顶部靠右内容用它做 margin-right */
    capsuleInset: 96,
  },

  onLaunch() {
    installUnauthorizedReset();

    const windowInfo = wx.getWindowInfo();
    this.globalData.statusBarHeight = windowInfo.statusBarHeight;

    // 系统胶囊（右上角 …⊙）占据的宽度，顶部布局避让
    const capsule = wx.getMenuButtonBoundingClientRect();
    this.globalData.capsuleInset = windowInfo.windowWidth - capsule.left + 8;

    // 预留：MiSans 子集字体通过 wx.loadFontFace 加载（待 CDN 域名白名单就绪）
  },
});
