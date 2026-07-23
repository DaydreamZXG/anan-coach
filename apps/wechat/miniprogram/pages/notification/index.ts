type Settings = {
  dailyReminder: boolean;
  settlementReminder: boolean;
  mockReminder: boolean;
};

const DEFAULTS: Settings = {
  dailyReminder: true,
  settlementReminder: true,
  mockReminder: true,
};

const STORAGE_KEY = "notification_settings";

Page({
  data: {
    ...DEFAULTS,
  },

  onLoad() {
    const saved = wx.getStorageSync(STORAGE_KEY) as Settings | "";
    if (saved && typeof saved === "object") {
      this.setData({ ...DEFAULTS, ...saved });
    }
  },

  onToggle(e: WechatMiniprogram.SwitchChange) {
    const { key } = e.currentTarget.dataset as { key: keyof Settings };
    const next = { ...this.getSettings(), [key]: e.detail.value };
    this.setData(next);
    wx.setStorageSync(STORAGE_KEY, next);
  },

  getSettings(): Settings {
    return {
      dailyReminder: this.data.dailyReminder,
      settlementReminder: this.data.settlementReminder,
      mockReminder: this.data.mockReminder,
    };
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) });
  },
});
