Page({
  data: {
    version: "0.1.0",
  },

  onCopyEmail() {
    wx.setClipboardData({
      data: "support@example.com",
      success: () => wx.showToast({ title: "邮箱已复制", icon: "none" }),
    });
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) });
  },
});
