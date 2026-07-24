# 岸岸 · Figma 本地交付包

本目录是“岸岸 · 小程序交付版”的本地设计交付权威，供 Fable、Antigravity 或后续开发者逐屏接入微信小程序。

## 来源与完整性

- 原始 Figma file key：`pU5sxRfG4UFeCCrnSPoaAX`
- 原文件名：`岸岸 · 小程序交付版`
- 本地 `.fig`：`source/anan-mini-program-delivery-2026-07-24.fig`
- `.fig` SHA-256：`bc18f998b879cab6aa72f47d116ad1f7b520eab226dd29a17f72f5dc5113086e`
- 可编辑导入副本 file key：`0fJdWImI2lGJQqTYjlZPN8`

已逐页激活并验证导入副本：8 个 Figma 页面、45 个顶层交付屏幕均存在。抽样节点 `31:2` 的设计上下文和截图与原文件逐字节一致。

## 目录

- `source/`：Figma `Save local copy` 得到的完整 `.fig`。
- `metadata/pages/`：8 个 Figma 页面的真实节点树。
- `contexts/`：45 个节点的 `get_design_context` 原始输出。它们是 React + Tailwind 参考，不是小程序生产代码。
- `screens/`：45 个正式 Design File 节点的 PNG 基线，不是小程序运行截图。
- `assets/by-hash/`：设计上下文引用的 184 个原始素材，按 Figma 内容标识保存。
- `variables/`：逐页 Variable 快照。当前 8 份均为空对象，因为文件没有发布 Variables。
- `tokens/`：全量节点的语义令牌、WXSS 映射和原始观察值。
- `screen-inventory.json`：页面、屏幕、校验和、上下文、截图和素材引用的完整索引。
- `route-map.json`：45 个 Figma 屏幕到正式小程序路由或路由内部状态的映射。
- `manifest.json`：交付总表、来源、覆盖率与统计。

## 页面覆盖

| Figma 页面 | 屏幕数 |
| --- | ---: |
| `00 · 核心四栏` | 4 |
| `01 · 登录与建档` | 9 |
| `02 · 今日与计划` | 4 |
| `03 · 行测练习与复盘` | 4 |
| `04 · 岗位决策` | 6 |
| `05 · 拍照搜题` | 11 |
| `06 · 申论工作台` | 3 |
| `07 · 私人资料库` | 4 |
| 合计 | 45 |

## 令牌边界

该 Figma 文件没有发布 Variables 或 Styles，所以这里不会伪造“官方变量导出”：

- `tokens/design-file.tokens.json`：从 45 个正式节点归纳的语义快照；
- `tokens/design-file.tokens.wxss`：对应的微信小程序单位与变量映射；
- `tokens/observed-values.json`：上下文中出现的所有颜色、透明色、字号、圆角、阴影和渐变及其频次；
- `variables/*.json`：Figma MCP 的逐页原始 Variable 结果。

若以后在 Figma 中建立正式 Variables collection，应以新导出的 collection/mode 数据替换语义快照，并保留迁移记录。

## 重新导出与验证

Figma Desktop 需打开原文件，并开启本地 MCP：

```sh
node apps/wechat/design/figma-delivery/scripts/export-from-local-mcp.mjs
node apps/wechat/design/figma-delivery/scripts/extract-observed-values.mjs
node apps/wechat/design/figma-delivery/scripts/validate-delivery.mjs
```

导出脚本只读取 Figma；它会依次激活 8 个页面，以处理 Figma Desktop 的惰性页面加载，然后重建元数据、上下文、截图和素材目录。

## Fable 接入规则

1. 视觉以 `screens/` 和对应 `contexts/` 为唯一依据，不自行重绘或“微调”。
2. 路由以 `miniprogram/app/navigation/routes.ts` 为唯一代码权威，具体映射见 `route-map.json`。
3. 将 React + Tailwind 上下文翻译为 WXML/WXSS/TypeScript，不把它直接塞入小程序。
4. 保留现有 controller、API、登录恢复、幂等、账号隔离和真实状态链路。
5. 同一路由的多张设计图是生产状态机的不同状态，不得拆成假页面或本地 `setTimeout` 演示。
6. 不使用 Figma Make mock、旧版页面或手写占位替换本交付包中的正式节点。
