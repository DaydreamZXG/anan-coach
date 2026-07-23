/**
 * 环境配置：集中管理，切换环境只改这里。
 */
export const config = {
  /** 后端 API 根地址（生产） */
  apiBaseUrl: "https://kg.xingguoxian.cn",
  /**
   * 接口不可达时是否回退到演示数据。
   * 仅用于设计走查与开发调试；演示数据在界面上必须明确标注。
   */
  enableDemoFallback: true,
  /** 本地存储键 */
  storageKeys: {
    session: "anan.session",
    deviceId: "anan.deviceId",
  },
} as const;
