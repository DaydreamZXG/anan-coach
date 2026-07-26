export interface SystemChromeMetrics {
  statusBarHeight: number;
  navigationHeight: number;
  menuTop: number;
  menuHeight: number;
  menuBottom: number;
  capsuleReserve: number;
}

export function measureSystemChrome(gap = 0): SystemChromeMetrics {
  const windowInfo = wx.getWindowInfo();
  const menu = wx.getMenuButtonBoundingClientRect();
  const menuTop = Math.max(windowInfo.statusBarHeight, menu.top);
  const menuHeight = Math.max(32, menu.height);
  const navigationHeight = Math.max(44, (menuTop - windowInfo.statusBarHeight) * 2 + menuHeight);
  return {
    statusBarHeight: windowInfo.statusBarHeight,
    navigationHeight,
    menuTop,
    menuHeight,
    menuBottom: menu.bottom + gap,
    capsuleReserve: Math.max(0, windowInfo.windowWidth - menu.left + gap),
  };
}

export function safeTopHolderHeight(mode: "menu" | "status", gap: number): number {
  const metrics = measureSystemChrome(gap);
  return mode === "status" ? metrics.statusBarHeight + gap : metrics.menuBottom;
}
