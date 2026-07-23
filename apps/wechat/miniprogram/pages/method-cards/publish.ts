import { createMethodCardDraft } from "../../api/endpoints";
import type { Track } from "../../api/generated/types.gen";

const TRACKS: Array<{ key: Track; label: string }> = [
  { key: "xingce", label: "行测" },
  { key: "essay", label: "申论" },
];

Page({
  data: {
    tracks: TRACKS,
    track: "xingce" as Track,
    sourceText: "",
    tagsInput: "",
    submitting: false,
  },

  onTrack(e: WechatMiniprogram.TouchEvent) {
    const { track } = e.currentTarget.dataset as { track: Track };
    this.setData({ track });
  },

  onText(e: WechatMiniprogram.Input) {
    this.setData({ sourceText: e.detail.value });
  },

  onTags(e: WechatMiniprogram.Input) {
    this.setData({ tagsInput: e.detail.value });
  },

  async onSubmit() {
    const text = this.data.sourceText.trim();
    if (text.length < 10) {
      wx.showToast({ title: "再多写一点，至少 10 个字", icon: "none" });
      return;
    }
    if (this.data.submitting) return;
    this.setData({ submitting: true });

    const applicability = this.data.tagsInput
      .split(/[,，\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);

    try {
      await createMethodCardDraft({
        track: this.data.track,
        sourceType: "text",
        sourceText: text,
        applicability,
      });
      wx.showToast({ title: "已提交，AI 正在帮你整理成卡片", icon: "none" });
      setTimeout(() => wx.navigateBack(), 1500);
    } catch (err) {
      console.error("[publish] draft failed", err);
      wx.showToast({ title: "提交失败，稍后再试", icon: "none" });
    } finally {
      this.setData({ submitting: false });
    }
  },

  onBack() {
    wx.navigateBack();
  },
});
