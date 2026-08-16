export interface NavigationFeedbackTarget {
  mode: "navigateTo" | "switchTab";
  url: string;
}

const destinationPaintDelayMs = 450;

/**
 * Keep feedback on the native layer while WeChat loads a page or subpackage.
 * The destination page can render its own skeleton without leaving a blank gap.
 */
export async function navigateWithImmediateFeedback(
  target: NavigationFeedbackTarget,
  title = "正在打开",
): Promise<void> {
  if (typeof wx.showLoading === "function") {
    void wx.showLoading({ title, mask: true });
  }
  try {
    if (target.mode === "switchTab") await wx.switchTab({ url: target.url });
    else await wx.navigateTo({ url: target.url });
  } finally {
    setTimeout(() => {
      if (typeof wx.hideLoading === "function") void wx.hideLoading();
    }, destinationPaintDelayMs);
  }
}
