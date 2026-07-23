/** 天气归属：web-view 展示 Apple Weather 官方归属链接，仅放行已知域名 */
Page({
  data: {
    url: "",
    valid: false,
  },

  onLoad(query: Record<string, string | undefined>) {
    let candidate: string;
    try {
      candidate = decodeURIComponent(query.url ?? "");
    } catch {
      this.setData({ url: "", valid: false });
      return;
    }
    const valid =
      candidate.startsWith("https://weatherkit.apple.com/") ||
      candidate.startsWith("https://weather-data.apple.com/") ||
      candidate.startsWith("https://www.apple.com/legal/");
    this.setData({ url: valid ? candidate : "", valid });
  },
});
