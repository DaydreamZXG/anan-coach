/** Keep the native tab bar suppressed while the product-owned floating tab bar is active. */
export function suppressNativeTabBar(): void {
  if (typeof wx.hideTabBar !== "function") return;
  void wx.hideTabBar({ animation: false }).catch(() => undefined);
}
