export interface SystemChromeMetrics {
  statusBarHeight: number;
  navigationHeight: number;
  menuTop: number;
  menuHeight: number;
  menuBottom: number;
  capsuleReserve: number;
}

export function measureSystemChrome(gap = 0): SystemChromeMetrics {
  const legacyGetSystemInfoSync = (
    wx as unknown as {
      getSystemInfoSync?: () => { statusBarHeight?: number; windowWidth?: number };
    }
  ).getSystemInfoSync;
  const windowInfo =
    typeof wx.getWindowInfo === "function"
      ? wx.getWindowInfo()
      : typeof legacyGetSystemInfoSync === "function"
        ? legacyGetSystemInfoSync()
        : { statusBarHeight: 20, windowWidth: 375 };
  const statusBarHeight = finiteMetric(windowInfo.statusBarHeight, 20);
  const windowWidth = finiteMetric(windowInfo.windowWidth, 375);
  const menu =
    typeof wx.getMenuButtonBoundingClientRect === "function"
      ? wx.getMenuButtonBoundingClientRect()
      : {
          top: statusBarHeight + 6,
          height: 32,
          bottom: statusBarHeight + 38,
          left: windowWidth - 96,
        };
  const menuTop = Math.max(statusBarHeight, finiteMetric(menu.top, statusBarHeight + 6));
  const menuHeight = Math.max(32, menu.height);
  const navigationHeight = Math.max(44, (menuTop - statusBarHeight) * 2 + menuHeight);
  return {
    statusBarHeight,
    navigationHeight,
    menuTop,
    menuHeight,
    menuBottom: finiteMetric(menu.bottom, menuTop + menuHeight) + gap,
    capsuleReserve: Math.max(0, windowWidth - finiteMetric(menu.left, windowWidth - 96) + gap),
  };
}

function finiteMetric(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function safeTopHolderHeight(mode: "menu" | "status", gap: number): number {
  const metrics = measureSystemChrome(gap);
  return mode === "status" ? metrics.statusBarHeight + gap : metrics.menuBottom;
}
