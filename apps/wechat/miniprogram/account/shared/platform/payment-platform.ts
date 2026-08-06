/**
 * 微信客户端平台与虚拟支付能力判定。
 *
 * “设备平台”来自 wx.getDeviceInfo().platform（ios / android / windows / mac /
 * devtools / ohos / harmony）。同一设备平台不同客户端/版本，能力差异很大：
 * 因此把“设备平台”与“客户端能力”分开判定，避免把能力差异硬编码进平台分支。
 */
export const WECHAT_CLIENT_PLATFORM = ["ios", "android", "harmony", "windows"] as const;
export type WechatClientPlatform = (typeof WECHAT_CLIENT_PLATFORM)[number];

/** 虚拟支付 API 是否存在（旧客户端/某些平台分支不提供）。 */
export function supportsVirtualPayment(): boolean {
  return typeof wx.canIUse === "function" && wx.canIUse("requestVirtualPayment");
}

/**
 * 微信虚拟支付在当前客户端可用的平台枚举。与后端协商的取值空间固定为
 * ios/android/harmony/windows：真机设备按 wx.getDeviceInfo().platform 映射；
 * 桌面 mac/devtools 等其余平台不在微信虚拟支付的支付域内，返回 null。
 */
export function resolveWechatClientPlatform(): WechatClientPlatform | null {
  if (typeof wx.getDeviceInfo !== "function") return null;
  const platform = wx.getDeviceInfo().platform.toLowerCase();
  if (platform === "ios") return "ios";
  if (platform === "android") return "android";
  if (platform === "windows") return "windows";
  if (platform === "ohos" || platform === "harmony") return "harmony";
  return null;
}
