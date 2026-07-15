# 岸岸角色系统 v1.0

本目录是“岸岸”角色定义、发布证据和主产品接入边界的权威入口。岗位、解题、规划和学习大脑业务逻辑仍由各自领域负责；业务页面只提交语义化视觉请求，不引用图片名、帧号或分包路径。

## 当前状态

- 角色方向已冻结：圆润、活泼的奶牛猫成长型伴宠，现代 2.5D 风格。
- 微信小程序 v1.0 已接入主产品：六种意图、四个成长阶段、减少动效、场景限制、无障碍标签和按意图异步分包均已实现。
- 小程序主包只包含 `loadingGroups.core`，确定性校验值为 266,874 bytes，低于 350 KiB 预算。
- Codex v2 宠物已安装到个人宠物目录；它与小程序共享角色身份，不共享运行图集。
- 原始生成工作区、候选稿、逐帧 PNG 和构建中间件已移出产品仓库，详见 `archive-inventory.md`。

## 产品运行时

- 统一组件：`apps/wechat/miniprogram/design-system/companion/anan-companion/`
- 运行时清单：`apps/wechat/miniprogram/design-system/companion/anan-companion/runtime-manifest.json`
- 非核心视觉分包：`apps/wechat/miniprogram/packages/anan-*/`
- 确定性验收：`cd apps/wechat && npm run anan:check`

页面只允许传入 `intent`、`size`、`motion`、`context`、`visualStage` 和可选无障碍名称。无效语义请求不会借用其他姿态兜底；题目阅读、减少动效及 160–240 px 大尺寸遵守静态策略。

## 本目录保留内容

```text
anan/
├── README.md
├── archive-inventory.md
├── anan-character-system-v0.1.md
├── anan-release-v1.0.md
├── masters/                  # 获批最终母版，不含候选稿
├── specs/                    # 生产合同与接入规则
└── release-evidence/         # 小程序/Codex 清单和 QA 证据，不参与产品打包
```

## 资产原则

1. 同一角色母版分别派生小程序资产与 Codex v2 宠物资产。
2. 两端共享身份、花纹、比例、表情语言和成长阶段，不共享运行图集契约。
3. 版本号进入资源路径，发布后不得覆盖旧文件造成客户端缓存污染。
4. 错误、风险、支付、隐私和网络状态继续使用独立图标与文字，不由岸岸替代。
