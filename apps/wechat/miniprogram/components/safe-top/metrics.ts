export interface SystemChromeMetrics {
  statusBarHeight: number;
  menuBottom: number;
  capsuleReserve: number;
}

export function measureSystemChrome(gap = 0): SystemChromeMetrics {
  const windowInfo = wx.getWindowInfo();
  const menu = wx.getMenuButtonBoundingClientRect();
  return {
    statusBarHeight: windowInfo.statusBarHeight,
    menuBottom: menu.bottom + gap,
    capsuleReserve: Math.max(0, windowInfo.windowWidth - menu.left + gap),
  };
}

export function safeTopHolderHeight(mode: "menu" | "status", gap: number): number {
  const metrics = measureSystemChrome(gap);
  return mode === "status" ? metrics.statusBarHeight + gap : metrics.menuBottom;
}
