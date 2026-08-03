import { registeredNavigationTarget, routes, type AppRoute } from "./routes";

export function navigateBackOr(fallbackUrl: AppRoute = routes.today): void {
  const fallback = () => {
    const target = registeredNavigationTarget(fallbackUrl);
    if (target?.mode === "switchTab") {
      void wx.switchTab({ url: target.url });
      return;
    }
    void wx.redirectTo({ url: target?.url ?? routes.today });
  };

  if (getCurrentPages().length <= 1) {
    fallback();
    return;
  }

  wx.navigateBack({ fail: fallback });
}
