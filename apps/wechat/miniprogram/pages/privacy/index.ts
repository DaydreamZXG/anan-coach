import { fetchPrivacyOverview, requestAccountDeletion } from "../../api/endpoints";
import { clearSession } from "../../api/auth";

const CONFIRM_PHRASE = "删除我的岸岸账户";

type CategoryVM = { category: string; countLabel: string; retention: string };

Page({
  data: {
    loading: true,
    usingDemo: false,
    policyTitle: "",
    policyVersion: "",
    rawMaterialRetention: "",
    categories: [] as CategoryVM[],
    deletionEffects: [] as string[],
    confirmPhrase: CONFIRM_PHRASE,
    phraseInput: "",
    deleting: false,
  },

  onLoad() {
    void this.load();
  },

  async load() {
    try {
      const ov = await fetchPrivacyOverview();
      this.setData({
        loading: false,
        policyTitle: ov.policyTitle,
        policyVersion: ov.policyVersion,
        rawMaterialRetention: ov.rawMaterialRetention,
        categories: ov.categories.map((c) => ({
          category: c.category,
          countLabel: `${c.itemCount} 条`,
          retention: c.retention ?? "",
        })),
        deletionEffects: ov.accountDeletionEffect,
      });
    } catch (err) {
      console.warn("[privacy] fallback to demo", err);
      this.setData({
        loading: false,
        usingDemo: true,
        policyTitle: "上岸计划隐私政策",
        policyVersion: "v1.0",
        rawMaterialRetention: "上传的原始材料仅用于生成学习数据，处理后 30 天内删除",
        categories: [
          { category: "账户与目标信息", countLabel: "12 条", retention: "注销后即时删除" },
          { category: "练习与模考记录", countLabel: "340 条", retention: "注销后即时删除" },
          { category: "对话记录", countLabel: "56 条", retention: "注销后即时删除" },
        ],
        deletionEffects: [
          "所有学习记录、计划、对话立即不可恢复",
          "社区已发布的方法卡会匿名化保留",
          "会员剩余时长不予退还",
        ],
      });
    }
  },

  onOpenPolicy() {
    wx.openPrivacyContract({
      fail: () => wx.showToast({ title: "隐私协议暂未配置", icon: "none" }),
    });
  },

  onPhraseInput(e: WechatMiniprogram.Input) {
    this.setData({ phraseInput: e.detail.value });
  },

  async onDelete() {
    if (this.data.deleting) return;
    if (this.data.phraseInput.trim() !== CONFIRM_PHRASE) {
      wx.showToast({ title: `请原样输入「${CONFIRM_PHRASE}」`, icon: "none" });
      return;
    }

    const confirmed = await new Promise<boolean>((resolve) => {
      wx.showModal({
        title: "最后确认",
        content: "注销后所有数据立即删除且不可恢复。确定继续吗？",
        confirmText: "永久删除",
        confirmColor: "#f0524c",
        success: (res) => resolve(!!res.confirm),
        fail: () => resolve(false),
      });
    });
    if (!confirmed) return;

    if (this.data.usingDemo) {
      wx.showToast({ title: "已提交注销（演示）", icon: "none" });
      return;
    }

    this.setData({ deleting: true });
    try {
      const key = `del-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
      await requestAccountDeletion(CONFIRM_PHRASE, key);
      clearSession();
      wx.showToast({ title: "账户已注销", icon: "none" });
      setTimeout(() => wx.reLaunch({ url: "/pages/onboarding/index" }), 1200);
    } catch (err) {
      console.error("[privacy] deletion failed", err);
      this.setData({ deleting: false });
      wx.showToast({ title: "没提交成功，再试一次", icon: "none" });
    }
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) });
  },
});
